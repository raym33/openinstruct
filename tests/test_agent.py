import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from openinstruct.agent import AgentRuntime
from openinstruct.config import Settings
from openinstruct.memory import BaseMemoryBackend, MemoryRecord
from openinstruct.providers import ProviderInfo


class DummyProvider:
    def list_models(self):
        return ["dummy-model"]

    def resolve_model(self, requested: str) -> str:
        return requested or "dummy-model"

    def chat(self, messages, model, temperature=0.2):
        user_messages = [message["content"] for message in messages if message["role"] == "user"]
        last = user_messages[-1] if user_messages else "ok"
        return json.dumps({"summary": "done", "actions": [], "final": last})


class PlanningProvider(DummyProvider):
    def chat(self, messages, model, temperature=0.2):
        system = messages[0]["content"] if messages else ""
        if "Break the goal into a dependency-aware task graph" in system:
            return json.dumps(
                {
                    "summary": "Parallel review then synthesis",
                    "tasks": [
                        {
                            "name": "inspect-auth",
                            "prompt": "Inspect the auth implementation",
                            "depends_on": [],
                            "write": False,
                            "write_paths": [],
                        },
                        {
                            "name": "inspect-tests",
                            "prompt": "Inspect the test suite",
                            "depends_on": [],
                            "write": False,
                            "write_paths": [],
                        },
                        {
                            "name": "synthesis",
                            "prompt": "Combine findings and propose changes",
                            "depends_on": ["inspect-auth", "inspect-tests"],
                            "write": False,
                            "write_paths": [],
                        },
                    ],
                }
            )
        return super().chat(messages, model, temperature)


class TestRuntime(AgentRuntime):
    def create_provider(self):
        return DummyProvider()


class PlanningRuntime(AgentRuntime):
    def create_provider(self):
        return PlanningProvider()


