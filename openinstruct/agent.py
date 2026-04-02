from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, replace
import difflib
import json
from queue import Queue
import shutil
import shlex
from datetime import datetime
from pathlib import Path
from threading import Lock, Thread
import time
from typing import Any, Dict, List, Optional

from .config import Settings, sanitize_session_name
from .checkpoint import CheckpointStore
from .knowledge import (
    build_compile_prompt,
    build_lint_prompt,
    build_question_prompt,
    default_query_output_path,
    init_knowledge_base,
    render_knowledge_status,
)
from .locking import WorkspaceLockManager
from .memory import BaseMemoryBackend, NullMemoryBackend, extract_memory_facts, render_memory_records
from .protocol import ProtocolError, build_system_prompt, extract_json_candidate, parse_model_response, render_tool_results
from .providers import BaseProvider, ProviderInfo, instantiate_provider, select_provider
from .session import SessionStore
from .sessions_api import ManagedSessionsAPI
from .tools import ToolError, ToolResult, WorkspaceTools
from .worktree import GitWorktree, WorktreeError, create_isolated_worktree, detect_git_repo, remove_isolated_worktree

Message = Dict[str, str]
MAX_MERGE_PREVIEW_CHARS = 4000
MAX_MERGE_FILES_PER_TASK = 6


class TerminalUI:
    def info(self, message: str) -> None:
        print(f"[info] {message}")

    def error(self, message: str) -> None:
        print(f"[error] {message}")

    def tool(self, result: ToolResult) -> None:
        status = "ok" if result.ok else "error"
        print(f"[tool:{result.tool}:{status}]")
        print(result.output)

    def assistant(self, message: str) -> None:
        print(message)

    def approval(self, action: str) -> bool:
        answer = input(f"[approve] {action}? [y/N] ").strip().lower()
        return answer in {"y", "yes"}


class SilentUI(TerminalUI):
    def info(self, message: str) -> None:
        return

    def error(self, message: str) -> None:
        return

    def tool(self, result: ToolResult) -> None:
        return

    def assistant(self, message: str) -> None:
        return

    def approval(self, action: str) -> bool:
        return False


@dataclass
class SubAgentTask:
    name: str
    prompt: str
    depends_on: List[str] = field(default_factory=list)
    write: bool = False
    write_paths: List[str] = field(default_factory=list)


@dataclass
class TaskPlan:
    summary: str
    tasks: List[SubAgentTask]


@dataclass
class BackgroundTask:
    task_id: str
    prompt: str
    status: str = "running"
    result: str = ""
    error: str = ""
    started_at: float = field(default_factory=time.time)
    completed_at: float = 0.0


@dataclass
class ManagedSessionMessage:
    message_id: str
    prompt: str
    status: str = "queued"
    result: str = ""
    error: str = ""
    queued_at: float = field(default_factory=time.time)
    started_at: float = 0.0
    completed_at: float = 0.0


@dataclass
class ManagedSession:
    session_id: str
    title: str
    parent_id: str = ""
    status: str = "idle"
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    last_result: str = ""
    last_error: str = ""
    runtime: Optional["AgentRuntime"] = None
    worker: Optional[Thread] = None
    inbox: Queue = field(default_factory=Queue)
    items: List[ManagedSessionMessage] = field(default_factory=list)


