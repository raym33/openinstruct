import tempfile
import unittest
from pathlib import Path

from openinstruct.config import Settings
from openinstruct.mobile import (
    build_daemon_command,
    build_tailscale_serve_command,
    normalize_publish_path,
    tailnet_url_from_status_payload,
)


class MobileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.settings = Settings(
            provider="ollama",
            model="qwen2.5-coder:14b",
            workdir=self.root,
            approval_policy="auto",
            max_steps=8,
            max_agents=4,
            task_retries=2,
            temperature=0.1,
            memory_backend="sqlite",
            memory_policy="selective",
            home=self.root / ".home",
        )

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_build_daemon_command_uses_effective_settings(self) -> None:
        command = build_daemon_command(self.settings, daemon_command="openinstructd", port=9911)
        self.assertEqual(command[:5], ["openinstructd", "--host", "127.0.0.1", "--port", "9911"])
        self.assertIn("--provider", command)
        self.assertIn("ollama", command)
        self.assertIn("--memory-backend", command)
        self.assertIn("sqlite", command)
        self.assertIn(str(self.root), command)

    def test_build_tailscale_serve_command_uses_local_proxy(self) -> None:
        command = build_tailscale_serve_command(
            tailscale_command="tailscale",
            daemon_port=8765,
            https_port=443,
            path="/mobile",
        )
        self.assertEqual(command[:4], ["tailscale", "serve", "--bg", "--yes"])
        self.assertIn("--set-path=/mobile", command)
        self.assertEqual(command[-1], "http://127.0.0.1:8765")

    def test_normalize_publish_path(self) -> None:
        self.assertEqual(normalize_publish_path("mobile/"), "/mobile")
        self.assertEqual(normalize_publish_path("/"), "/")

    def test_tailnet_url_from_status_payload_uses_dns_name(self) -> None:
        payload = {"Self": {"DNSName": "mini-m4.tail123.ts.net."}}
        url = tailnet_url_from_status_payload(payload, https_port=443, path="/mobile")
        self.assertEqual(url, "https://mini-m4.tail123.ts.net/mobile")