class MutationRuntime(TestRuntime):
    def __init__(self, *args, task_actions=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.task_actions = task_actions or {}

    def _run_subagent_task(self, task, allow_mutations, index, dependency_context=""):
        child_runtime = self.clone_runtime(
            session_suffix=f"mutation-{index}",
            approval_policy="auto" if allow_mutations else "deny",
            enable_subagents=False,
        )
        for action in self.task_actions.get(task.name, []):
            result = child_runtime.tools.run(action["tool"], action.get("args", {}))
            if not result.ok:
                raise AssertionError(result.output)
        return {
            "name": task.name,
            "ok": True,
            "final": task.prompt,
            "mutations": child_runtime.tools.drain_mutation_log(),
        }


class ToolCallingProvider(DummyProvider):
    def __init__(self, task_actions):
        self.task_actions = task_actions

    def chat(self, messages, model, temperature=0.2):
        user_messages = [message["content"] for message in messages if message["role"] == "user"]
        last = user_messages[-1] if user_messages else "ok"
        if "Tool results are ready" in last:
            return json.dumps({"summary": "done", "actions": [], "final": "completed"})
        for task_name, actions in self.task_actions.items():
            if f"delegated subtask named '{task_name}'" in last:
                return json.dumps({"summary": "act", "actions": actions, "final": ""})
        return super().chat(messages, model, temperature)


class ToolCallingRuntime(AgentRuntime):
    def __init__(self, *args, task_actions=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.task_actions = task_actions or {}

    def create_provider(self):
        return ToolCallingProvider(self.task_actions)


class FlakyRuntime(TestRuntime):
    def __init__(self, *args, failure_plan=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.failure_plan = dict(failure_plan or {})
        self.attempts = {}

    def _run_subagent_task(self, task, allow_mutations, index, dependency_context=""):
        attempt = self.attempts.get(task.name, 0) + 1
        self.attempts[task.name] = attempt
        failures_before_success = self.failure_plan.get(task.name, 0)
        if attempt <= failures_before_success:
            return {
                "name": task.name,
                "ok": False,
                "final": f"attempt {attempt} failed for {task.name}",
                "mutations": [],
            }
        return {
            "name": task.name,
            "ok": True,
            "final": f"{task.name} completed on attempt {attempt}",
            "mutations": [],
        }


class FakeMemoryBackend(BaseMemoryBackend):
    name = "fake-memory"

    def __init__(self):
        self.recall_queries = []
        self.stored = []
        self.recent_records = []

    def enabled(self) -> bool:
        return True

    def describe(self) -> str:
        return self.name

    def search(self, query: str, *, session_name: str, agent_label: str, limit: int = 5):
        self.recall_queries.append((query, session_name, agent_label))
        return self.recent_records[:limit] or [MemoryRecord(text="User prefers terse summaries.", backend=self.name)]

    def recent(self, *, session_name: str, agent_label: str, limit: int = 8):
        return self.recent_records[:limit]

    def store(self, user_text: str, assistant_text: str, *, session_name: str, agent_label: str) -> None:
        self.stored.append((user_text, assistant_text, session_name, agent_label))


class AgentRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.settings = Settings(
            provider="ollama",
            model="dummy-model",
            workdir=self.root,
            approval_policy="auto",
            max_steps=4,
            temperature=0.2,
            home=self.root / ".home",
        )
        self.runtime = AgentRuntime(
            settings=self.settings,
            provider_info=ProviderInfo(name="ollama", base_url="http://127.0.0.1:11434", model="dummy-model"),
            provider=DummyProvider(),
        )

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def init_git_repo(self, root: Path) -> None:
        subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True, text=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True, capture_output=True, text=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=root, check=True, capture_output=True, text=True)
        (root / "README.md").write_text("base\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=root, check=True, capture_output=True, text=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=root, check=True, capture_output=True, text=True)

    def test_init_project_memory_creates_file(self) -> None:
        result = self.runtime.init_project_memory()
        self.assertIn(".openinstruct/project.md", result)
        memory_path = self.root / ".openinstruct" / "project.md"
        self.assertTrue(memory_path.exists())

    def test_compact_history_keeps_recent_messages(self) -> None:
        self.runtime.messages.extend(
            [
                {"role": "user", "content": "one"},
                {"role": "assistant", "content": "two"},
                {"role": "user", "content": "three"},
            ]
        )
        result = self.runtime.compact_history(2)
        self.assertIn("history compacted", result)
        self.assertEqual(len(self.runtime.messages), 3)
        self.assertEqual(self.runtime.messages[1]["content"], "two")
        self.assertEqual(self.runtime.messages[2]["content"], "three")

    def test_run_parallel_tasks_returns_ordered_results(self) -> None:
        runtime = TestRuntime(
            settings=self.settings,
            provider_info=ProviderInfo(name="ollama", base_url="http://127.0.0.1:11434", model="dummy-model"),
            provider=DummyProvider(),
        )
        result = runtime.run_parallel_tasks(
            tasks=[
                {"name": "scan", "prompt": "inspect auth flow"},
                {"name": "tests", "prompt": "check failing tests"},
            ],
            allow_mutations=False,
            max_agents=2,
        )
        self.assertTrue(result.ok)
        self.assertIn("Executed 2 planned tasks", result.output)
        self.assertIn("scan", result.output)
        self.assertIn("inspect auth flow", result.output)
        self.assertIn("tests", result.output)
        self.assertIn("check failing tests", result.output)

    def test_run_parallel_tasks_blocks_mutations_without_auto(self) -> None:
        strict_settings = Settings(
            provider="ollama",
            model="dummy-model",
            workdir=self.root,
            approval_policy="ask",
            max_steps=4,
            max_agents=3,
            temperature=0.2,
            home=self.root / ".home",
        )
        runtime = TestRuntime(
            settings=strict_settings,
            provider_info=ProviderInfo(name="ollama", base_url="http://127.0.0.1:11434", model="dummy-model"),
            provider=DummyProvider(),
        )
        result = runtime.run_parallel_tasks(tasks=["write code", "run tests"], allow_mutations=True)
        self.assertFalse(result.ok)
        self.assertIn("approval policy is 'auto'", result.output)

    def test_plan_goal_returns_dependency_graph(self) -> None:
        runtime = PlanningRuntime(
            settings=self.settings,
            provider_info=ProviderInfo(name="ollama", base_url="http://127.0.0.1:11434", model="dummy-model"),
            provider=PlanningProvider(),
        )
        result = runtime.plan_goal("Review the project and synthesize findings", allow_mutations=False, max_tasks=3)
        self.assertTrue(result.ok)
        self.assertIn("Parallel review then synthesis", result.output)
        self.assertIn("inspect-auth", result.output)
        self.assertIn("synthesis", result.output)
        self.assertEqual(len(result.metadata["tasks"]), 3)

    def test_background_task_completes(self) -> None:
        runtime = TestRuntime(
            settings=self.settings,
            provider_info=ProviderInfo(name="ollama", base_url="http://127.0.0.1:11434", model="dummy-model"),
            provider=DummyProvider(),
        )
        task = runtime.start_background_task("inspect the auth module")
        finished = runtime.wait_for_background_task(task.task_id, timeout=1.0)
        self.assertEqual(finished.status, "completed")
        self.assertIn("inspect the auth module", finished.result)
        listing = runtime.list_background_tasks()
        self.assertIn(task.task_id, listing)

    def test_managed_session_spawn_send_and_history(self) -> None:
        runtime = TestRuntime(
            settings=self.settings,
            provider_info=ProviderInfo(name="ollama", base_url="http://127.0.0.1:11434", model="dummy-model"),
            provider=DummyProvider(),
        )
        session = runtime.spawn_managed_session("inspect auth flow")
        runtime.wait_for_managed_session(session.session_id, timeout=1.0)
        queued = runtime.send_managed_session_input(session.session_id, "summarize findings")
        self.assertTrue(queued.message_id.startswith(session.session_id))
        runtime.wait_for_managed_session(session.session_id, timeout=1.0)
        listing = runtime.list_managed_sessions()
        status = runtime.managed_session_status(session.session_id)
        history = runtime.managed_session_history(session.session_id, limit=6)
        self.assertIn(session.session_id, listing)
        self.assertIn("queued=", status)
        self.assertIn(session.session_id, status)
        self.assertIn("summarize findings", history)
        self.assertIn("inspect auth flow", history)

    def test_session_tools_spawn_send_and_history(self) -> None:
        runtime = TestRuntime(
            settings=self.settings,
            provider_info=ProviderInfo(name="ollama", base_url="http://127.0.0.1:11434", model="dummy-model"),
            provider=DummyProvider(),
        )
        spawn = runtime.run_action("sessions_spawn", {"prompt": "inspect billing"})
        self.assertTrue(spawn.ok)
        session_id = spawn.metadata["session_id"]
        runtime.wait_for_managed_session(session_id, timeout=1.0)
        send = runtime.run_action("sessions_send", {"session_id": session_id, "prompt": "report back"})
        self.assertTrue(send.ok)
        runtime.wait_for_managed_session(session_id, timeout=1.0)
        status = runtime.run_action("sessions_status", {"session_id": session_id})
        history = runtime.run_action("sessions_history", {"session_id": session_id, "limit": 6})
        listing = runtime.run_action("sessions_list", {})
        self.assertTrue(status.ok)
        self.assertTrue(history.ok)
        self.assertTrue(listing.ok)
        self.assertEqual(status.metadata["session_id"], session_id)
        self.assertEqual(listing.metadata["sessions"][0]["session_id"], session_id)
        self.assertIn("queued=", status.output)
        self.assertIn("report back", history.output)
        self.assertIn(session_id, listing.output)

    def test_tree_visibility_limits_managed_session_access(self) -> None:
        root = TestRuntime(
            settings=self.settings,
            provider_info=ProviderInfo(name="ollama", base_url="http://127.0.0.1:11434", model="dummy-model"),
            provider=DummyProvider(),
        )
        parent = root.spawn_managed_session("inspect auth flow", session_id="sess_parent", visibility="tree")
        root.wait_for_managed_session(parent.session_id, timeout=1.0)
        outsider = root.spawn_managed_session("inspect billing flow", session_id="sess_outsider", visibility="tree")
        root.wait_for_managed_session(outsider.session_id, timeout=1.0)

        child_runtime = parent.runtime
        self.assertIsNotNone(child_runtime)
        descendant = child_runtime.spawn_managed_session("inspect auth tests", session_id="sess_child", visibility="tree")  # type: ignore[union-attr]
        root.wait_for_managed_session(descendant.session_id, timeout=1.0)

        listing = child_runtime.run_action("sessions_list", {})  # type: ignore[union-attr]
        denied = child_runtime.run_action("sessions_status", {"session_id": outsider.session_id})  # type: ignore[union-attr]
        allowed = child_runtime.run_action("sessions_status", {"session_id": descendant.session_id})  # type: ignore[union-attr]

        self.assertTrue(listing.ok)
        visible_ids = [item["session_id"] for item in listing.metadata["sessions"]]
        self.assertIn(parent.session_id, visible_ids)
        self.assertIn(descendant.session_id, visible_ids)
        self.assertNotIn(outsider.session_id, visible_ids)
        self.assertFalse(denied.ok)
        self.assertIn("not visible", denied.output)
        self.assertTrue(allowed.ok)
        self.assertEqual(allowed.metadata["visibility"], "tree")

    def test_self_visibility_only_exposes_current_session(self) -> None:
        root = TestRuntime(
            settings=self.settings,
            provider_info=ProviderInfo(name="ollama", base_url="http://127.0.0.1:11434", model="dummy-model"),
            provider=DummyProvider(),
        )
        session = root.spawn_managed_session("inspect auth flow", session_id="sess_self", visibility="self")
        root.wait_for_managed_session(session.session_id, timeout=1.0)
        sibling = root.spawn_managed_session("inspect docs", session_id="sess_sibling", visibility="tree")
        root.wait_for_managed_session(sibling.session_id, timeout=1.0)

        child_runtime = session.runtime
        self.assertIsNotNone(child_runtime)
        listing = child_runtime.run_action("sessions_list", {})  # type: ignore[union-attr]
        self.assertTrue(listing.ok)
        self.assertEqual([item["session_id"] for item in listing.metadata["sessions"]], [session.session_id])
        denied = child_runtime.run_action("sessions_send", {"session_id": sibling.session_id, "prompt": "hello"})  # type: ignore[union-attr]
        self.assertFalse(denied.ok)
        self.assertIn("not visible", denied.output)

    def test_orchestrate_goal_executes_planned_tasks(self) -> None:
        runtime = PlanningRuntime(
            settings=self.settings,
            provider_info=ProviderInfo(name="ollama", base_url="http://127.0.0.1:11434", model="dummy-model"),
            provider=PlanningProvider(),
        )
        result = runtime.orchestrate_goal("Review the project and synthesize findings", allow_mutations=False)
        self.assertTrue(result.ok)
        self.assertEqual(result.tool, "orchestrate")
        self.assertIn("Parallel review then synthesis", result.output)
        self.assertIn("inspect-auth", result.output)
        self.assertIn("synthesis", result.output)

    def test_run_task_uses_memory_backend_for_recall_and_store(self) -> None:
        backend = FakeMemoryBackend()
        settings = Settings(
            provider="ollama",
            model="dummy-model",
            workdir=self.root,
            approval_policy="auto",
            memory_policy="all",
            max_steps=4,
            temperature=0.2,
            home=self.root / ".home",
        )
        runtime = AgentRuntime(
            settings=settings,
            provider_info=ProviderInfo(name="ollama", base_url="http://127.0.0.1:11434", model="dummy-model"),
            provider=DummyProvider(),
            memory_backend=backend,
        )
        result = runtime.run_task("summarize the repo")
        self.assertEqual(result, "summarize the repo\nRelevant long-term memory from the configured memory backend:\nUse it only when relevant and never over the current workspace or user instruction.\n- User prefers terse summaries.")
        self.assertEqual(backend.recall_queries[0][0], "summarize the repo")
        self.assertEqual(backend.stored[0][0], "summarize the repo")
        self.assertEqual(backend.stored[0][1], result)

    def test_selective_memory_storage_extracts_useful_facts(self) -> None:
        backend = FakeMemoryBackend()
        runtime = AgentRuntime(
            settings=self.settings,
            provider_info=ProviderInfo(name="ollama", base_url="http://127.0.0.1:11434", model="dummy-model"),
            provider=DummyProvider(),
            memory_backend=backend,
        )
        runtime._store_long_term_memory(
            "remember my workflow",
            "- Use Obsidian as the frontend for the wiki.\n- Store outputs in outputs/slides and outputs/figures.\n- Keep backlinks in wiki/concepts.",
        )
        stored_facts = [item[0] for item in backend.stored]
        self.assertTrue(any("Use Obsidian as the frontend for the wiki." in fact for fact in stored_facts))
        self.assertTrue(any("outputs/slides" in fact for fact in stored_facts))

    def test_describe_memories_uses_recent_or_cached_results(self) -> None:
        backend = FakeMemoryBackend()
        backend.recent_records = [
            MemoryRecord(text="Use qwen for coding.", backend=backend.name),
            MemoryRecord(text="Keep the wiki in markdown.", backend=backend.name),
        ]
        runtime = AgentRuntime(
            settings=self.settings,
            provider_info=ProviderInfo(name="ollama", base_url="http://127.0.0.1:11434", model="dummy-model"),
            provider=DummyProvider(),
            memory_backend=backend,
        )
        text = runtime.describe_memories()
        self.assertIn("recent memories", text)
        self.assertIn("Keep the wiki in markdown.", text)

    def test_knowledge_output_path_must_stay_in_workspace(self) -> None:
        runtime = TestRuntime(
            settings=self.settings,
            provider_info=ProviderInfo(name="ollama", base_url="http://127.0.0.1:11434", model="dummy-model"),
            provider=DummyProvider(),
        )
        with self.assertRaises(ValueError):
            runtime.run_knowledge_ask("summarize the topic", output_path="../../outside.md")

    def test_failed_task_is_retried_and_checkpointed(self) -> None:
        runtime = FlakyRuntime(
            settings=self.settings,
            provider_info=ProviderInfo(name="ollama", base_url="http://127.0.0.1:11434", model="dummy-model"),
            provider=DummyProvider(),
            failure_plan={"scan": 1},
        )
        result = runtime.run_parallel_tasks(
            tasks=[{"name": "scan", "prompt": "Inspect auth flow"}],
            allow_mutations=False,
            max_agents=1,
        )
        self.assertTrue(result.ok)
        self.assertEqual(runtime.attempts["scan"], 2)
        self.assertIn("attempts=2", result.output)
        self.assertTrue(runtime.last_checkpoint_path and runtime.last_checkpoint_path.exists())
        checkpoints = result.metadata["checkpoints"]
        self.assertEqual(checkpoints[0]["status"], "retrying")
        self.assertEqual(checkpoints[1]["status"], "success")

    def test_failed_dependency_blocks_downstream_task_and_checkpoint_records_it(self) -> None:
        retry_settings = Settings(
            provider="ollama",
            model="dummy-model",
            workdir=self.root,
            approval_policy="auto",
            max_steps=4,
            max_agents=2,
            task_retries=0,
            temperature=0.2,
            home=self.root / ".home",
        )
        runtime = FlakyRuntime(
            settings=retry_settings,
            provider_info=ProviderInfo(name="ollama", base_url="http://127.0.0.1:11434", model="dummy-model"),
            provider=DummyProvider(),
            failure_plan={"scan": 1},
        )
        result = runtime.run_parallel_tasks(
            tasks=[
                {"name": "scan", "prompt": "Inspect auth flow"},
                {"name": "fix", "prompt": "Apply fix", "depends_on": ["scan"]},
            ],
            allow_mutations=False,
            max_agents=2,
        )
        self.assertFalse(result.ok)
        self.assertIn("blocked by failed dependencies: scan", result.output)
        self.assertIn("retrying=0", runtime.describe_checkpoints())
        checkpoints = result.metadata["checkpoints"]
        self.assertEqual(checkpoints[0]["status"], "failed")
        self.assertEqual(checkpoints[1]["status"], "blocked")

    def test_resume_checkpoint_reruns_only_failed_or_blocked_tasks(self) -> None:
        retry_settings = Settings(
            provider="ollama",
            model="dummy-model",
            workdir=self.root,
            approval_policy="auto",
            max_steps=4,
            max_agents=2,
            task_retries=0,
            temperature=0.2,
            home=self.root / ".home",
        )
        runtime = FlakyRuntime(
            settings=retry_settings,
            provider_info=ProviderInfo(name="ollama", base_url="http://127.0.0.1:11434", model="dummy-model"),
            provider=DummyProvider(),
            failure_plan={"scan": 1},
        )
        first = runtime.run_parallel_tasks(
            tasks=[
                {"name": "scan", "prompt": "Inspect auth flow"},
                {"name": "fix", "prompt": "Apply fix", "depends_on": ["scan"]},
            ],
            allow_mutations=False,
            max_agents=2,
        )
        self.assertFalse(first.ok)
        first_run_id = first.metadata["checkpoint_run_id"]
        resumed = runtime.resume_from_checkpoint(first_run_id, max_agents=2)
        self.assertTrue(resumed.ok)
        self.assertEqual(resumed.tool, "spawn_agents")
        self.assertEqual(runtime.attempts["scan"], 2)
        self.assertIn("Resumed from checkpoint", resumed.output)
        self.assertEqual(resumed.metadata["resumed_from_run_id"], first_run_id)

    def test_resume_checkpoint_reuses_successful_tasks(self) -> None:
        retry_settings = Settings(
            provider="ollama",
            model="dummy-model",
            workdir=self.root,
            approval_policy="auto",
            max_steps=4,
            max_agents=2,
            task_retries=0,
            temperature=0.2,
            home=self.root / ".home",
        )
        runtime = FlakyRuntime(
            settings=retry_settings,
            provider_info=ProviderInfo(name="ollama", base_url="http://127.0.0.1:11434", model="dummy-model"),
            provider=DummyProvider(),
            failure_plan={"fix": 1},
        )
        first = runtime.run_parallel_tasks(
            tasks=[
                {"name": "scan", "prompt": "Inspect auth flow"},
                {"name": "fix", "prompt": "Apply fix"},
            ],
            allow_mutations=False,
            max_agents=2,
        )
        self.assertFalse(first.ok)
        self.assertEqual(runtime.attempts["scan"], 1)
        resumed = runtime.resume_from_checkpoint(first.metadata["checkpoint_run_id"], max_agents=2)
        self.assertTrue(resumed.ok)
        self.assertEqual(runtime.attempts["scan"], 1)
        self.assertEqual(runtime.attempts["fix"], 2)
        self.assertIn("Reused tasks: scan", resumed.output)
        self.assertIn("scan", resumed.metadata["reused_tasks"])

    def test_parallel_mutations_include_merge_report(self) -> None:
        runtime = MutationRuntime(
            settings=self.settings,
            provider_info=ProviderInfo(name="ollama", base_url="http://127.0.0.1:11434", model="dummy-model"),
            provider=DummyProvider(),
            task_actions={
                "auth-fix": [{"tool": "write_file", "args": {"path": "auth.py", "content": "AUTH = True\n"}}],
                "tests-fix": [{"tool": "write_file", "args": {"path": "tests.txt", "content": "ok\n"}}],
            },
        )
        result = runtime.run_parallel_tasks(
            tasks=[
                {"name": "auth-fix", "prompt": "Fix auth", "write": True, "write_paths": ["auth.py"]},
                {"name": "tests-fix", "prompt": "Fix tests", "write": True, "write_paths": ["tests.txt"]},
            ],
            allow_mutations=True,
            max_agents=2,
        )
        report = result.metadata["merge_report"]
        by_name = {item["name"]: item for item in report["tasks"]}
        self.assertTrue(result.ok)
        self.assertIn("Merge supervisor:", result.output)
        self.assertEqual(report["changed_path_count"], 2)
        self.assertEqual(by_name["auth-fix"]["changed_paths"], ["auth.py"])
        self.assertEqual(by_name["tests-fix"]["changed_paths"], ["tests.txt"])
        self.assertEqual(report["conflicts"], [])

    def test_merge_supervisor_detects_handoffs(self) -> None:
        runtime = MutationRuntime(
            settings=self.settings,
            provider_info=ProviderInfo(name="ollama", base_url="http://127.0.0.1:11434", model="dummy-model"),
            provider=DummyProvider(),
            task_actions={
                "draft": [{"tool": "write_file", "args": {"path": "notes.txt", "content": "draft\n"}}],
                "finalize": [{"tool": "append_file", "args": {"path": "notes.txt", "content": "final\n"}}],
            },
        )
        result = runtime.run_parallel_tasks(
            tasks=[
                {"name": "draft", "prompt": "Draft notes", "write": True, "write_paths": ["notes.txt"]},
                {
                    "name": "finalize",
                    "prompt": "Finalize notes",
                    "write": True,
                    "write_paths": ["notes.txt"],
                    "depends_on": ["draft"],
                },
            ],
            allow_mutations=True,
            max_agents=2,
        )
        handoff = result.metadata["merge_report"]["handoffs"][0]
        self.assertEqual(handoff["from"], "draft")
        self.assertEqual(handoff["to"], "finalize")
        self.assertEqual(handoff["paths"], ["notes.txt"])

    def test_merge_supervisor_flags_out_of_scope_conflicts(self) -> None:
        runtime = MutationRuntime(
            settings=self.settings,
            provider_info=ProviderInfo(name="ollama", base_url="http://127.0.0.1:11434", model="dummy-model"),
            provider=DummyProvider(),
            task_actions={
                "left": [{"tool": "write_file", "args": {"path": "shared.txt", "content": "left\n"}}],
                "right": [{"tool": "append_file", "args": {"path": "shared.txt", "content": "right\n"}}],
            },
        )
        result = runtime.run_parallel_tasks(
            tasks=[
                {"name": "left", "prompt": "Left edit", "write": True, "write_paths": ["left.txt"]},
                {"name": "right", "prompt": "Right edit", "write": True, "write_paths": ["right.txt"]},
            ],
            allow_mutations=True,
            max_agents=2,
        )
        report = result.metadata["merge_report"]
        by_name = {item["name"]: item for item in report["tasks"]}
        self.assertEqual(report["conflicts"][0]["paths"], ["shared.txt"])
        self.assertEqual(by_name["left"]["out_of_scope_paths"], ["shared.txt"])
        self.assertEqual(by_name["right"]["out_of_scope_paths"], ["shared.txt"])

    def test_git_worktree_isolation_merges_back_to_primary_workspace(self) -> None:
        self.init_git_repo(self.root)
        runtime = ToolCallingRuntime(
            settings=self.settings,
            provider_info=ProviderInfo(name="ollama", base_url="http://127.0.0.1:11434", model="dummy-model"),
            provider=DummyProvider(),
            task_actions={
                "auth-fix": [
                    {
                        "tool": "write_file",
                        "args": {"path": "auth.py", "content": "AUTH = 'isolated'\n"},
                    }
                ]
            },
        )
        result = runtime.run_parallel_tasks(
            tasks=[{"name": "auth-fix", "prompt": "Fix auth", "write": True, "write_paths": ["auth.py"]}],
            allow_mutations=True,
            max_agents=1,
        )
        item = result.metadata["results"][0]
        sandbox = item["sandbox"]
        self.assertTrue(result.ok)
        self.assertTrue(sandbox["isolated"])
        self.assertFalse(Path(sandbox["worktree_path"]).exists())
        self.assertEqual((self.root / "auth.py").read_text(encoding="utf-8"), "AUTH = 'isolated'\n")

    def test_git_worktree_overlap_is_reported_in_merge_supervisor(self) -> None:
        self.init_git_repo(self.root)
        retry_settings = Settings(
            provider="ollama",
            model="dummy-model",
            workdir=self.root,
            approval_policy="auto",
            max_steps=4,
            max_agents=2,
            task_retries=0,
            temperature=0.2,
            home=self.root / ".home",
        )
        runtime = ToolCallingRuntime(
            settings=retry_settings,
            provider_info=ProviderInfo(name="ollama", base_url="http://127.0.0.1:11434", model="dummy-model"),
            provider=DummyProvider(),
            task_actions={
                "left": [{"tool": "write_file", "args": {"path": "shared.txt", "content": "left\n"}}],
                "right": [{"tool": "write_file", "args": {"path": "shared.txt", "content": "right\n"}}],
            },
        )
        result = runtime.run_parallel_tasks(
            tasks=[
                {"name": "left", "prompt": "Left edit", "write": True, "write_paths": ["left.txt"]},
                {"name": "right", "prompt": "Right edit", "write": True, "write_paths": ["right.txt"]},
            ],
            allow_mutations=True,
            max_agents=2,
        )
        merge_report = result.metadata["merge_report"]
        self.assertTrue(result.ok)
        self.assertTrue(merge_report["conflicts"])
        self.assertEqual(merge_report["conflicts"][0]["paths"], ["shared.txt"])


if __name__ == "__main__":
    unittest.main()