class AgentRuntime:
    def __init__(
        self,
        settings: Settings,
        provider_info: ProviderInfo,
        provider: BaseProvider,
        ui: Optional[TerminalUI] = None,
        enable_subagents: bool = True,
        lock_manager: Optional[WorkspaceLockManager] = None,
        agent_label: str = "primary",
        managed_session_id: str = "",
        memory_backend: Optional[BaseMemoryBackend] = None,
    ):
        self.ui = ui or TerminalUI()
        self.settings = settings
        self.provider_info = provider_info
        self.provider = provider
        self.enable_subagents = enable_subagents
        self.store = SessionStore(settings.home)
        self.checkpoint_store = CheckpointStore(settings.home)
        self.session_name = sanitize_session_name(settings.session or self.store.default_name())
        self.lock_manager = lock_manager or WorkspaceLockManager()
        self.agent_label = agent_label
        self.managed_session_id = managed_session_id
        self.managed_sessions: Dict[str, ManagedSession] = {}
        self.sessions_api = ManagedSessionsAPI(self)
        self.background_lock = Lock()
        self.last_merge_report: Optional[Dict[str, Any]] = None
        self.last_checkpoint_run_id: str = ""
        self.last_checkpoint_path: Optional[Path] = None
        self.last_task_checkpoints: List[Dict[str, Any]] = []
        self.memory_backend = memory_backend or NullMemoryBackend()
        self.last_memory_query: str = ""
        self.last_memory_hits: List[Any] = []
        self.last_memory_stored: List[str] = []
        self.tools = WorkspaceTools(
            settings.workdir,
            approval_callback=self.ui.approval,
            approval_policy=settings.approval_policy,
            lock_manager=self.lock_manager,
            owner_id=f"{self.agent_label}:{self.session_name}",
            ignored_roots=[settings.home],
        )
        self.messages: List[Message] = []
        self._reset_messages()

    def _memory_prompt(self) -> str:
        memory_path = self.tools.memory_path("project")
        if not memory_path.exists():
            return ""
        try:
            content = memory_path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            return ""
        if not content:
            return ""
        return (
            "\n\nWorkspace memory from .openinstruct/project.md:\n"
            "Use this as project-specific guidance, but prefer the actual files when they conflict.\n"
            f"{content[:12000]}"
        )

    def create_provider(self) -> BaseProvider:
        return instantiate_provider(self.provider_info)

    def clone_runtime(
        self,
        *,
        session_suffix: str,
        ui: Optional[TerminalUI] = None,
        enable_subagents: bool = False,
        approval_policy: Optional[str] = None,
        agent_label: Optional[str] = None,
        managed_session_id: str = "",
        workdir: Optional[Path] = None,
    ) -> "AgentRuntime":
        child_settings = replace(
            self.settings,
            session=f"{self.session_name}-{session_suffix}",
            approval_policy=approval_policy or self.settings.approval_policy,
            max_agents=1 if not enable_subagents else self.settings.max_agents,
            workdir=(workdir or self.settings.workdir).expanduser().resolve(),
        )
        child_provider = self.create_provider()
        child = AgentRuntime(
            settings=child_settings,
            provider_info=ProviderInfo(
                name=self.provider_info.name,
                base_url=self.provider_info.base_url,
                model=self.provider_info.model,
            ),
            provider=child_provider,
            ui=ui or SilentUI(),
            enable_subagents=enable_subagents,
            lock_manager=self.lock_manager,
            agent_label=agent_label or f"{self.agent_label}-{session_suffix}",
            managed_session_id=managed_session_id,
            memory_backend=self.memory_backend,
        )
        child.managed_sessions = self.managed_sessions
        child.background_lock = self.background_lock
        return child

    def _tool_manifest(self) -> str:
        manifest = self.tools.manifest()
        if self.enable_subagents:
            manifest += (
                "\n"
                '- sessions_list(): list active managed sessions and their status.'
                "\n"
                '- sessions_spawn(prompt, session_id="", write=false): start a managed session in the background and queue the initial prompt.'
                "\n"
                '- sessions_send(session_id, prompt): queue follow-up work for an existing managed session.'
                "\n"
                '- sessions_history(session_id, limit=8): inspect queued work and recent conversation history for a managed session.'
                "\n"
                '- sessions_status(session_id): inspect the current status, queue depth and last result for a managed session.'
            )
        if self.enable_subagents and self.settings.max_agents > 1:
            manifest += (
                "\n"
                '- plan_tasks(goal, write=false, max_tasks=3): break a complex goal into dependency-aware subtasks. '
                "Each planned task includes name, prompt, depends_on, write and write_paths."
                "\n"
                f'- spawn_agents(tasks, write=false, max_agents={self.settings.max_agents}): '
                "delegate independent subtasks to parallel sub-agents. "
                "Each task may be a string or an object with name, prompt, write, write_paths and depends_on. "
                "Only use it when tasks are independent and can run in parallel."
                "\n"
                f'- orchestrate(goal, write=false, max_tasks={self.settings.max_agents}, max_agents={self.settings.max_agents}): '
                "plan a task graph and execute it with sub-agents while respecting dependencies and write conflicts."
            )
        return manifest

    def _system_message(self) -> Message:
        content = build_system_prompt(self.tools.root, self._tool_manifest()) + self._memory_prompt()
        return {"role": "system", "content": content}

    def _reset_messages(self) -> None:
        self.messages = [self._system_message()]

    def _session_payload(self) -> Dict:
        return {
            "session_name": self.session_name,
            "provider": self.provider_info.name,
            "model": self.provider_info.model,
            "base_url": self.provider_info.base_url,
            "workdir": str(self.tools.root),
            "approval_policy": self.settings.approval_policy,
            "messages": self.messages,
        }

    def save_session(self, name: Optional[str] = None) -> str:
        if name:
            self.session_name = sanitize_session_name(name)
            self.tools.owner_id = f"{self.agent_label}:{self.session_name}"
        try:
            path = self.store.save(self.session_name, self._session_payload())
        except OSError as exc:
            return f"session save failed: {exc}"
        return str(path)

    def load_session(self, name: str) -> None:
        data = self.store.load(name)
        self.session_name = sanitize_session_name(name)
        self.tools.owner_id = f"{self.agent_label}:{self.session_name}"
        workdir = Path(data.get("workdir", self.tools.root)).expanduser().resolve()
        self.tools.set_root(workdir)
        self.settings.workdir = workdir
        loaded_messages = data.get("messages") or []
        if loaded_messages:
            self.messages = loaded_messages
        else:
            self._reset_messages()

    def status(self) -> str:
        return (
            f"provider={self.provider_info.name} "
            f"model={self.provider_info.model} "
            f"memory={self.memory_backend.describe()} "
            f"memory_policy={self.settings.memory_policy} "
            f"workdir={self.tools.root} "
            f"approval={self.settings.approval_policy} "
            f"max_agents={self.settings.max_agents} "
            f"task_retries={self.settings.task_retries} "
            f"session={self.session_name}"
        )

    def set_provider(self, info: ProviderInfo, provider: BaseProvider) -> None:
        self.provider_info = info
        self.provider = provider
        self.settings.provider = info.name
        self.settings.model = info.model

    def set_model(self, model: str) -> None:
        self.provider_info.model = model
        self.settings.model = model

    def set_workdir(self, path: str) -> None:
        workdir = Path(path).expanduser().resolve()
        if not workdir.is_dir():
            raise ValueError(f"Not a directory: {path}")
        self.tools.set_root(workdir)
        self.settings.workdir = workdir
        self._reset_messages()

    def set_approval_policy(self, policy: str) -> None:
        if policy not in {"ask", "auto", "deny"}:
            raise ValueError("approval policy must be ask, auto or deny")
        self.settings.approval_policy = policy
        self.tools.set_approval_policy(policy)

    def set_max_agents(self, value: int) -> None:
        if value < 1:
            raise ValueError("max_agents must be >= 1")
        self.settings.max_agents = value
        self._reset_messages()

    def set_task_retries(self, value: int) -> None:
        if value < 0:
            raise ValueError("task_retries must be >= 0")
        self.settings.task_retries = value

    def set_memory_policy(self, policy: str) -> None:
        if policy not in {"none", "selective", "all"}:
            raise ValueError("memory policy must be none, selective or all")
        self.settings.memory_policy = policy

    def recent_history(self, limit: int = 8) -> List[Message]:
        filtered = [message for message in self.messages if message["role"] != "system"]
        return filtered[-limit:]

    def _long_term_memory_prompt(self, query: str) -> str:
        if not self.memory_backend.enabled():
            return ""
        try:
            records = self.memory_backend.search(
                query,
                session_name=self.session_name,
                agent_label=self.agent_label,
                limit=5,
            )
        except Exception:  # pragma: no cover
            return ""
        self.last_memory_query = query
        self.last_memory_hits = list(records)
        memories = [record.text for record in records]
        if not memories:
            return ""
        lines = [
            "",
            "Relevant long-term memory from the configured memory backend:",
            "Use it only when relevant and never over the current workspace or user instruction.",
        ]
        lines.extend(f"- {memory}" for memory in memories[:5])
        return "\n".join(lines)

    def _store_long_term_memory(self, user_text: str, assistant_text: str) -> None:
        if not self.memory_backend.enabled():
            return
        if not user_text.strip() or not assistant_text.strip():
            return
        if self.settings.memory_policy == "none":
            self.last_memory_stored = []
            return
        try:
            if self.settings.memory_policy == "all":
                self.memory_backend.store(
                    user_text,
                    assistant_text,
                    session_name=self.session_name,
                    agent_label=self.agent_label,
                )
                self.last_memory_stored = [assistant_text.strip()]
                return
            facts = extract_memory_facts(user_text, assistant_text)
            self.last_memory_stored = list(facts)
            for fact in facts:
                self.memory_backend.store(
                    fact,
                    "",
                    session_name=self.session_name,
                    agent_label=self.agent_label,
                )
        except Exception:  # pragma: no cover
            return

    def describe_memories(self, query: str = "", limit: int = 8) -> str:
        if not self.memory_backend.enabled():
            return "(memory backend disabled)"
        clean_query = query.strip()
        try:
            if clean_query:
                records = self.memory_backend.search(
                    clean_query,
                    session_name=self.session_name,
                    agent_label=self.agent_label,
                    limit=limit,
                )
                self.last_memory_query = clean_query
                self.last_memory_hits = list(records)
                return render_memory_records(records, title=f"memories for: {clean_query}")
            records = self.memory_backend.recent(
                session_name=self.session_name,
                agent_label=self.agent_label,
                limit=limit,
            )
        except Exception as exc:  # pragma: no cover
            return f"memory inspect failed: {exc}"
        if records:
            return render_memory_records(records, title="recent memories")
        if self.last_memory_hits:
            return render_memory_records(self.last_memory_hits[:limit], title=f"last recall for: {self.last_memory_query}")
        if self.last_memory_stored:
            return "\n".join(["last stored facts:", "", *[f"- {item}" for item in self.last_memory_stored]])
        return "(no memories)"

    def _checkpoint_summary(self, entries: List[Dict[str, Any]]) -> Dict[str, int]:
        summary = {"success": 0, "failed": 0, "retrying": 0, "blocked": 0}
        for entry in entries:
            status = str(entry.get("status") or "")
            if status in summary:
                summary[status] += 1
        return summary

    def _checkpoint_payload(
        self,
        run_id: str,
        plan_summary: str,
        tasks: List[SubAgentTask],
        entries: List[Dict[str, Any]],
        max_retries: int,
        completed: bool,
    ) -> Dict[str, Any]:
        return {
            "run_id": run_id,
            "session_name": self.session_name,
            "workdir": str(self.tools.root),
            "plan_summary": plan_summary,
            "max_retries": max_retries,
            "completed": completed,
            "task_names": [task.name for task in tasks],
            "summary": self._checkpoint_summary(entries),
            "entries": entries,
        }

    def _persist_checkpoint_run(
        self,
        run_id: str,
        plan_summary: str,
        tasks: List[SubAgentTask],
        entries: List[Dict[str, Any]],
        max_retries: int,
        completed: bool,
    ) -> None:
        payload = self._checkpoint_payload(run_id, plan_summary, tasks, entries, max_retries, completed)
        self.last_checkpoint_run_id = run_id
        self.last_checkpoint_path = self.checkpoint_store.save(run_id, payload)
        self.last_task_checkpoints = list(entries)

    def _record_task_checkpoint(
        self,
        *,
        run_id: str,
        plan_summary: str,
        tasks: List[SubAgentTask],
        entries: List[Dict[str, Any]],
        max_retries: int,
        task_name: str,
        attempt: int,
        status: str,
        ok: bool,
        final: str = "",
        error: str = "",
        retry_scheduled: bool = False,
        blocked_by: Optional[List[str]] = None,
        sandbox: Optional[Dict[str, Any]] = None,
        merge: Optional[Dict[str, Any]] = None,
        completed: bool = False,
    ) -> None:
        entry = {
            "task_name": task_name,
            "attempt": attempt,
            "status": status,
            "ok": ok,
            "final": final,
            "error": error,
            "retry_scheduled": retry_scheduled,
            "blocked_by": blocked_by or [],
            "sandbox": sandbox or {},
            "merge": merge or {},
            "timestamp": datetime.now().isoformat(timespec="seconds"),
        }
        entries.append(entry)
        self._persist_checkpoint_run(run_id, plan_summary, tasks, entries, max_retries, completed)

    def describe_checkpoints(self, run_id: Optional[str] = None) -> str:
        target_run_id = (run_id or self.last_checkpoint_run_id).strip()
        if not target_run_id:
            runs = self.checkpoint_store.list()
            return "\n".join(runs[:12]) if runs else "(no checkpoints)"
        try:
            payload = self.checkpoint_store.load(target_run_id)
        except FileNotFoundError:
            return f"checkpoint run not found: {target_run_id}"
        summary = payload.get("summary") or {}
        lines = [
            f"run_id={payload.get('run_id', target_run_id)}",
            f"completed={payload.get('completed', False)}",
            f"max_retries={payload.get('max_retries', self.settings.task_retries)}",
            (
                "summary: "
                f"success={summary.get('success', 0)} "
                f"failed={summary.get('failed', 0)} "
                f"retrying={summary.get('retrying', 0)} "
                f"blocked={summary.get('blocked', 0)}"
            ),
        ]
        if payload.get("plan_summary"):
            lines.append(payload["plan_summary"])
        for entry in payload.get("entries", []):
            lines.append("")
            lines.append(
                f"[{entry.get('task_name')}] attempt={entry.get('attempt')} status={entry.get('status')}"
            )
            if entry.get("retry_scheduled"):
                lines.append("retry_scheduled=true")
            if entry.get("blocked_by"):
                lines.append(f"blocked_by={', '.join(entry['blocked_by'])}")
            if entry.get("error"):
                lines.append(f"error={entry['error']}")
            elif entry.get("final"):
                lines.append(self._preview_text(entry["final"], limit=220))
        return "\n".join(lines).strip()

    def _path_within_scope(self, rel_path: str, declared_paths: List[str]) -> bool:
        if not declared_paths:
            return True
        candidate = Path(rel_path)
        for raw_path in declared_paths:
            normalized = str(Path(raw_path).as_posix()).strip()
            if normalized in {"", "."}:
                return True
            declared = Path(normalized)
            if candidate == declared or declared in candidate.parents:
                return True
        return False

    def _task_depends_on(self, task_name: str, dependency_name: str, task_map: Dict[str, SubAgentTask]) -> bool:
        task = task_map.get(task_name)
        pending = list(task.depends_on) if task is not None else []
        visited = set()
        while pending:
            current = pending.pop()
            if current == dependency_name:
                return True
            if current in visited:
                continue
            visited.add(current)
            parent = task_map.get(current)
            if parent is not None:
                pending.extend(parent.depends_on)
        return False

    def _collapse_mutation_events(
        self, mutations: List[Dict[str, Any]]
    ) -> tuple[Dict[str, Dict[str, Any]], List[str]]:
        changed: Dict[str, Dict[str, Any]] = {}
        commands: List[str] = []
        for event in mutations:
            command = str(event.get("command") or "").strip()
            if command:
                commands.append(command)
            before_states = event.get("before") or {}
            after_states = event.get("after") or {}
            for path in event.get("paths") or []:
                before_state = dict(before_states.get(path) or {"path": path, "exists": False, "is_dir": False})
                after_state = dict(after_states.get(path) or {"path": path, "exists": False, "is_dir": False})
                record = changed.setdefault(path, {"before": before_state, "after": after_state, "actions": []})
                record["after"] = after_state
                record["actions"].append(event.get("action") or "unknown")
        return changed, commands

    def _render_path_diff(self, path: str, before: Dict[str, Any], after: Dict[str, Any]) -> str:
        before_exists = bool(before.get("exists"))
        after_exists = bool(after.get("exists"))
        if (before_exists and before.get("is_dir")) or (after_exists and after.get("is_dir")):
            if not before_exists and after_exists:
                return f"directory created: {path}"
            if before_exists and not after_exists:
                return f"directory removed: {path}"
            return f"directory changed: {path}"
        if not before_exists and not after_exists:
            return ""
        if before.get("binary") or after.get("binary"):
            return f"binary file changed: {path}"
        if before.get("too_large") or after.get("too_large"):
            return f"large file changed: {path}"
        before_lines = str(before.get("content") or "").splitlines()
        after_lines = str(after.get("content") or "").splitlines()
        diff_lines = list(
            difflib.unified_diff(
                before_lines,
                after_lines,
                fromfile=f"a/{path}",
                tofile=f"b/{path}",
                lineterm="",
            )
        )
        if not diff_lines:
            if before_exists != after_exists:
                return f"file state changed: {path}"
            return ""
        preview = "\n".join(diff_lines)
        if len(preview) > 1500:
            preview = preview[:1500].rstrip() + "\n... diff truncated"
        return preview

    def _build_merge_report(
        self,
        tasks: List[SubAgentTask],
        ordered_results: List[Dict[str, Any]],
        allow_mutations: bool,
    ) -> Optional[Dict[str, Any]]:
        if not allow_mutations:
            return None
        task_map = {task.name: task for task in tasks}
        task_reports: List[Dict[str, Any]] = []
        for item in ordered_results:
            task = task_map[item["name"]]
            mutations = item.get("mutations") or []
            changed_paths, commands = self._collapse_mutation_events(mutations)
            sorted_paths = sorted(changed_paths)
            merge_result = item.get("merge") or {}
            sandbox = item.get("sandbox") or {}
            previews = [
                self._render_path_diff(path, changed_paths[path]["before"], changed_paths[path]["after"])
                for path in sorted_paths[:MAX_MERGE_FILES_PER_TASK]
            ]
            diff_preview = "\n\n".join(part for part in previews if part).strip() or "(no file changes)"
            if len(diff_preview) > MAX_MERGE_PREVIEW_CHARS:
                diff_preview = diff_preview[:MAX_MERGE_PREVIEW_CHARS].rstrip() + "\n... truncated"
            out_of_scope = [path for path in sorted_paths if not self._path_within_scope(path, task.write_paths)]
            task_reports.append(
                {
                    "name": task.name,
                    "ok": bool(item["ok"]),
                    "mutation_count": len(mutations),
                    "changed_paths": sorted_paths,
                    "out_of_scope_paths": out_of_scope,
                    "mutating_commands": commands,
                    "diff_preview": diff_preview,
                    "sandbox": sandbox,
                    "merge_conflict_paths": merge_result.get("conflict_paths", []),
                    "merge_applied_paths": merge_result.get("applied_paths", []),
                }
            )

        handoffs: List[Dict[str, Any]] = []
        conflicts: List[Dict[str, Any]] = []
        for index, left in enumerate(task_reports):
            left_paths = set(left["changed_paths"])
            if not left_paths:
                continue
            for right in task_reports[index + 1 :]:
                overlap = sorted(left_paths.intersection(right["changed_paths"]))
                if not overlap:
                    continue
                if self._task_depends_on(right["name"], left["name"], task_map):
                    handoffs.append({"from": left["name"], "to": right["name"], "paths": overlap})
                elif self._task_depends_on(left["name"], right["name"], task_map):
                    handoffs.append({"from": right["name"], "to": left["name"], "paths": overlap})
                else:
                    conflicts.append({"tasks": [left["name"], right["name"]], "paths": overlap})

        unique_paths = sorted({path for task in task_reports for path in task["changed_paths"]})
        return {
            "task_count": len(tasks),
            "tasks_with_changes": sum(1 for task in task_reports if task["changed_paths"]),
            "changed_path_count": len(unique_paths),
            "tasks": task_reports,
            "handoffs": handoffs,
            "conflicts": conflicts,
        }

    def describe_merge_report(self, report: Optional[Dict[str, Any]]) -> str:
        if not report:
            return "(no merge report)"
        lines = [
            "Merge supervisor:",
            f"changed_tasks={report['tasks_with_changes']}/{report['task_count']}",
            f"changed_paths={report['changed_path_count']}",
            f"handoffs={len(report['handoffs'])}",
            f"conflicts={len(report['conflicts'])}",
        ]
        task_details = False
        for task in report["tasks"]:
            if not task["changed_paths"] and not task["mutating_commands"]:
                continue
            task_details = True
            lines.append("")
            lines.append(f"[merge] {task['name']}")
            changed = ", ".join(task["changed_paths"]) if task["changed_paths"] else "(no file changes)"
            lines.append(f"changed: {changed}")
            if task["out_of_scope_paths"]:
                lines.append(f"out_of_scope: {', '.join(task['out_of_scope_paths'])}")
            if task["sandbox"].get("isolated"):
                lines.append(f"sandbox: {task['sandbox'].get('reason')}")
            if task["merge_conflict_paths"]:
                lines.append(f"merge_conflicts: {', '.join(task['merge_conflict_paths'])}")
            if task["mutating_commands"]:
                lines.append(f"commands: {' | '.join(task['mutating_commands'])}")
            if task["diff_preview"] != "(no file changes)":
                lines.append(task["diff_preview"])
        if report["handoffs"]:
            lines.append("")
            lines.append("handoffs:")
            for handoff in report["handoffs"]:
                lines.append(f"- {handoff['from']} -> {handoff['to']}: {', '.join(handoff['paths'])}")
        if report["conflicts"]:
            lines.append("")
            lines.append("conflicts:")
            for conflict in report["conflicts"]:
                left, right = conflict["tasks"]
                lines.append(f"- {left} vs {right}: {', '.join(conflict['paths'])}")
        if not task_details:
            lines.append("")
            lines.append("(no file changes captured)")
        text = "\n".join(lines).strip()
        if len(text) > 8000:
            text = text[:8000].rstrip() + "\n... truncated"
        return text

    def _prepare_subagent_workspace(self, task: SubAgentTask, index: int, allow_mutations: bool) -> Dict[str, Any]:
        if not allow_mutations:
            return {"isolated": False, "workdir": self.tools.root, "reason": "read-only task"}
        label = f"{self.session_name}-{task.name}-{index}"
        repo_root = detect_git_repo(self.tools.root)
        lock_keys = [f"git-worktree:{repo_root or self.tools.root}"]
        try:
            with self.lock_manager.hold(lock_keys, f"worktree-setup:{self.agent_label}:{task.name}"):
                worktree = create_isolated_worktree(
                    self.tools.root,
                    self.settings.home,
                    label,
                    ignored_roots=[self.settings.home],
                )
        except WorktreeError as exc:
            return {"isolated": False, "workdir": self.tools.root, "reason": str(exc)}
        if worktree is None:
            return {"isolated": False, "workdir": self.tools.root, "reason": "workspace is not a git repository"}
        return {
            "isolated": True,
            "workdir": worktree.workspace_root,
            "worktree": worktree,
            "reason": "git worktree",
        }

    def _current_workspace_state(self, rel_path: str) -> Dict[str, Any]:
        target = self.tools._resolve_path(rel_path, allow_missing=True)
        return self.tools._snapshot_state(target)

    def _apply_isolated_path_state(self, rel_path: str, after_state: Dict[str, Any], source_root: Path) -> None:
        target = self.tools._resolve_path(rel_path, allow_missing=True)
        source = source_root / rel_path
        if not after_state.get("exists"):
            if target.is_dir():
                shutil.rmtree(target)
            elif target.exists():
                target.unlink()
            return
        if after_state.get("is_dir"):
            if target.exists() and not target.is_dir():
                target.unlink()
            target.mkdir(parents=True, exist_ok=True)
            return
        if not source.exists():
            raise FileNotFoundError(f"Isolated worktree is missing changed path: {rel_path}")
        if target.exists() and target.is_dir():
            shutil.rmtree(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)

    def _merge_isolated_subagent_changes(
        self,
        task: SubAgentTask,
        worktree: GitWorktree,
        mutations: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        changed_paths, _ = self._collapse_mutation_events(mutations)
        applied_paths: List[str] = []
        conflict_paths: List[str] = []
        skipped_paths: List[str] = []
        if not changed_paths:
            return {
                "ok": True,
                "applied_paths": applied_paths,
                "conflict_paths": conflict_paths,
                "skipped_paths": skipped_paths,
            }
        owner = f"merge:{self.agent_label}:{task.name}"
        with self.lock_manager.hold([self.tools._workspace_write_lock_key()], owner):
            for path in sorted(changed_paths):
                before_state = changed_paths[path]["before"]
                after_state = changed_paths[path]["after"]
                current_state = self._current_workspace_state(path)
                if current_state == after_state:
                    applied_paths.append(path)
                    continue
                if current_state != before_state:
                    conflict_paths.append(path)
                    continue
                try:
                    self._apply_isolated_path_state(path, after_state, worktree.workspace_root)
                except OSError:
                    skipped_paths.append(path)
                    continue
                applied_paths.append(path)
        return {
            "ok": not conflict_paths and not skipped_paths,
            "applied_paths": applied_paths,
            "conflict_paths": conflict_paths,
            "skipped_paths": skipped_paths,
        }

    def _preview_text(self, text: str, limit: int = 120) -> str:
        clean = " ".join(str(text).strip().split())
        if len(clean) <= limit:
            return clean
        return clean[:limit].rstrip() + "..."

    def _new_managed_session_id(self, prefix: str = "sess") -> str:
        return f"{prefix}_{int(time.time() * 1000)}"

    def _new_managed_message_id(self, session: ManagedSession) -> str:
        return f"{session.session_id}_msg_{len(session.items) + 1}"

    def _managed_session_prompt(self, session: ManagedSession) -> str:
        if session.items:
            return session.items[-1].prompt
        return session.title

    def _managed_to_background_task(self, session: ManagedSession) -> BackgroundTask:
        prompt = self._managed_session_prompt(session)
        status = session.status
        result = ""
        error = ""
        completed_at = 0.0
        if session.items:
            current = session.items[-1]
            if current.status == "completed":
                status = "completed"
                result = current.result
                completed_at = current.completed_at
            elif current.status == "failed":
                status = "failed"
                error = current.error
                completed_at = current.completed_at
            elif current.status in {"queued", "running"}:
                status = current.status
        return BackgroundTask(
            task_id=session.session_id,
            prompt=prompt,
            status=status,
            result=result or session.last_result,
            error=error or session.last_error,
            started_at=session.created_at,
            completed_at=completed_at,
        )

    def _managed_session_worker(self, session_id: str) -> None:
        while True:
            with self.background_lock:
                session = self.managed_sessions.get(session_id)
                if session is None:
                    return
                runtime = session.runtime
            if runtime is None:
                return
            item = session.inbox.get()
            with self.background_lock:
                session = self.managed_sessions.get(session_id)
                if session is None:
                    return
                session.status = "running"
                session.updated_at = time.time()
                item.status = "running"
                item.started_at = time.time()
            try:
                result = runtime.run_task(item.prompt)
                with self.background_lock:
                    session = self.managed_sessions.get(session_id)
                    if session is None:
                        return
                    session.status = "idle"
                    session.updated_at = time.time()
                    session.last_result = result
                    session.last_error = ""
                    item.status = "completed"
                    item.result = result
                    item.completed_at = time.time()
            except Exception as exc:  # pragma: no cover
                with self.background_lock:
                    session = self.managed_sessions.get(session_id)
                    if session is None:
                        return
                    session.status = "failed"
                    session.updated_at = time.time()
                    session.last_error = str(exc)
                    item.status = "failed"
                    item.error = str(exc)
                    item.completed_at = time.time()
            finally:
                session.inbox.task_done()

    def spawn_managed_session(
        self,
        prompt: str,
        *,
        session_id: Optional[str] = None,
        prefix: str = "sess",
        title: Optional[str] = None,
        approval_policy: Optional[str] = None,
    ) -> ManagedSession:
        clean_prompt = prompt.strip()
        if not clean_prompt:
            raise ValueError("Session prompt must be non-empty.")
        managed_id = sanitize_session_name(session_id or self._new_managed_session_id(prefix))
        with self.background_lock:
            if managed_id in self.managed_sessions:
                raise ValueError(f"Session already exists: {managed_id}")
        child_policy = approval_policy or ("auto" if self.settings.approval_policy == "auto" else "deny")
        child_runtime = self.clone_runtime(
            session_suffix=managed_id,
            ui=SilentUI(),
            enable_subagents=True,
            approval_policy=child_policy,
            agent_label=f"{self.agent_label}-{managed_id}",
            managed_session_id=managed_id,
        )
        session = ManagedSession(
            session_id=managed_id,
            title=title or self._preview_text(clean_prompt, limit=72),
            parent_id=self.managed_session_id,
            runtime=child_runtime,
            status="queued",
        )
        item = ManagedSessionMessage(message_id=self._new_managed_message_id(session), prompt=clean_prompt)
        session.items.append(item)
        session.inbox.put(item)
        worker = Thread(target=self._managed_session_worker, args=(managed_id,), daemon=True)
        session.worker = worker
        with self.background_lock:
            self.managed_sessions[managed_id] = session
        worker.start()
        return session

    def send_managed_session_input(self, session_id: str, prompt: str) -> ManagedSessionMessage:
        clean_prompt = prompt.strip()
        if not clean_prompt:
            raise ValueError("Session prompt must be non-empty.")
        with self.background_lock:
            session = self.managed_sessions.get(session_id)
            if session is None:
                raise ValueError(f"Unknown session: {session_id}")
            item = ManagedSessionMessage(message_id=self._new_managed_message_id(session), prompt=clean_prompt)
            session.items.append(item)
            session.status = "queued" if session.status != "running" else "running"
            session.updated_at = time.time()
            session.inbox.put(item)
            return item

    def wait_for_managed_session(self, session_id: str, timeout: float = 5.0) -> ManagedSession:
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self.background_lock:
                session = self.managed_sessions.get(session_id)
                if session is None:
                    raise ValueError(f"Unknown session: {session_id}")
                if session.status in {"idle", "failed"} and session.inbox.empty():
                    return session
            time.sleep(0.01)
        with self.background_lock:
            session = self.managed_sessions.get(session_id)
            if session is None:
                raise ValueError(f"Unknown session: {session_id}")
            return session

    def list_managed_sessions(self, prefix: Optional[str] = None) -> str:
        sessions = self.managed_sessions_snapshot(prefix=prefix)
        if not sessions:
            return "(no active sessions)"
        lines = []
        for session in sessions:
            lines.append(
                f"{session['session_id']} [{session['status']}] queued={session['queue_depth']} "
                f"parent={session['parent_id'] or '-'} {session['title']}"
            )
            if session["last_result"]:
                lines.append(f"  result: {self._preview_text(session['last_result'], limit=180)}")
            if session["last_error"]:
                lines.append(f"  error: {self._preview_text(session['last_error'], limit=180)}")
        return "\n".join(lines)

    def managed_sessions_snapshot(self, prefix: Optional[str] = None) -> List[Dict[str, Any]]:
        with self.background_lock:
            sessions = list(self.managed_sessions.values())
        if prefix is not None:
            sessions = [session for session in sessions if session.session_id.startswith(prefix)]
        snapshot: List[Dict[str, Any]] = []
        for session in sorted(sessions, key=lambda item: item.created_at):
            latest = session.items[-1] if session.items else None
            snapshot.append(
                {
                    "session_id": session.session_id,
                    "title": session.title,
                    "parent_id": session.parent_id,
                    "status": session.status,
                    "created_at": session.created_at,
                    "updated_at": session.updated_at,
                    "queue_depth": session.inbox.qsize(),
                    "item_count": len(session.items),
                    "last_result": session.last_result,
                    "last_error": session.last_error,
                    "latest_prompt": latest.prompt if latest is not None else "",
                    "latest_message_id": latest.message_id if latest is not None else "",
                    "latest_message_status": latest.status if latest is not None else "",
                }
            )
        return snapshot

    def managed_session_history(self, session_id: str, limit: int = 8) -> str:
        payload = self.managed_session_history_payload(session_id, limit=limit)
        lines = [f"{payload['session_id']} [{payload['status']}] {payload['title']}", ""]
        items = payload["queued_work"]
        if items:
            lines.append("Queued work:")
            for item in items:
                lines.append(f"- {item['message_id']} [{item['status']}] {item['prompt']}")
                if item["result"]:
                    lines.append(f"  result: {self._preview_text(item['result'], limit=220)}")
                if item["error"]:
                    lines.append(f"  error: {self._preview_text(item['error'], limit=220)}")
            lines.append("")
        messages = payload["conversation"]
        if messages:
            lines.append("Conversation:")
            for message in messages:
                lines.append(f"- {message['role']}: {self._preview_text(message['content'], limit=220)}")
        else:
            lines.append("(no conversation history)")
        return "\n".join(lines).strip()

    def managed_session_status_payload(self, session_id: str) -> Dict[str, Any]:
        with self.background_lock:
            session = self.managed_sessions.get(session_id)
            if session is None:
                raise ValueError(f"Unknown session: {session_id}")
            latest = session.items[-1] if session.items else None
            return {
                "session_id": session.session_id,
                "title": session.title,
                "parent_id": session.parent_id,
                "status": session.status,
                "created_at": session.created_at,
                "updated_at": session.updated_at,
                "queue_depth": session.inbox.qsize(),
                "item_count": len(session.items),
                "last_result": session.last_result,
                "last_error": session.last_error,
                "latest_message_id": latest.message_id if latest is not None else "",
                "latest_message_status": latest.status if latest is not None else "",
                "latest_prompt": latest.prompt if latest is not None else "",
            }

    def managed_session_status(self, session_id: str) -> str:
        payload = self.managed_session_status_payload(session_id)
        lines = [
            f"{payload['session_id']} [{payload['status']}] {payload['title']}",
            f"queued={payload['queue_depth']} items={payload['item_count']} parent={payload['parent_id'] or '-'}",
        ]
        if payload["latest_message_id"]:
            lines.append(
                f"latest={payload['latest_message_id']} [{payload['latest_message_status']}] {payload['latest_prompt']}"
            )
        if payload["last_result"]:
            lines.append(f"result={self._preview_text(payload['last_result'], limit=220)}")
        if payload["last_error"]:
            lines.append(f"error={self._preview_text(payload['last_error'], limit=220)}")
        return "\n".join(lines)

    def managed_session_history_payload(self, session_id: str, limit: int = 8) -> Dict[str, Any]:
        with self.background_lock:
            session = self.managed_sessions.get(session_id)
            if session is None:
                raise ValueError(f"Unknown session: {session_id}")
            items = list(session.items[-limit:])
            runtime = session.runtime
            messages = runtime.recent_history(limit) if runtime is not None else []
        return {
            "session_id": session.session_id,
            "title": session.title,
            "parent_id": session.parent_id,
            "status": session.status,
            "created_at": session.created_at,
            "updated_at": session.updated_at,
            "last_result": session.last_result,
            "last_error": session.last_error,
            "queued_work": [
                {
                    "message_id": item.message_id,
                    "prompt": item.prompt,
                    "status": item.status,
                    "result": item.result,
                    "error": item.error,
                    "queued_at": item.queued_at,
                    "started_at": item.started_at,
                    "completed_at": item.completed_at,
                }
                for item in items
            ],
            "conversation": list(messages),
        }

    def status_payload(self) -> Dict[str, Any]:
        return {
            "provider": self.provider_info.name,
            "model": self.provider_info.model,
            "memory_backend": self.memory_backend.describe(),
            "memory_policy": self.settings.memory_policy,
            "workdir": str(self.tools.root),
            "approval_policy": self.settings.approval_policy,
            "max_agents": self.settings.max_agents,
            "task_retries": self.settings.task_retries,
            "session_name": self.session_name,
            "managed_session_id": self.managed_session_id,
            "last_checkpoint_run_id": self.last_checkpoint_run_id,
            "last_memory_query": self.last_memory_query,
            "last_memory_stored": list(self.last_memory_stored),
            "has_merge_report": self.last_merge_report is not None,
            "managed_sessions": self.managed_sessions_snapshot(),
        }

    def _normalize_subagent_tasks(self, items: Any) -> List[SubAgentTask]:
        if not isinstance(items, list) or not items:
            raise ValueError("'tasks' must be a non-empty list.")

        normalized: List[SubAgentTask] = []
        for index, item in enumerate(items, start=1):
            if isinstance(item, str):
                prompt = item.strip()
                if not prompt:
                    raise ValueError("Sub-agent task strings must be non-empty.")
                normalized.append(SubAgentTask(name=f"task-{index}", prompt=prompt))
                continue
            if not isinstance(item, dict):
                raise ValueError("Each sub-agent task must be a string or object.")
            prompt = str(item.get("prompt") or item.get("task") or item.get("description") or "").strip()
            if not prompt:
                raise ValueError("Each sub-agent task object must include 'prompt' or 'task'.")
            name = str(item.get("name") or f"task-{index}").strip() or f"task-{index}"
            depends_on = item.get("depends_on") or []
            if not isinstance(depends_on, list):
                raise ValueError("'depends_on' must be a list when provided.")
            write_paths = item.get("write_paths") or []
            if not isinstance(write_paths, list):
                raise ValueError("'write_paths' must be a list when provided.")
            raw_write = item.get("write", False)
            write = raw_write if isinstance(raw_write, bool) else str(raw_write).strip().lower() in {
                "1",
                "true",
                "yes",
                "on",
            }
            normalized.append(
                SubAgentTask(
                    name=name,
                    prompt=prompt,
                    depends_on=[str(dep).strip() for dep in depends_on if str(dep).strip()],
                    write=write,
                    write_paths=[str(path).strip() for path in write_paths if str(path).strip()],
                )
            )
        names = [task.name for task in normalized]
        if len(names) != len(set(names)):
            raise ValueError("Sub-agent task names must be unique.")
        for task in normalized:
            unknown = [dep for dep in task.depends_on if dep not in names]
            if unknown:
                raise ValueError(f"Task '{task.name}' references unknown dependencies: {', '.join(unknown)}")
        return normalized

    def _task_lock_keys(self, task: SubAgentTask) -> List[str]:
        if not task.write:
            return []
        if not task.write_paths:
            return [self.tools._workspace_write_lock_key()]
        keys: List[str] = []
        for path in task.write_paths:
            try:
                target = self.tools._resolve_path(path, allow_missing=True)
            except ToolError:
                return [self.tools._workspace_write_lock_key()]
            keys.append(self.tools._lock_key_for_path(target))
        return sorted(set(keys))

    def _select_ready_batch(self, ready: List[SubAgentTask], limit: int) -> List[SubAgentTask]:
        batch: List[SubAgentTask] = []
        held_keys = set()
        for task in ready:
            task_keys = set(self._task_lock_keys(task))
            if task_keys and held_keys.intersection(task_keys):
                continue
            batch.append(task)
            held_keys.update(task_keys)
            if len(batch) >= limit:
                break
        if not batch and ready:
            batch.append(ready[0])
        return batch

    def _dependency_context(self, task: SubAgentTask, results: Dict[str, Dict[str, Any]]) -> str:
        if not task.depends_on:
            return ""
        lines = ["Dependency context:"]
        for dependency in task.depends_on:
            item = results[dependency]
            lines.append(f"- {dependency}: {item['final']}")
        return "\n".join(lines)

    def _run_subagent_task(
        self,
        task: SubAgentTask,
        allow_mutations: bool,
        index: int,
        dependency_context: str = "",
    ) -> Dict[str, Any]:
        child_policy = "auto" if allow_mutations else "deny"
        workspace = self._prepare_subagent_workspace(task, index, allow_mutations)
        child_runtime = self.clone_runtime(
            session_suffix=f"subagent-{index}",
            ui=SilentUI(),
            enable_subagents=False,
            approval_policy=child_policy,
            agent_label=f"{self.agent_label}-subagent-{index}",
            workdir=workspace["workdir"],
        )
        mode = (
            "You may modify files and run commands inside the workspace if needed."
            if allow_mutations
            else "You must stay read-only. Do not modify files and do not run mutating shell commands."
        )
        write_scope = (
            f"Expected write scope: {', '.join(task.write_paths)}."
            if task.write_paths
            else "Expected write scope: none declared."
        )
        child_prompt = (
            f"You are handling a delegated subtask named '{task.name}'.\n"
            f"{mode}\n"
            f"{write_scope}\n"
            "Focus only on this subtask and report the outcome concisely.\n\n"
            f"{dependency_context}\n\n"
            f"Subtask:\n{task.prompt}"
        )
        try:
            final = child_runtime.run_task(child_prompt)
            mutations = child_runtime.tools.drain_mutation_log()
            merge_result = None
            ok = True
            if workspace["isolated"]:
                merge_result = self._merge_isolated_subagent_changes(task, workspace["worktree"], mutations)
                ok = bool(merge_result["ok"])
                if not ok and merge_result["conflict_paths"]:
                    final += (
                        "\n\nMerge back to the primary workspace reported conflicts on: "
                        + ", ".join(merge_result["conflict_paths"])
                    )
            return {
                "name": task.name,
                "ok": ok,
                "final": final,
                "mutations": mutations,
                "sandbox": {
                    "isolated": bool(workspace["isolated"]),
                    "reason": workspace.get("reason", ""),
                    "worktree_path": str(workspace["worktree"].worktree_root) if workspace.get("worktree") else "",
                },
                "merge": merge_result,
            }
        finally:
            if workspace.get("worktree") is not None:
                worktree = workspace["worktree"]
                lock_keys = [f"git-worktree:{worktree.repo_root}"]
                with self.lock_manager.hold(lock_keys, f"worktree-cleanup:{self.agent_label}:{task.name}"):
                    remove_isolated_worktree(worktree)

    def _parse_task_plan(self, raw: str, max_tasks: int) -> TaskPlan:
        payload = json.loads(extract_json_candidate(raw))
        if not isinstance(payload, dict):
            raise ValueError("Planner response must be a JSON object.")
        summary = str(payload.get("summary") or "").strip()
        tasks = self._normalize_subagent_tasks(payload.get("tasks"))
        if len(tasks) > max_tasks:
            raise ValueError(f"Planner returned {len(tasks)} tasks, but the limit is {max_tasks}.")
        return TaskPlan(summary=summary or "Planned subtasks", tasks=tasks)

    def _plan_prompt(self, goal: str, allow_mutations: bool, max_tasks: int) -> List[Message]:
        write_mode = "allowed" if allow_mutations else "not allowed"
        top_level = self.tools.list_dir(".", recursive=False, limit=60).output
        system = (
            "You are the planning agent for a local coding CLI.\n"
            "Break the goal into a dependency-aware task graph for parallel sub-agents.\n"
            "Return exactly one JSON object inside a ```json fenced block.\n"
            "Schema:\n"
            "{\n"
            '  "summary": "short plan summary",\n'
            '  "tasks": [\n'
            '    {"name": "task-name", "prompt": "what to do", "depends_on": [], "write": false, "write_paths": []}\n'
            "  ]\n"
            "}\n"
            f"Rules:\n- At most {max_tasks} tasks.\n"
            "- Use independent read-only tasks when possible.\n"
            "- Add depends_on when one task should wait for another.\n"
            "- If two tasks may modify the same files, either serialize them with depends_on or declare overlapping write_paths.\n"
            "- The executor audits actual file changes, so keep write_paths precise.\n"
            "- write_paths must be relative workspace paths or empty.\n"
            f"- Mutations are {write_mode} for this orchestration.\n"
            "- Do not include markdown outside the JSON block."
        )
        user = (
            f"Workspace root: {self.tools.root}\n"
            f"Top-level entries:\n{top_level}\n\n"
            f"Goal:\n{goal}"
        )
        return [{"role": "system", "content": system}, {"role": "user", "content": user}]

    def create_task_plan(self, goal: str, allow_mutations: bool = False, max_tasks: Optional[int] = None) -> TaskPlan:
        task_limit = int(max_tasks or self.settings.max_agents)
        messages = self._plan_prompt(goal, allow_mutations, task_limit)
        last_error = "planner failed"
        for _ in range(2):
            raw = self.provider.chat(messages, self.provider_info.model, self.settings.temperature)
            try:
                return self._parse_task_plan(raw, task_limit)
            except (ValueError, json.JSONDecodeError, ProtocolError) as exc:
                last_error = str(exc)
                messages.append({"role": "assistant", "content": raw})
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            f"Your previous plan was invalid: {exc}. "
                            "Reply again with a single valid JSON object following the schema exactly."
                        ),
                    }
                )
        raise ValueError(last_error)

    def describe_task_plan(self, plan: TaskPlan) -> str:
        lines = [plan.summary, ""]
        for index, task in enumerate(plan.tasks, start=1):
            deps = ", ".join(task.depends_on) if task.depends_on else "-"
            writes = ", ".join(task.write_paths) if task.write_paths else "-"
            lines.append(f"[{index}] {task.name}")
            lines.append(f"  depends_on: {deps}")
            lines.append(f"  write: {task.write}")
            lines.append(f"  write_paths: {writes}")
            lines.append(f"  prompt: {task.prompt}")
            lines.append("")
        return "\n".join(lines).strip()

    def plan_goal(self, goal: str, allow_mutations: bool = False, max_tasks: Optional[int] = None) -> ToolResult:
        try:
            plan = self.create_task_plan(goal, allow_mutations=allow_mutations, max_tasks=max_tasks)
        except ValueError as exc:
            return ToolResult(tool="plan_tasks", ok=False, output=str(exc))
        return ToolResult(
            tool="plan_tasks",
            ok=True,
            output=self.describe_task_plan(plan),
            metadata={
                "summary": plan.summary,
                "tasks": [
                    {
                        "name": task.name,
                        "prompt": task.prompt,
                        "depends_on": task.depends_on,
                        "write": task.write,
                        "write_paths": task.write_paths,
                    }
                    for task in plan.tasks
                ],
            },
        )

    def execute_task_graph(
        self,
        tasks: List[SubAgentTask],
        allow_mutations: bool = False,
        max_agents: Optional[int] = None,
        plan_summary: str = "",
    ) -> ToolResult:
        if not self.enable_subagents:
            return ToolResult(tool="spawn_agents", ok=False, output="Sub-agents are disabled in this runtime.")

        try:
            worker_limit = int(max_agents or self.settings.max_agents)
        except (TypeError, ValueError):
            return ToolResult(tool="spawn_agents", ok=False, output="max_agents must be an integer")
        if worker_limit < 1:
            return ToolResult(tool="spawn_agents", ok=False, output="max_agents must be >= 1")
        retry_limit = max(0, int(self.settings.task_retries))
        worker_count = min(worker_limit, self.settings.max_agents, len(tasks))
        pending = {task.name: task for task in tasks}
        results: Dict[str, Dict[str, Any]] = {}
        ordered_results: List[Dict[str, Any]] = []
        checkpoint_entries: List[Dict[str, Any]] = []
        task_attempts = {task.name: 0 for task in tasks}
        subagent_index = 0
        self.last_merge_report = None
        checkpoint_run_id = self.checkpoint_store.new_run_id(prefix=f"{self.session_name}-dag")
        self._persist_checkpoint_run(
            checkpoint_run_id,
            plan_summary,
            tasks,
            checkpoint_entries,
            retry_limit,
            completed=False,
        )

        while pending:
            blocked = [
                task
                for task in tasks
                if task.name in pending and any(dep in results and not results[dep]["ok"] for dep in task.depends_on)
            ]
            for task in blocked:
                failed_deps = [dep for dep in task.depends_on if dep in results and not results[dep]["ok"]]
                item = {
                    "name": task.name,
                    "ok": False,
                    "final": "blocked by failed dependencies: " + ", ".join(failed_deps),
                    "mutations": [],
                    "attempts": task_attempts[task.name],
                    "status": "blocked",
                }
                results[task.name] = item
                ordered_results.append(item)
                pending.pop(task.name, None)
                self._record_task_checkpoint(
                    run_id=checkpoint_run_id,
                    plan_summary=plan_summary,
                    tasks=tasks,
                    entries=checkpoint_entries,
                    max_retries=retry_limit,
                    task_name=task.name,
                    attempt=task_attempts[task.name],
                    status="blocked",
                    ok=False,
                    final=item["final"],
                    blocked_by=failed_deps,
                    completed=False,
                )
            if not pending:
                break
            ready = [
                task
                for task in tasks
                if task.name in pending and all(dependency in results and results[dependency]["ok"] for dependency in task.depends_on)
            ]
            if not ready:
                unresolved = ", ".join(sorted(pending))
                self._persist_checkpoint_run(
                    checkpoint_run_id,
                    plan_summary,
                    tasks,
                    checkpoint_entries,
                    retry_limit,
                    completed=False,
                )
                return ToolResult(
                    tool="spawn_agents",
                    ok=False,
                    output=f"Task graph is blocked by cyclic or missing dependencies: {unresolved}",
                )

            batch = self._select_ready_batch(ready, worker_count)
            with ThreadPoolExecutor(max_workers=min(worker_count, len(batch))) as pool:
                future_map = {}
                for task in batch:
                    subagent_index += 1
                    task_attempts[task.name] += 1
                    dependency_context = self._dependency_context(task, results)
                    future = pool.submit(
                        self._run_subagent_task,
                        task,
                        allow_mutations and task.write,
                        subagent_index,
                        dependency_context,
                    )
                    future_map[future] = (task, task_attempts[task.name])

                for future in as_completed(future_map):
                    task, attempt = future_map[future]
                    try:
                        item = future.result()
                    except Exception as exc:  # pragma: no cover
                        item = {
                            "name": task.name,
                            "ok": False,
                            "final": f"sub-agent error: {exc}",
                            "mutations": [],
                        }
                    item["attempts"] = attempt
                    if item["ok"]:
                        item["status"] = "ok"
                        results[task.name] = item
                        ordered_results.append(item)
                        pending.pop(task.name, None)
                        self._record_task_checkpoint(
                            run_id=checkpoint_run_id,
                            plan_summary=plan_summary,
                            tasks=tasks,
                            entries=checkpoint_entries,
                            max_retries=retry_limit,
                            task_name=task.name,
                            attempt=attempt,
                            status="success",
                            ok=True,
                            final=item["final"],
                            sandbox=item.get("sandbox"),
                            merge=item.get("merge"),
                            completed=False,
                        )
                        continue

                    error_text = str(item.get("final") or item.get("error") or "").strip()
                    if attempt <= retry_limit:
                        self._record_task_checkpoint(
                            run_id=checkpoint_run_id,
                            plan_summary=plan_summary,
                            tasks=tasks,
                            entries=checkpoint_entries,
                            max_retries=retry_limit,
                            task_name=task.name,
                            attempt=attempt,
                            status="retrying",
                            ok=False,
                            final=item.get("final", ""),
                            error=error_text,
                            retry_scheduled=True,
                            sandbox=item.get("sandbox"),
                            merge=item.get("merge"),
                            completed=False,
                        )
                        continue

                    item["status"] = "error"
                    results[task.name] = item
                    ordered_results.append(item)
                    pending.pop(task.name, None)
                    self._record_task_checkpoint(
                        run_id=checkpoint_run_id,
                        plan_summary=plan_summary,
                        tasks=tasks,
                        entries=checkpoint_entries,
                        max_retries=retry_limit,
                        task_name=task.name,
                        attempt=attempt,
                        status="failed",
                        ok=False,
                        final=item.get("final", ""),
                        error=error_text,
                        sandbox=item.get("sandbox"),
                        merge=item.get("merge"),
                        completed=False,
                    )

        lines = []
        if plan_summary:
            lines.append(plan_summary)
            lines.append("")
        lines.append(f"Executed {len(tasks)} planned tasks with parallelism={worker_count}.")
        lines.append(f"Mutation mode: {'enabled' if allow_mutations else 'disabled'}")
        lines.append(f"Task retries: {retry_limit}")
        lines.append(f"Checkpoint run: {checkpoint_run_id}")
        lines.append("")
        for index, item in enumerate(ordered_results, start=1):
            status = item.get("status") or ("ok" if item["ok"] else "error")
            lines.append(f"[{index}] {item['name']} [{status}] attempts={item.get('attempts', 1)}")
            lines.append(str(item["final"]))
            lines.append("")
        merge_report = self._build_merge_report(tasks, ordered_results, allow_mutations)
        self.last_merge_report = merge_report
        if merge_report:
            lines.append(self.describe_merge_report(merge_report))
        output = "\n".join(lines).strip()
        self._persist_checkpoint_run(
            checkpoint_run_id,
            plan_summary,
            tasks,
            checkpoint_entries,
            retry_limit,
            completed=True,
        )
        metadata = {
            "results": ordered_results,
            "parallelism": worker_count,
            "checkpoint_run_id": checkpoint_run_id,
            "checkpoint_path": str(self.last_checkpoint_path) if self.last_checkpoint_path else "",
            "checkpoints": checkpoint_entries,
        }
        if merge_report:
            metadata["merge_report"] = merge_report
        return ToolResult(
            tool="spawn_agents",
            ok=all(bool(item["ok"]) for item in ordered_results),
            output=output,
            metadata=metadata,
        )

    def run_parallel_tasks(
        self,
        tasks: Any,
        allow_mutations: bool = False,
        max_agents: Optional[int] = None,
    ) -> ToolResult:
        try:
            normalized = self._normalize_subagent_tasks(tasks)
        except ValueError as exc:
            return ToolResult(tool="spawn_agents", ok=False, output=str(exc))

        if allow_mutations and self.settings.approval_policy != "auto":
            return ToolResult(
                tool="spawn_agents",
                ok=False,
                output="Parallel agents may mutate the workspace only when approval policy is 'auto'.",
            )
        return self.execute_task_graph(normalized, allow_mutations=allow_mutations, max_agents=max_agents)

    def orchestrate_goal(
        self,
        goal: str,
        allow_mutations: bool = False,
        max_tasks: Optional[int] = None,
        max_agents: Optional[int] = None,
    ) -> ToolResult:
        if allow_mutations and self.settings.approval_policy != "auto":
            return ToolResult(
                tool="orchestrate",
                ok=False,
                output="Planner-driven mutations require approval policy 'auto'.",
            )
        try:
            plan = self.create_task_plan(goal, allow_mutations=allow_mutations, max_tasks=max_tasks)
        except ValueError as exc:
            return ToolResult(tool="orchestrate", ok=False, output=str(exc))
        result = self.execute_task_graph(
            plan.tasks,
            allow_mutations=allow_mutations,
            max_agents=max_agents,
            plan_summary=plan.summary,
        )
        return ToolResult(tool="orchestrate", ok=result.ok, output=result.output, metadata=result.metadata)

    def locks_status(self) -> str:
        snapshot = self.lock_manager.snapshot()
        if not snapshot:
            return "(no active locks)"
        return "\n".join(f"{key} -> {owner}" for key, owner in sorted(snapshot.items()))

    def start_background_task(self, prompt: str) -> BackgroundTask:
        session = self.spawn_managed_session(
            prompt,
            prefix="bg",
            approval_policy="auto" if self.settings.approval_policy == "auto" else "deny",
        )
        return self._managed_to_background_task(session)

    def list_background_tasks(self) -> str:
        listing = self.list_managed_sessions(prefix="bg_")
        if listing == "(no active sessions)":
            return "(no background tasks)"
        return listing

    def wait_for_background_task(self, task_id: str, timeout: float = 5.0) -> BackgroundTask:
        session = self.wait_for_managed_session(task_id, timeout=timeout)
        return self._managed_to_background_task(session)

    def run_action(self, tool_name: str, args: Dict[str, Any]) -> ToolResult:
        if tool_name == "sessions_list":
            payload = self.sessions_api.list(prefix=str(args.get("prefix") or "").strip() or None)
            sessions = payload["sessions"]
            text = self.list_managed_sessions(prefix=str(args.get("prefix") or "").strip() or None)
            return ToolResult(tool="sessions_list", ok=True, output=text, metadata=payload)
        if tool_name == "sessions_spawn":
            raw_write = args.get("write", False)
            allow_mutations = raw_write if isinstance(raw_write, bool) else str(raw_write).strip().lower() in {
                "1",
                "true",
                "yes",
                "on",
            }
            prompt = str(args.get("prompt") or args.get("goal") or "").strip()
            if not prompt:
                return ToolResult(tool="sessions_spawn", ok=False, output="'prompt' is required.")
            try:
                payload = self.sessions_api.spawn(
                    prompt,
                    session_id=args.get("session_id"),
                    write=allow_mutations,
                    title=str(args.get("title") or "").strip(),
                )
            except ValueError as exc:
                return ToolResult(tool="sessions_spawn", ok=False, output=str(exc))
            return ToolResult(
                tool="sessions_spawn",
                ok=True,
                output=f"spawned {payload['session_id']}",
                metadata=payload,
            )
        if tool_name == "sessions_send":
            session_id = str(args.get("session_id") or "").strip()
            prompt = str(args.get("prompt") or args.get("message") or "").strip()
            if not session_id:
                return ToolResult(tool="sessions_send", ok=False, output="'session_id' is required.")
            if not prompt:
                return ToolResult(tool="sessions_send", ok=False, output="'prompt' is required.")
            try:
                payload = self.sessions_api.send(session_id, prompt)
            except ValueError as exc:
                return ToolResult(tool="sessions_send", ok=False, output=str(exc))
            return ToolResult(
                tool="sessions_send",
                ok=True,
                output=f"queued {payload['message_id']} for {session_id}",
                metadata=payload,
            )
        if tool_name == "sessions_history":
            session_id = str(args.get("session_id") or "").strip()
            if not session_id:
                return ToolResult(tool="sessions_history", ok=False, output="'session_id' is required.")
            limit = int(args.get("limit") or 8)
            try:
                payload = self.sessions_api.history(session_id, limit=limit)
            except ValueError as exc:
                return ToolResult(tool="sessions_history", ok=False, output=str(exc))
            return ToolResult(tool="sessions_history", ok=True, output=self.managed_session_history(session_id, limit=limit), metadata=payload)
        if tool_name == "sessions_status":
            session_id = str(args.get("session_id") or "").strip()
            if not session_id:
                return ToolResult(tool="sessions_status", ok=False, output="'session_id' is required.")
            try:
                payload = self.sessions_api.status(session_id)
            except ValueError as exc:
                return ToolResult(tool="sessions_status", ok=False, output=str(exc))
            return ToolResult(tool="sessions_status", ok=True, output=self.managed_session_status(session_id), metadata=payload)
        if tool_name == "plan_tasks":
            raw_write = args.get("write", False)
            allow_mutations = raw_write if isinstance(raw_write, bool) else str(raw_write).strip().lower() in {
                "1",
                "true",
                "yes",
                "on",
            }
            goal = str(args.get("goal") or args.get("prompt") or "").strip()
            if not goal:
                return ToolResult(tool="plan_tasks", ok=False, output="'goal' is required.")
            return self.plan_goal(goal=goal, allow_mutations=allow_mutations, max_tasks=args.get("max_tasks"))
        if tool_name == "spawn_agents":
            raw_write = args.get("write", False)
            allow_mutations = raw_write if isinstance(raw_write, bool) else str(raw_write).strip().lower() in {
                "1",
                "true",
                "yes",
                "on",
            }
            return self.run_parallel_tasks(
                tasks=args.get("tasks"),
                allow_mutations=allow_mutations,
                max_agents=args.get("max_agents"),
            )
        if tool_name == "orchestrate":
            raw_write = args.get("write", False)
            allow_mutations = raw_write if isinstance(raw_write, bool) else str(raw_write).strip().lower() in {
                "1",
                "true",
                "yes",
                "on",
            }
            goal = str(args.get("goal") or args.get("prompt") or "").strip()
            if not goal:
                return ToolResult(tool="orchestrate", ok=False, output="'goal' is required.")
            return self.orchestrate_goal(
                goal=goal,
                allow_mutations=allow_mutations,
                max_tasks=args.get("max_tasks"),
                max_agents=args.get("max_agents"),
            )
        return self.tools.run(tool_name, args)

    def run_task(self, text: str) -> str:
        original_text = text
        memory_suffix = self._long_term_memory_prompt(text)
        user_text = text + memory_suffix if memory_suffix else text
        self.messages.append({"role": "user", "content": user_text})
        repairs = 0
        for _ in range(self.settings.max_steps):
            raw = self.provider.chat(self.messages, self.provider_info.model, self.settings.temperature)
            self.messages.append({"role": "assistant", "content": raw})
            try:
                reply = parse_model_response(raw)
            except ProtocolError as exc:
                if repairs >= 1:
                    self.save_session()
                    return raw.strip()
                repairs += 1
                repair_message = (
                    "Your last response broke the JSON protocol. "
                    f"Error: {exc}. Reply again with a single valid JSON object."
                )
                self.messages.append({"role": "user", "content": repair_message})
                continue

            if reply.summary:
                self.ui.info(reply.summary)

            if reply.actions:
                results = []
                for action in reply.actions:
                    result = self.run_action(action.tool, action.args)
                    self.ui.tool(result)
                    results.append(result.as_prompt_payload())
                self.messages.append({"role": "user", "content": render_tool_results(results)})
                continue

            final = reply.final.strip() or reply.summary.strip()
            if final:
                self._store_long_term_memory(original_text, final)
                self.save_session()
                return final

            self.messages.append(
                {
                    "role": "user",
                    "content": "You returned no actions and no final answer. Reply again using the JSON protocol.",
                }
            )

        self.save_session()
        return f"Stopped after reaching max steps ({self.settings.max_steps})."

    def init_project_memory(self) -> str:
        top_level = self.tools.list_dir(".", recursive=False, limit=60).output
        readme_text = "(no README found)"
        for candidate in ("README.md", "README.txt", "readme.md"):
            file_path = self.tools.root / candidate
            if file_path.exists() and file_path.is_file():
                readme_text = file_path.read_text(encoding="utf-8", errors="replace")[:5000].strip() or "(empty README)"
                break

        content = "\n".join(
            [
                "# Project Memory",
                "",
                f"Workspace: {self.tools.root}",
                f"Initialized: {datetime.now().isoformat(timespec='seconds')}",
                "",
                "Top-level files and directories:",
                top_level,
                "",
                "README excerpt:",
                readme_text,
                "",
                "Notes:",
                "- Add architecture notes, key commands, conventions, and gotchas here.",
                "- OpenInstruct injects this file into new/reset sessions automatically.",
            ]
        )
        result = self.tools.write_memory(content, name="project")
        self._reset_messages()
        return result.output

    def compact_history(self, keep_last: int = 8) -> str:
        if keep_last < 1:
            raise ValueError("keep_last must be >= 1")
        recent = self.recent_history(keep_last)
        self.messages = [self._system_message(), *recent]
        return f"history compacted to {len(recent)} messages"

    def init_knowledge_base(self, name: str = "") -> str:
        payload = init_knowledge_base(self.tools.root, name=name)
        self._reset_messages()
        return (
            "knowledge base initialized\n"
            f"root={payload['root']}\n"
            f"config={payload['config_path']}\n"
            f"raw={payload['raw_dir']}\n"
            f"wiki={payload['wiki_dir']}\n"
            f"outputs={payload['outputs_dir']}"
        )

    def knowledge_status(self) -> str:
        return render_knowledge_status(self.tools.root)

    def run_knowledge_compile(self, scope: str = "") -> str:
        return self.run_task(build_compile_prompt(self.tools.root, scope=scope))

    def run_knowledge_ask(self, question: str, output_path: str = "", output_format: str = "markdown") -> str:
        if output_path:
            target = (self.tools.root / output_path).expanduser().resolve()
            try:
                target.relative_to(self.tools.root)
            except ValueError as exc:
                raise ValueError("Knowledge outputs must stay inside the workspace.") from exc
        else:
            target = default_query_output_path(self.tools.root, question, output_format=output_format)
        prompt = build_question_prompt(self.tools.root, question, output_path=target, output_format=output_format)
        return self.run_task(prompt)

    def run_knowledge_lint(self, fix: bool = False) -> str:
        return self.run_task(build_lint_prompt(self.tools.root, fix=fix))

    def print_help(self) -> None:
        self.ui.assistant(
            "\n".join(
                [
                    "/help",
                    "/status",
                    "/models",
                    "/provider <auto|ollama|lmstudio>",
                    "/model <name>",
                    "/pwd",
                    "/cd <path>",
                    "/approval <ask|auto|deny>",
                    "/memory-policy <none|selective|all>",
                    "/agents [count]",
                    "/retries [count]",
                    "/parallel <task1 || task2 || task3>",
                    "/plan <goal>",
                    "/delegate <goal>",
                    "/session-spawn <goal>",
                    "/session-send <session_id> <goal>",
                    "/session-status <session_id>",
                    "/session-history <session_id> [limit]",
                    "/background <goal>",
                    "/backgrounds",
                    "/waitbg <task_id>",
                    "/locks",
                    "/merge",
                    "/checkpoints [run_id]",
                    "/history [limit]",
                    "/compact [limit]",
                    "/init",
                    "/memory",
                    "/memories [query]",
                    "/kb-init [name]",
                    "/kb-status",
                    "/kb-compile [scope]",
                    "/kb-ask <question>",
                    "/kb-slide <question>",
                    "/kb-lint [fix]",
                    "/diff [path]",
                    "/review",
                    "/sessions",
                    "/saved-sessions",
                    "/save [name]",
                    "/load <name>",
                    "/reset",
                    "/run <shell-command>",
                    "/exit",
                ]
            )
        )

    def handle_command(self, line: str) -> Optional[str]:
        parts = shlex.split(line)
        if not parts:
            return None

        command = parts[0]
        args = parts[1:]

        if command == "/help":
            self.print_help()
            return None
        if command == "/status":
            self.ui.assistant(self.status())
            return None
        if command == "/models":
            models = self.provider.list_models()
            self.ui.assistant("\n".join(models) if models else "(no models)")
            return None
        if command == "/provider":
            if not args:
                self.ui.assistant(self.provider_info.name)
                return None
            info = select_provider(
                preference=args[0],
                model=self.provider_info.model,
                ollama_base_url=self.settings.ollama_base_url,
                lmstudio_base_url=self.settings.lmstudio_base_url,
            )
            provider = instantiate_provider(info)
            self.set_provider(info, provider)
            self.ui.assistant(f"provider={self.provider_info.name} model={self.provider_info.model}")
            return None
        if command == "/model":
            if not args:
                self.ui.assistant(self.provider_info.model)
                return None
            model = self.provider.resolve_model(args[0])
            self.set_model(model)
            self.ui.assistant(f"model={self.provider_info.model}")
            return None
        if command == "/pwd":
            self.ui.assistant(str(self.tools.root))
            return None
        if command == "/cd":
            if not args:
                raise ValueError("Usage: /cd <path>")
            self.set_workdir(args[0])
            self.ui.assistant(f"workdir={self.tools.root}")
            return None
        if command == "/approval":
            if not args:
                self.ui.assistant(self.settings.approval_policy)
                return None
            self.set_approval_policy(args[0])
            self.ui.assistant(f"approval={self.settings.approval_policy}")
            return None
        if command == "/memory-policy":
            if not args:
                self.ui.assistant(self.settings.memory_policy)
                return None
            self.set_memory_policy(args[0])
            self.ui.assistant(f"memory_policy={self.settings.memory_policy}")
            return None
        if command == "/agents":
            if not args:
                self.ui.assistant(str(self.settings.max_agents))
                return None
            self.set_max_agents(int(args[0]))
            self.ui.assistant(f"max_agents={self.settings.max_agents}")
            return None
        if command == "/retries":
            if not args:
                self.ui.assistant(str(self.settings.task_retries))
                return None
            self.set_task_retries(int(args[0]))
            self.ui.assistant(f"task_retries={self.settings.task_retries}")
            return None
        if command == "/parallel":
            task_blob = line[len("/parallel") :].strip()
            if not task_blob:
                raise ValueError("Usage: /parallel <task1 || task2 || task3>")
            tasks = [part.strip() for part in task_blob.split("||") if part.strip()]
            if len(tasks) < 2:
                raise ValueError("Provide at least two tasks separated by '||'.")
            allow_mutations = self.settings.approval_policy == "auto"
            result = self.run_parallel_tasks(tasks=tasks, allow_mutations=allow_mutations)
            self.ui.tool(result)
            return None
        if command == "/plan":
            goal = line[len("/plan") :].strip()
            if not goal:
                raise ValueError("Usage: /plan <goal>")
            result = self.plan_goal(goal=goal, allow_mutations=self.settings.approval_policy == "auto")
            self.ui.tool(result)
            return None
        if command == "/delegate":
            goal = line[len("/delegate") :].strip()
            if not goal:
                raise ValueError("Usage: /delegate <goal>")
            result = self.orchestrate_goal(
                goal=goal,
                allow_mutations=self.settings.approval_policy == "auto",
                max_tasks=self.settings.max_agents,
                max_agents=self.settings.max_agents,
            )
            self.ui.tool(result)
            return None
        if command == "/session-spawn":
            goal = line[len("/session-spawn") :].strip()
            if not goal:
                raise ValueError("Usage: /session-spawn <goal>")
            session = self.spawn_managed_session(
                goal,
                approval_policy="auto" if self.settings.approval_policy == "auto" else "deny",
            )
            self.ui.assistant(f"session started: {session.session_id}")
            return None
        if command == "/session-send":
            if len(args) < 2:
                raise ValueError("Usage: /session-send <session_id> <goal>")
            item = self.send_managed_session_input(args[0], " ".join(args[1:]))
            self.ui.assistant(f"queued {item.message_id} for {args[0]}")
            return None
        if command == "/session-status":
            if not args:
                raise ValueError("Usage: /session-status <session_id>")
            self.ui.assistant(self.managed_session_status(args[0]))
            return None
        if command == "/session-history":
            if not args:
                raise ValueError("Usage: /session-history <session_id> [limit]")
            limit = int(args[1]) if len(args) > 1 else 8
            self.ui.assistant(self.managed_session_history(args[0], limit=limit))
            return None
        if command == "/background":
            goal = line[len("/background") :].strip()
            if not goal:
                raise ValueError("Usage: /background <goal>")
            task = self.start_background_task(goal)
            self.ui.assistant(f"background task started: {task.task_id}")
            return None
        if command == "/backgrounds":
            self.ui.assistant(self.list_background_tasks())
            return None
        if command == "/waitbg":
            if not args:
                raise ValueError("Usage: /waitbg <task_id>")
            task = self.wait_for_background_task(args[0])
            if task.status == "completed":
                self.ui.assistant(task.result or "(empty result)")
            elif task.status == "failed":
                self.ui.error(task.error or "background task failed")
            else:
                self.ui.assistant(f"{task.task_id} still running")
            return None
        if command == "/locks":
            self.ui.assistant(self.locks_status())
            return None
        if command == "/merge":
            self.ui.assistant(self.describe_merge_report(self.last_merge_report))
            return None
        if command == "/checkpoints":
            run_id = args[0] if args else None
            self.ui.assistant(self.describe_checkpoints(run_id))
            return None
        if command == "/history":
            limit = int(args[0]) if args else 8
            history = self.recent_history(limit)
            if not history:
                self.ui.assistant("(empty)")
                return None
            lines = [f"{item['role']}: {item['content']}" for item in history]
            self.ui.assistant("\n\n".join(lines))
            return None
        if command == "/compact":
            keep_last = int(args[0]) if args else 8
            self.ui.assistant(self.compact_history(keep_last))
            return None
        if command == "/init":
            self.ui.assistant(self.init_project_memory())
            return None
        if command == "/memory":
            result = self.tools.read_memory("project")
            self.ui.tool(result)
            return None
        if command == "/memories":
            query = line[len("/memories") :].strip()
            self.ui.assistant(self.describe_memories(query=query))
            return None
        if command == "/kb-init":
            name = " ".join(args).strip() if args else ""
            self.ui.assistant(self.init_knowledge_base(name=name))
            return None
        if command == "/kb-status":
            self.ui.assistant(self.knowledge_status())
            return None
        if command == "/kb-compile":
            scope = line[len("/kb-compile") :].strip()
            self.ui.assistant(self.run_knowledge_compile(scope=scope))
            return None
        if command == "/kb-ask":
            question = line[len("/kb-ask") :].strip()
            if not question:
                raise ValueError("Usage: /kb-ask <question>")
            self.ui.assistant(self.run_knowledge_ask(question))
            return None
        if command == "/kb-slide":
            question = line[len("/kb-slide") :].strip()
            if not question:
                raise ValueError("Usage: /kb-slide <question>")
            self.ui.assistant(self.run_knowledge_ask(question, output_format="marp"))
            return None
        if command == "/kb-lint":
            fix = bool(args and args[0].strip().lower() in {"fix", "--fix", "true", "1", "yes"})
            self.ui.assistant(self.run_knowledge_lint(fix=fix))
            return None
        if command == "/diff":
            pathspec = args[0] if args else "."
            result = self.tools.run("git_diff", {"pathspec": pathspec})
            self.ui.tool(result)
            return None
        if command == "/review":
            prompt = (
                "Review the current workspace like a senior code reviewer. "
                "If git diff exists, inspect it first with git_status and git_diff. "
                "Report concrete findings ordered by severity with file references and concise fixes."
            )
            final = self.run_task(prompt)
            self.ui.assistant(final)
            return None
        if command == "/sessions":
            self.ui.assistant(self.list_managed_sessions())
            return None
        if command == "/saved-sessions":
            sessions = self.store.list()
            self.ui.assistant("\n".join(sessions) if sessions else "(no saved sessions)")
            return None
        if command == "/save":
            name = args[0] if args else self.session_name
            path = self.save_session(name)
            self.ui.assistant(f"saved {path}")
            return None
        if command == "/load":
            if not args:
                raise ValueError("Usage: /load <name>")
            self.load_session(args[0])
            self.ui.assistant(f"loaded {self.session_name}")
            return None
        if command == "/reset":
            self._reset_messages()
            self.ui.assistant("session reset")
            return None
        if command == "/run":
            if not args:
                raise ValueError("Usage: /run <shell-command>")
            result = self.tools.run("run_command", {"command": " ".join(args)})
            self.ui.tool(result)
            return None
        if command == "/exit":
            return "exit"
        raise ValueError(f"Unknown command: {command}")

    def repl(self) -> int:
        self.ui.assistant("OpenInstruct REPL. Use /help for commands.")
        self.ui.assistant(self.status())
        while True:
            try:
                line = input("openinstruct> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                self.save_session()
                return 0
            if not line:
                continue
            if line.startswith("/"):
                try:
                    result = self.handle_command(line)
                except Exception as exc:
                    self.ui.error(str(exc))
                    continue
                if result == "exit":
                    self.save_session()
                    return 0
                continue
            final = self.run_task(line)
            self.ui.assistant(final)
