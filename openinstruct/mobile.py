from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from .config import Settings


def normalize_publish_path(path: str) -> str:
    clean = (path or "/").strip() or "/"
    if not clean.startswith("/"):
        clean = f"/{clean}"
    if clean != "/" and clean.endswith("/"):
        clean = clean.rstrip("/")
    return clean


def build_daemon_command(
    settings: Settings,
    *,
    daemon_command: str = "openinstructd",
    host: str = "127.0.0.1",
    port: int = 8765,
) -> List[str]:
    command = [
        daemon_command,
        "--host",
        host,
        "--port",
        str(port),
        "--provider",
        settings.provider,
        "--model",
        settings.model,
        "--memory-backend",
        settings.memory_backend,
        "--memory-policy",
        settings.memory_policy,
        "--workdir",
        str(settings.workdir),
        "--approval-policy",
        settings.approval_policy,
        "--ollama-url",
        settings.ollama_base_url,
        "--lmstudio-url",
        settings.lmstudio_base_url,
        "--max-steps",
        str(settings.max_steps),
        "--max-agents",
        str(settings.max_agents),
        "--task-retries",
        str(settings.task_retries),
        "--temperature",
        str(settings.temperature),
    ]
    if settings.session:
        command.extend(["--session", settings.session])
    return command


def build_tailscale_serve_command(
    *,
    tailscale_command: str = "tailscale",
    daemon_port: int = 8765,
    https_port: int = 443,
    path: str = "/",
) -> List[str]:
    command = [
        tailscale_command,
        "serve",
        "--bg",
        "--yes",
        f"--https={https_port}",
    ]
    publish_path = normalize_publish_path(path)
    if publish_path != "/":
        command.append(f"--set-path={publish_path}")
    command.append(f"http://127.0.0.1:{daemon_port}")
    return command


def tailnet_url_from_status_payload(payload: Dict[str, Any], *, https_port: int = 443, path: str = "/") -> str:
    self_payload = payload.get("Self") if isinstance(payload, dict) else {}
    dns_name = ""
    if isinstance(self_payload, dict):
        dns_name = str(self_payload.get("DNSName") or "").rstrip(".")
    if not dns_name:
        return ""
    base = f"https://{dns_name}" if https_port == 443 else f"https://{dns_name}:{https_port}"
    publish_path = normalize_publish_path(path)
    return base if publish_path == "/" else f"{base}{publish_path}"


def _daemon_health_url(port: int) -> str:
    return f"http://127.0.0.1:{port}/health"


def daemon_is_healthy(port: int, timeout: float = 0.7) -> bool:
    try:
        with urlopen(_daemon_health_url(port), timeout=timeout) as response:
            return int(getattr(response, "status", 0)) == 200
    except (HTTPError, URLError, OSError):
        return False


def wait_for_daemon(port: int, timeout: float = 15.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if daemon_is_healthy(port):
            return True
        time.sleep(0.2)
    return False


def _run_command(command: List[str], *, capture_output: bool = True) -> subprocess.CompletedProcess[str]:
    binary = shutil.which(command[0])
    if binary is None:
        raise RuntimeError(f"required command not found: {command[0]}")
    result = subprocess.run(command, text=True, capture_output=capture_output, check=False)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"command failed ({result.returncode}): {' '.join(command)}\n{detail}".strip())
    return result


def command_mobile_publish(
    settings: Settings,
    *,
    port: int = 8765,
    https_port: int = 443,
    path: str = "/",
    daemon_command: str = "openinstructd",
    tailscale_command: str = "tailscale",
    no_start_daemon: bool = False,
    reset: bool = False,
) -> int:
    mobile_dir = settings.home / "mobile-ui"
    mobile_dir.mkdir(parents=True, exist_ok=True)
    metadata_path = mobile_dir / "publish.json"
    daemon_log_path = mobile_dir / "openinstructd.log"
    publish_path = normalize_publish_path(path)

    if reset:
        _run_command([tailscale_command, "serve", "reset"])

    daemon_started = False
    daemon_pid = 0
    daemon_process: subprocess.Popen[str] | None = None

    if not daemon_is_healthy(port):
        if no_start_daemon:
            raise RuntimeError(f"openinstructd is not reachable on {_daemon_health_url(port)}")
        daemon_cmd = build_daemon_command(settings, daemon_command=daemon_command, port=port)
        log_handle = daemon_log_path.open("a", encoding="utf-8")
        daemon_process = subprocess.Popen(
            daemon_cmd,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            text=True,
        )
        log_handle.close()
        daemon_started = True
        daemon_pid = int(daemon_process.pid or 0)
        if not wait_for_daemon(port, timeout=20.0):
            try:
                daemon_process.kill()
                daemon_process.wait(timeout=5.0)
            except OSError:
                pass
            raise RuntimeError(
                f"openinstructd did not become healthy on {_daemon_health_url(port)}; inspect {daemon_log_path}"
            )

    serve_command = build_tailscale_serve_command(
        tailscale_command=tailscale_command,
        daemon_port=port,
        https_port=https_port,
        path=publish_path,
    )
    _run_command(serve_command)

    serve_status_text = ""
    try:
        serve_status_text = _run_command([tailscale_command, "serve", "status", "--json"]).stdout.strip()
    except RuntimeError:
        serve_status_text = _run_command([tailscale_command, "serve", "status"]).stdout.strip()

    tailnet_status: Dict[str, Any] = {}
    try:
        status_text = _run_command([tailscale_command, "status", "--json"]).stdout.strip()
        tailnet_status = json.loads(status_text) if status_text else {}
    except (RuntimeError, json.JSONDecodeError):
        tailnet_status = {}

    tailnet_url = tailnet_url_from_status_payload(tailnet_status, https_port=https_port, path=publish_path)
    payload = {
        "published_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "local_url": f"http://127.0.0.1:{port}{publish_path}",
        "tailnet_url": tailnet_url,
        "publish_path": publish_path,
        "daemon_port": port,
        "https_port": https_port,
        "daemon_started": daemon_started,
        "daemon_pid": daemon_pid,
        "daemon_log_path": str(daemon_log_path),
        "metadata_path": str(metadata_path),
        "workdir": str(settings.workdir),
        "serve_command": serve_command,
        "serve_status": serve_status_text,
    }
    metadata_path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")

    print("mobile ui published")
    print(f"local_url={payload['local_url']}")
    if tailnet_url:
        print(f"tailnet_url={tailnet_url}")
    else:
        print("tailnet_url=(unavailable; run `tailscale status --json` or `tailscale serve status` locally)")
    print(f"publish_path={publish_path}")
    print(f"metadata={metadata_path}")
    print(f"daemon_log={daemon_log_path}")
    print(f"daemon_started={daemon_started}")
    if daemon_pid:
        print(f"daemon_pid={daemon_pid}")
    return 0
