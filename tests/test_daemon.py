import json
import tempfile
import time
import unittest
from pathlib import Path

from openinstruct.agent import AgentRuntime
from openinstruct.config import Settings
from openinstruct.daemon import OpenInstructDaemon
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


class DaemonTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        settings = Settings(
            provider="ollama",
            model="dummy-model",
            workdir=self.root,
            approval_policy="auto",
            max_steps=4,
            temperature=0.2,
            home=self.root / ".home",
        )
        runtime = AgentRuntime(
            settings=settings,
            provider_info=ProviderInfo(name="ollama", base_url="http://127.0.0.1:11434", model="dummy-model"),
            provider=DummyProvider(),
        )
        self.daemon = OpenInstructDaemon(runtime)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def wait_for_job(self, job_id: str, timeout: float = 3.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            job = self.daemon.get_job(job_id)
            if job.status in {"completed", "failed"}:
                return job
            time.sleep(0.01)
        self.fail(f"job {job_id} did not finish in time")

    def test_state_payload_reports_runtime_status(self) -> None:
        payload = self.daemon.state_payload()
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["runtime"]["model"], "dummy-model")
        self.assertEqual(payload["runtime"]["approval_policy"], "auto")
        self.assertEqual(payload["job_count"], 0)

    def test_prompt_job_runs_and_records_result(self) -> None:
        job = self.daemon.create_prompt_job("inspect the repo")
        completed = self.wait_for_job(job.job_id)
        self.assertEqual(completed.status, "completed")
        self.assertEqual(completed.result, "inspect the repo")
        self.assertTrue(completed.events)
        self.assertEqual(completed.events[0].kind, "info")

    def test_command_job_captures_command_output(self) -> None:
        job = self.daemon.create_command_job("/status")
        completed = self.wait_for_job(job.job_id)
        self.assertEqual(completed.status, "completed")
        self.assertIn("provider=ollama", completed.result)
        self.assertIn("session=", completed.result)

    def test_managed_sessions_payloads_are_structured(self) -> None:
        payload = self.daemon.spawn_session("review auth layer")
        session_id = payload["session_id"]
        waited = self.daemon.runtime.wait_for_managed_session(session_id, timeout=2.0)
        self.assertEqual(waited.session_id, session_id)
        listing = self.daemon.sessions_payload()
        self.assertEqual(len(listing["sessions"]), 1)
        status = self.daemon.session_status_payload(session_id)
        history = self.daemon.session_payload(session_id)
        self.assertEqual(status["session_id"], session_id)
        self.assertIn("queue_depth", status)
        self.assertEqual(history["session_id"], session_id)
        self.assertTrue(history["queued_work"])


if __name__ == "__main__":
    unittest.main()
