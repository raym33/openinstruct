import json
import http.client
import tempfile
import time
import unittest
from pathlib import Path
from threading import Thread

from openinstruct.agent import AgentRuntime
from openinstruct.config import Settings
from openinstruct.daemon import DaemonHTTPServer, OpenInstructDaemon, OpenInstructRequestHandler
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

    def start_http_server(self):
        server = DaemonHTTPServer(("127.0.0.1", 0), OpenInstructRequestHandler, self.daemon)
        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()
        return server, worker

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

    def test_root_route_serves_mobile_html(self) -> None:
        server, worker = self.start_http_server()
        try:
            host, port = server.server_address
            connection = http.client.HTTPConnection(host, port, timeout=3.0)
            connection.request("GET", "/")
            response = connection.getresponse()
            body = response.read().decode("utf-8")
            self.assertEqual(response.status, 200)
            self.assertIn("text/html", response.getheader("Content-Type", ""))
            self.assertIn("OpenInstruct Mobile", body)
            self.assertIn("/api/jobs", body)
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=2.0)

    def test_http_sessions_spawn_accepts_visibility(self) -> None:
        server, worker = self.start_http_server()
        try:
            host, port = server.server_address
            connection = http.client.HTTPConnection(host, port, timeout=3.0)
            payload = json.dumps({"prompt": "review auth layer", "visibility": "self"})
            connection.request("POST", "/api/sessions", body=payload, headers={"Content-Type": "application/json"})
            response = connection.getresponse()
            body = json.loads(response.read().decode("utf-8"))
            self.assertEqual(response.status, 202)
            self.assertEqual(body["visibility"], "self")
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=2.0)


if __name__ == "__main__":
    unittest.main()
