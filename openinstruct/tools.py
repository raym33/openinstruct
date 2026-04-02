from __future__ import annotations

import hashlib
import os
import shutil
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

from .locking import WorkspaceLockManager

ApprovalCallback = Callable[[str], bool]

READONLY_COMMANDS = {
    "cat",
    "find",
    "head",
    "ls",
    "pwd",
    "rg",
    "sed",
    "tail",
    "which",
}
READONLY_GIT_SUBCOMMANDS = {
    "branch",
    "diff",
    "log",
    "ls-files",
    "rev-parse",
    "show",
    "status",
}
SHELL_RISKY_TOKENS = ("|", "&&", "||", ";", ">", "<", "$(", "`")
MAX_SNAPSHOT_BYTES = 120_000


class ToolError(RuntimeError):
    pass


@dataclass
class ToolResult:
    tool: str
    ok: bool
    output: str
    metadata: Dict[str, Any] = field(default_factory=dict)

    def as_prompt_payload(self) -> Dict[str, Any]:
        payload = {"tool": self.tool, "ok": self.ok, "output": self.output}
        payload.update(self.metadata)
        return payload


class WorkspaceTools:
    def __init__(
        self,
        root: Path,
        approval_callback: Optional[ApprovalCallback] = None,
        approval_policy: str = "ask",
        shell: Optional[str] = None,
        lock_manager: Optional[WorkspaceLockManager] = None,
        owner_id: str = "agent",
        ignored_roots: Optional[List[Path]] = None,
    ):
        self.root = root.expanduser().resolve()
        self.approval_callback = approval_callback or (lambda prompt: False)
        self.approval_policy = approval_policy
        self.shell = shell or os.environ.get("SHELL", "/bin/sh")
        self.lock_manager = lock_manager or WorkspaceLockManager()
        self.owner_id = owner_id
        self.ignored_roots = self._normalize_ignored_roots(ignored_roots or [])
        self._mutation_log: List[Dict[str, Any]] = []

    def set_root(self, root: Path) -> None:
        self.root = root.expanduser().resolve()
        self.ignored_roots = self._normalize_ignored_roots(self.ignored_roots)

    def _normalize_ignored_roots(self, roots: List[Path]) -> List[Path]:
        normalized: List[Path] = []
        for path in roots:
            candidate = path.expanduser().resolve()
            if candidate == self.root or candidate in self.root.parents:
                continue
            normalized.append(candidate)
        return normalized

    def set_approval_policy(self, policy: str) -> None:
        self.approval_policy = policy

    def manifest(self) -> str:
        return "\n".join(
            [
                '- get_cwd(): return the current workspace root.',
                '- list_dir(path=".", recursive=false, limit=200): list directory entries.',
                '- glob_files(pattern="**/*", path=".", limit=200): expand a file glob.',
                '- read_file(path, start=1, end=200): read a text file with line numbers.',
                '- search_files(pattern, path=".", limit=50): search text in files.',
                '- read_memory(name="project"): read workspace memory from .openinstruct/<name>.md.',
                '- write_memory(content, name="project"): save workspace memory for future sessions.',
                '- git_status(): show git status if the workspace is a repository.',
                '- git_diff(pathspec="."): show git diff for the workspace or a path.',
                '- make_dir(path): create a directory.',
                '- write_file(path, content): overwrite or create a file.',
                '- append_file(path, content): append text to a file.',
                '- replace_in_file(path, old, new, count=0): string replacement in a file.',
                '- run_command(command, timeout=60): run a shell command in the workspace. Read-only commands can run without approval; mutating commands require approval.',
            ]
        )

    def run(self, name: str, args: Dict[str, Any]) -> ToolResult:
        handlers = {
            "get_cwd": self.get_cwd,
            "list_dir": self.list_dir,
            "glob_files": self.glob_files,
            "read_file": self.read_file,
            "search_files": self.search_files,
            "read_memory": self.read_memory,
            "write_memory": self.write_memory,
            "git_status": self.git_status,
            "git_diff": self.git_diff,
            "make_dir": self.make_dir,
            "write_file": self.write_file,
            "append_file": self.append_file,
            "replace_in_file": self.replace_in_file,
            "run_command": self.run_command,
        }
        if name not in handlers:
            return ToolResult(tool=name, ok=False, output=f"Unknown tool '{name}'.")
        try:
            return handlers[name](**args)
        except TypeError as exc:
            return ToolResult(tool=name, ok=False, output=f"Invalid arguments for '{name}': {exc}")
        except ToolError as exc:
            return ToolResult(tool=name, ok=False, output=str(exc))
        except Exception as exc:  # pragma: no cover
            return ToolResult(tool=name, ok=False, output=f"Unexpected tool error: {exc}")

    def _resolve_path(self, path: str, allow_missing: bool = False) -> Path:
        candidate = Path(path).expanduser()
        if not candidate.is_absolute():
            candidate = self.root / candidate
        candidate = candidate.resolve()
        if candidate != self.root and self.root not in candidate.parents:
            raise ToolError(f"Path escapes workspace root: {path}")
        if not allow_missing and not candidate.exists():
            raise ToolError(f"Path does not exist: {path}")
        return candidate

    def _approve(self, action: str) -> None:
        policy = self.approval_policy.lower()
        if policy == "auto":
            return
        if policy == "deny":
            raise ToolError(f"Action blocked by approval policy: {action}")
        if policy != "ask":
            raise ToolError(f"Unknown approval policy: {self.approval_policy}")
        if not self.approval_callback(action):
            raise ToolError(f"Action rejected by user: {action}")

    def get_cwd(self) -> ToolResult:
        return ToolResult(tool="get_cwd", ok=True, output=str(self.root))

    def list_dir(self, path: str = ".", recursive: bool = False, limit: int = 200) -> ToolResult:
        target = self._resolve_path(path)
        if not target.is_dir():
            raise ToolError(f"Not a directory: {path}")

        entries: Iterable[Path]
        if recursive:
            entries = sorted(target.rglob("*"))
        else:
            entries = sorted(target.iterdir())

        lines: List[str] = []
        for index, entry in enumerate(entries):
            if index >= limit:
                lines.append(f"... truncated after {limit} entries")
                break
            rel = entry.relative_to(self.root)
            suffix = "/" if entry.is_dir() else ""
            lines.append(str(rel) + suffix)
        output = "\n".join(lines) if lines else "(empty)"
        return ToolResult(tool="list_dir", ok=True, output=output, metadata={"path": str(target)})

    def glob_files(self, pattern: str = "**/*", path: str = ".", limit: int = 200) -> ToolResult:
        target = self._resolve_path(path)
        if not target.is_dir():
            raise ToolError(f"Not a directory: {path}")

        matches = sorted(target.glob(pattern))
        lines: List[str] = []
        for index, entry in enumerate(matches):
            if index >= limit:
                lines.append(f"... truncated after {limit} matches")
                break
            if any(part in {".git", "node_modules"} for part in entry.parts):
                continue
            rel = entry.relative_to(self.root)
            suffix = "/" if entry.is_dir() else ""
            lines.append(str(rel) + suffix)
        output = "\n".join(lines) if lines else "(no matches)"
        return ToolResult(tool="glob_files", ok=True, output=output)

    def read_file(self, path: str, start: int = 1, end: int = 200) -> ToolResult:
        target = self._resolve_path(path)
        if not target.is_file():
            raise ToolError(f"Not a file: {path}")
        if start < 1 or end < start:
            raise ToolError("Invalid line range.")

        lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
        selected = lines[start - 1 : end]
        numbered = [f"{start + index:>4} | {line}" for index, line in enumerate(selected)]
        header = f"{target.relative_to(self.root)} lines {start}-{min(end, len(lines))} of {len(lines)}"
        return ToolResult(tool="read_file", ok=True, output=header + "\n" + "\n".join(numbered))

    def search_files(self, pattern: str, path: str = ".", limit: int = 50) -> ToolResult:
        target = self._resolve_path(path)
        if shutil.which("rg"):
            command = [
                "rg",
                "-n",
                "--hidden",
                "--glob",
                "!.git",
                "--glob",
                "!node_modules",
                pattern,
                str(target),
            ]
            completed = subprocess.run(command, capture_output=True, text=True, cwd=self.root)
            if completed.returncode not in (0, 1):
                raise ToolError(completed.stderr.strip() or "rg failed")
            lines = [line for line in completed.stdout.splitlines() if line.strip()]
        else:
            lines = self._search_python(pattern, target)

        if not lines:
            return ToolResult(tool="search_files", ok=True, output="No matches found.")

        trimmed = lines[:limit]
        output = "\n".join(trimmed)
        if len(lines) > limit:
            output += f"\n... truncated after {limit} matches"
        return ToolResult(tool="search_files", ok=True, output=output)

    def memory_path(self, name: str = "project") -> Path:
        safe_name = "".join(char if char.isalnum() or char in {"-", "_"} else "-" for char in name) or "project"
        return self.root / ".openinstruct" / f"{safe_name}.md"

    def _relative_path(self, path: Path) -> str:
        if path == self.root:
            return "."
        return path.relative_to(self.root).as_posix()

    def _lock_key_for_path(self, path: Path) -> str:
        return f"path:{path}"

    def _workspace_write_lock_key(self) -> str:
        return f"workspace:{self.root}:write"

    def _should_ignore_snapshot_path(self, path: Path) -> bool:
        try:
            rel = path.relative_to(self.root)
        except ValueError:
            return True
        if any(part in {".git", "node_modules", "__pycache__"} for part in rel.parts):
            return True
        if path.name == ".DS_Store":
            return True
        for ignored in self.ignored_roots:
            if path == ignored or ignored in path.parents:
                return True
        return False

    def _hash_file(self, path: Path) -> str:
        digest = hashlib.sha1()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(65536), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _snapshot_state(self, path: Path) -> Dict[str, Any]:
        rel_path = self._relative_path(path)
        if not path.exists():
            return {"path": rel_path, "exists": False, "is_dir": False}
        if path.is_dir():
            return {"path": rel_path, "exists": True, "is_dir": True}

        size = path.stat().st_size
        digest = self._hash_file(path)
        if size > MAX_SNAPSHOT_BYTES:
            return {
                "path": rel_path,
                "exists": True,
                "is_dir": False,
                "binary": False,
                "too_large": True,
                "digest": digest,
                "content": "",
            }

        raw = path.read_bytes()
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError:
            return {
                "path": rel_path,
                "exists": True,
                "is_dir": False,
                "binary": True,
                "too_large": False,
                "digest": digest,
                "content": "",
            }
        return {
            "path": rel_path,
            "exists": True,
            "is_dir": False,
            "binary": False,
            "too_large": False,
            "digest": digest,
            "content": content,
        }

    def _snapshot_workspace(self) -> Dict[str, Dict[str, Any]]:
        snapshot: Dict[str, Dict[str, Any]] = {}
        for entry in sorted(self.root.rglob("*")):
            if self._should_ignore_snapshot_path(entry):
                continue
            if not entry.is_file():
                continue
            rel_path = self._relative_path(entry)
            snapshot[rel_path] = self._snapshot_state(entry)
        return snapshot

    def _record_mutation_event(
        self,
        action: str,
        paths: List[str],
        before: Dict[str, Dict[str, Any]],
        after: Dict[str, Dict[str, Any]],
        **metadata: Any,
    ) -> None:
        self._mutation_log.append(
            {
                "action": action,
                "paths": paths,
                "before": before,
                "after": after,
                **metadata,
            }
        )

    def drain_mutation_log(self) -> List[Dict[str, Any]]:
        events = list(self._mutation_log)
        self._mutation_log.clear()
        return events

    def read_memory(self, name: str = "project") -> ToolResult:
        target = self.memory_path(name)
        if not target.exists():
            return ToolResult(tool="read_memory", ok=True, output="(memory not initialized)")
        content = target.read_text(encoding="utf-8", errors="replace")
        return ToolResult(tool="read_memory", ok=True, output=content, metadata={"path": str(target)})

    def write_memory(self, content: str, name: str = "project") -> ToolResult:
        self._approve(f"write_memory {name}")
        target = self.memory_path(name)
        before = {self._relative_path(target): self._snapshot_state(target)}
        with self.lock_manager.hold([self._lock_key_for_path(target)], self.owner_id):
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        after = {self._relative_path(target): self._snapshot_state(target)}
        self._record_mutation_event(
            "write_memory",
            [self._relative_path(target)],
            before,
            after,
            tool="write_memory",
        )
        return ToolResult(tool="write_memory", ok=True, output=f"Saved memory to {target.relative_to(self.root)}")

    def _search_python(self, pattern: str, target: Path) -> List[str]:
        matches: List[str] = []
        files: Iterable[Path]
        if target.is_file():
            files = [target]
        else:
            files = sorted(target.rglob("*"))
        for file_path in files:
            if not file_path.is_file():
                continue
            if any(part in {".git", "node_modules"} for part in file_path.parts):
                continue
            try:
                lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            for line_number, line in enumerate(lines, start=1):
                if pattern in line:
                    rel = file_path.relative_to(self.root)
                    matches.append(f"{rel}:{line_number}:{line}")
        return matches

    def _run_process(self, argv: List[str], timeout: int = 30) -> subprocess.CompletedProcess:
        try:
            return subprocess.run(argv, cwd=self.root, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            raise ToolError(f"Command timed out after {timeout}s: {' '.join(argv)}") from exc

    def git_status(self) -> ToolResult:
        completed = self._run_process(["git", "status", "--short", "--branch"])
        if completed.returncode != 0:
            raise ToolError(completed.stderr.strip() or "git status failed")
        output = completed.stdout.strip() or "(clean working tree)"
        return ToolResult(tool="git_status", ok=True, output=output)

    def git_diff(self, pathspec: str = ".") -> ToolResult:
        completed = self._run_process(["git", "diff", "--", pathspec], timeout=60)
        if completed.returncode != 0:
            raise ToolError(completed.stderr.strip() or "git diff failed")
        output = completed.stdout.strip() or "(no diff)"
        if len(output) > 12000:
            output = output[:12000] + "\n... truncated"
        return ToolResult(tool="git_diff", ok=True, output=output)

    def make_dir(self, path: str) -> ToolResult:
        self._approve(f"make_dir {path}")
        target = self._resolve_path(path, allow_missing=True)
        before = {self._relative_path(target): self._snapshot_state(target)}
        with self.lock_manager.hold([self._lock_key_for_path(target)], self.owner_id):
            target.mkdir(parents=True, exist_ok=True)
        after = {self._relative_path(target): self._snapshot_state(target)}
        self._record_mutation_event("make_dir", [self._relative_path(target)], before, after, tool="make_dir")
        return ToolResult(tool="make_dir", ok=True, output=f"Created directory {target.relative_to(self.root)}")

    def write_file(self, path: str, content: str) -> ToolResult:
        self._approve(f"write_file {path}")
        target = self._resolve_path(path, allow_missing=True)
        before = {self._relative_path(target): self._snapshot_state(target)}
        with self.lock_manager.hold([self._lock_key_for_path(target)], self.owner_id):
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        after = {self._relative_path(target): self._snapshot_state(target)}
        self._record_mutation_event("write_file", [self._relative_path(target)], before, after, tool="write_file")
        return ToolResult(
            tool="write_file",
            ok=True,
            output=f"Wrote {len(content)} characters to {target.relative_to(self.root)}",
        )

    def append_file(self, path: str, content: str) -> ToolResult:
        self._approve(f"append_file {path}")
        target = self._resolve_path(path, allow_missing=True)
        before = {self._relative_path(target): self._snapshot_state(target)}
        with self.lock_manager.hold([self._lock_key_for_path(target)], self.owner_id):
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("a", encoding="utf-8") as handle:
                handle.write(content)
        after = {self._relative_path(target): self._snapshot_state(target)}
        self._record_mutation_event("append_file", [self._relative_path(target)], before, after, tool="append_file")
        return ToolResult(
            tool="append_file",
            ok=True,
            output=f"Appended {len(content)} characters to {target.relative_to(self.root)}",
        )

    def replace_in_file(self, path: str, old: str, new: str, count: int = 0) -> ToolResult:
        self._approve(f"replace_in_file {path}")
        target = self._resolve_path(path)
        before = {self._relative_path(target): self._snapshot_state(target)}
        with self.lock_manager.hold([self._lock_key_for_path(target)], self.owner_id):
            original = target.read_text(encoding="utf-8", errors="replace")
            occurrences = original.count(old)
            if occurrences == 0:
                raise ToolError("Target text was not found in file.")
            replaced = original.replace(old, new, count or occurrences)
            target.write_text(replaced, encoding="utf-8")
        after = {self._relative_path(target): self._snapshot_state(target)}
        self._record_mutation_event(
            "replace_in_file",
            [self._relative_path(target)],
            before,
            after,
            tool="replace_in_file",
        )
        changed = min(count, occurrences) if count else occurrences
        return ToolResult(tool="replace_in_file", ok=True, output=f"Replaced {changed} occurrence(s) in {path}")

    def _command_requires_approval(self, command: str) -> bool:
        if any(token in command for token in SHELL_RISKY_TOKENS):
            return True
        try:
            argv = shlex.split(command)
        except ValueError:
            return True
        if not argv:
            return False
        binary = argv[0]
        if binary == "git":
            return len(argv) < 2 or argv[1] not in READONLY_GIT_SUBCOMMANDS
        return binary not in READONLY_COMMANDS

    def run_command(self, command: str, timeout: int = 60) -> ToolResult:
        requires_approval = self._command_requires_approval(command)
        if requires_approval:
            self._approve(f"run_command {command}")
        lock_keys = [self._workspace_write_lock_key()] if requires_approval else []
        before_snapshot = self._snapshot_workspace() if requires_approval else {}
        try:
            with self.lock_manager.hold(lock_keys, self.owner_id):
                completed = subprocess.run(
                    command,
                    shell=True,
                    cwd=self.root,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    executable=self.shell,
                )
        except subprocess.TimeoutExpired as exc:
            raise ToolError(f"Command timed out after {timeout}s: {command}") from exc
        if requires_approval:
            after_snapshot = self._snapshot_workspace()
            changed_paths = sorted(
                path
                for path in (set(before_snapshot) | set(after_snapshot))
                if before_snapshot.get(path) != after_snapshot.get(path)
            )
            before = {path: before_snapshot.get(path, {"path": path, "exists": False, "is_dir": False}) for path in changed_paths}
            after = {path: after_snapshot.get(path, {"path": path, "exists": False, "is_dir": False}) for path in changed_paths}
            self._record_mutation_event(
                "run_command",
                changed_paths,
                before,
                after,
                tool="run_command",
                command=command,
                scope="workspace",
            )

        output = (completed.stdout + completed.stderr).strip()
        if not output:
            output = "(no output)"
        if len(output) > 6000:
            output = output[:6000] + "\n... truncated"
        ok = completed.returncode == 0
        return ToolResult(
            tool="run_command",
            ok=ok,
            output=output,
            metadata={"returncode": completed.returncode},
        )
