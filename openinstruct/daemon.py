from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict, dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock, Thread
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

from .agent import AgentRuntime, TerminalUI
from .config import Settings, load_settings
from .memory import MemoryBackendError, build_memory_backend
from .providers import ProviderError, instantiate_provider, select_provider
from .webui import render_mobile_ui


@dataclass
class DaemonEvent:
    kind: str
    message: str
    timestamp: float = field(default_factory=time.time)
    tool: str = ""
    ok: Optional[bool] = None


@dataclass
class DaemonJob:
    job_id: str
    kind: str
    input_text: str
    status: str = "queued"
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    completed_at: float = 0.0
    result: str = ""
    error: str = ""
    events: List[DaemonEvent] = field(default_factory=list)
    exit_requested: bool = False

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["events"] = [asdict(event) for event in self.events]
        return payload


class EventBufferUI(TerminalUI):
    def __init__(self, sink):
        self._sink = sink

    def _emit(self, kind: str, message: str, *, tool: str = "", ok: Optional[bool] = None) -> None:
        self._sink(DaemonEvent(kind=kind, message=message, tool=tool, ok=ok))

    def info(self, message: str) -> None:
        self._emit("info", message)

    def error(self, message: str) -> None:
        self._emit("error", message)

    def tool(self, result) -> None:
        self._emit("tool", result.output, tool=result.tool, ok=result.ok)

    def assistant(self, message: str) -> None:
        self._emit("assistant", message)

    def approval(self, action: str) -> bool:
        self._emit("approval", f"approval required: {action}", ok=False)
        return False


class OpenInstructDaemon:
    def __init__(self, runtime: AgentRuntime):
        self.runtime = runtime
        self.runtime_lock = Lock()
        self.jobs_lock = Lock()
        self.jobs: Dict[str, DaemonJob] = {}

    def state_payload(self) -> Dict[str, Any]:
        return {
            "status": "ok",
            "runtime": self.runtime.status_payload(),
            "job_count": len(self.jobs),
            "jobs": [self._job_summary(job) for job in self.list_jobs()],
        }

    def _job_summary(self, job: DaemonJob) -> Dict[str, Any]:
        return {
            "job_id": job.job_id,
            "kind": job.kind,
            "status": job.status,
            "input_text": job.input_text,
            "created_at": job.created_at,
            "updated_at": job.updated_at,
            "completed_at": job.completed_at,
            "event_count": len(job.events),
            "error": job.error,
        }

    def list_jobs(self) -> List[DaemonJob]:
        with self.jobs_lock:
            return sorted(self.jobs.values(), key=lambda item: item.created_at)

    def get_job(self, job_id: str) -> DaemonJob:
        with self.jobs_lock:
            job = self.jobs.get(job_id)
        if job is None:
            raise KeyError(job_id)
        return job

    def _new_job_id(self, prefix: str) -> str:
        return f"{prefix}_{int(time.time() * 1000)}"

    def _append_event(self, job_id: str, event: DaemonEvent) -> None:
        with self.jobs_lock:
            job = self.jobs.get(job_id)
            if job is None:
                return
            job.events.append(event)
            job.updated_at = time.time()

    def _run_job(self, job_id: str) -> None:
        job = self.get_job(job_id)
        ui = EventBufferUI(lambda event: self._append_event(job_id, event))
        with self.jobs_lock:
            job.status = "running"
            job.updated_at = time.time()
        with self.runtime_lock:
            original_ui = self.runtime.ui
            self.runtime.ui = ui
            try:
                if job.kind == "command":
                    outcome = self.runtime.handle_command(job.input_text)
                    if outcome == "exit":
                        with self.jobs_lock:
                            job.exit_requested = True
                            job.result = "exit requested"
                            job.status = "completed"
                    else:
                        with self.jobs_lock:
                            job.result = "\n".join(event.message for event in job.events if event.kind != "approval").strip()
                            job.status = "completed"
                else:
                    result = self.runtime.run_task(job.input_text)
                    with self.jobs_lock:
                        job.result = result
                        job.status = "completed"
            except Exception as exc:
                with self.jobs_lock:
                    job.error = str(exc)
                    job.status = "failed"
                self._append_event(job_id, DaemonEvent(kind="error", message=str(exc), ok=False))
            finally:
                self.runtime.ui = original_ui
                with self.jobs_lock:
                    job.updated_at = time.time()
                    if job.status in {"completed", "failed"}:
                        job.completed_at = time.time()

    def create_job(self, kind: str, input_text: str) -> DaemonJob:
        clean_kind = kind.strip().lower()
        if clean_kind not in {"prompt", "command"}:
            raise ValueError("kind must be 'prompt' or 'command'")
        clean_input = input_text.strip()
        if not clean_input:
            raise ValueError("input_text must be non-empty")
        job = DaemonJob(job_id=self._new_job_id("job"), kind=clean_kind, input_text=clean_input)
        with self.jobs_lock:
            self.jobs[job.job_id] = job
        worker = Thread(target=self._run_job, args=(job.job_id,), daemon=True)
        worker.start()
        return job

    def create_prompt_job(self, prompt: str) -> DaemonJob:
        return self.create_job("prompt", prompt)

    def create_command_job(self, command: str) -> DaemonJob:
        return self.create_job("command", command)

    def sessions_payload(self) -> Dict[str, Any]:
        return self.runtime.sessions_api.list()

    def session_status_payload(self, session_id: str) -> Dict[str, Any]:
        return self.runtime.sessions_api.status(session_id)

    def session_payload(self, session_id: str, limit: int = 8) -> Dict[str, Any]:
        return self.runtime.sessions_api.history(session_id, limit=limit)

    def spawn_session(
        self,
        prompt: str,
        session_id: str = "",
        write: bool = False,
        visibility: str = "tree",
    ) -> Dict[str, Any]:
        return self.runtime.sessions_api.spawn(prompt, session_id=session_id, write=write, visibility=visibility)

    def send_session_input(self, session_id: str, prompt: str) -> Dict[str, Any]:
        return self.runtime.sessions_api.send(session_id, prompt)


class DaemonHTTPServer(ThreadingHTTPServer):
    def __init__(self, server_address, RequestHandlerClass, daemon_runtime: OpenInstructDaemon):
        super().__init__(server_address, RequestHandlerClass)
        self.daemon_runtime = daemon_runtime


class OpenInstructRequestHandler(BaseHTTPRequestHandler):
    server_version = "openinstructd/0.1"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        return

    @property
    def daemon_runtime(self) -> OpenInstructDaemon:
        return self.server.daemon_runtime  # type: ignore[attr-defined]

    def _send_json(self, status: int, payload: Dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_text(self, status: int, body: str, content_type: str) -> None:
        encoded = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(encoded)

    def _read_json_body(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        if not raw.strip():
            return {}
        return json.loads(raw)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        query = parse_qs(parsed.query)
        try:
            if path == "/":
                self._send_text(HTTPStatus.OK, render_mobile_ui(self.daemon_runtime.state_payload()), "text/html")
                return
            if path == "/health":
                self._send_json(HTTPStatus.OK, {"ok": True, "service": "openinstructd"})
                return
            if path == "/api/state":
                self._send_json(HTTPStatus.OK, self.daemon_runtime.state_payload())
                return
            if path == "/api/jobs":
                self._send_json(
                    HTTPStatus.OK,
                    {"jobs": [self.daemon_runtime._job_summary(job) for job in self.daemon_runtime.list_jobs()]},
                )
                return
            if path.startswith("/api/jobs/"):
                job_id = path.split("/", 3)[-1]
                self._send_json(HTTPStatus.OK, self.daemon_runtime.get_job(job_id).to_dict())
                return
            if path == "/api/sessions":
                self._send_json(HTTPStatus.OK, self.daemon_runtime.sessions_payload())
                return
            if path.startswith("/api/sessions/") and path.endswith("/status"):
                session_id = path.split("/", 4)[-2]
                self._send_json(HTTPStatus.OK, self.daemon_runtime.session_status_payload(session_id))
                return
            if path.startswith("/api/sessions/"):
                session_id = path.split("/", 3)[-1]
                limit = int((query.get("limit") or ["8"])[0])
                self._send_json(HTTPStatus.OK, self.daemon_runtime.session_payload(session_id, limit=limit))
                return
        except KeyError as exc:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": f"not found: {exc.args[0]}"})
            return
        except ValueError as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        self._send_json(HTTPStatus.NOT_FOUND, {"error": f"unknown path: {path}"})

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        try:
            payload = self._read_json_body()
            if path == "/api/jobs":
                kind = str(payload.get("kind") or "prompt")
                input_text = str(payload.get("input") or payload.get("prompt") or payload.get("command") or "").strip()
                job = self.daemon_runtime.create_job(kind, input_text)
                self._send_json(HTTPStatus.ACCEPTED, job.to_dict())
                return
            if path == "/api/sessions":
                prompt = str(payload.get("prompt") or "").strip()
                session_id = str(payload.get("session_id") or "").strip()
                write = bool(payload.get("write", False))
                visibility = str(payload.get("visibility") or "tree").strip() or "tree"
                self._send_json(
                    HTTPStatus.ACCEPTED,
                    self.daemon_runtime.spawn_session(
                        prompt,
                        session_id=session_id,
                        write=write,
                        visibility=visibility,
                    ),
                )
                return
            if path.startswith("/api/sessions/") and path.endswith("/messages"):
                session_id = path.split("/", 4)[-2]
                prompt = str(payload.get("prompt") or payload.get("message") or "").strip()
                self._send_json(HTTPStatus.ACCEPTED, self.daemon_runtime.send_session_input(session_id, prompt))
                return
        except json.JSONDecodeError as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": f"invalid json: {exc}"})
            return
        except KeyError as exc:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": f"not found: {exc.args[0]}"})
            return
        except (ProviderError, MemoryBackendError, ValueError) as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        self._send_json(HTTPStatus.NOT_FOUND, {"error": f"unknown path: {path}"})


def build_runtime(settings: Settings) -> AgentRuntime:
    provider_info = select_provider(
        preference=settings.provider,
        model=settings.model,
        ollama_base_url=settings.ollama_base_url,
        lmstudio_base_url=settings.lmstudio_base_url,
    )
    provider = instantiate_provider(provider_info)
    memory_backend = build_memory_backend(settings, provider_info)
    return AgentRuntime(settings=settings, provider_info=provider_info, provider=provider, memory_backend=memory_backend)


def serve(settings: Settings, host: str = "127.0.0.1", port: int = 8765) -> DaemonHTTPServer:
    runtime = build_runtime(settings)
    daemon_runtime = OpenInstructDaemon(runtime)
    return DaemonHTTPServer((host, port), OpenInstructRequestHandler, daemon_runtime)


def command_daemon(settings: Settings, host: str = "127.0.0.1", port: int = 8765) -> int:
    server = serve(settings=settings, host=host, port=port)
    address = server.server_address
    print(f"openinstructd listening on http://{address[0]}:{address[1]}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()
    finally:
        server.server_close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="openinstructd", description="Daemon local HTTP para OpenInstruct")
    parser.add_argument("--provider", default=None, choices=["auto", "ollama", "lmstudio"])
    parser.add_argument("--model", default=None)
    parser.add_argument("--memory-backend", default=None, choices=["none", "mem0", "sqlite"])
    parser.add_argument("--memory-policy", default=None, choices=["none", "selective", "all"])
    parser.add_argument("--workdir", default=None)
    parser.add_argument("--approval-policy", default=None, choices=["ask", "auto", "deny"])
    parser.add_argument("--ollama-url", default=None)
    parser.add_argument("--lmstudio-url", default=None)
    parser.add_argument("--max-steps", default=None, type=int)
    parser.add_argument("--max-agents", default=None, type=int)
    parser.add_argument("--task-retries", default=None, type=int)
    parser.add_argument("--temperature", default=None, type=float)
    parser.add_argument("--session", default=None)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8765, type=int)
    return parser


def _settings_from_args(args: argparse.Namespace) -> Settings:
    overrides = {
        "provider": args.provider,
        "model": args.model,
        "memory_backend": args.memory_backend,
        "memory_policy": args.memory_policy,
        "workdir": args.workdir,
        "approval_policy": args.approval_policy,
        "ollama_base_url": args.ollama_url,
        "lmstudio_base_url": args.lmstudio_url,
        "max_steps": args.max_steps,
        "max_agents": args.max_agents,
        "task_retries": args.task_retries,
        "temperature": args.temperature,
        "session": args.session,
    }
    return load_settings(overrides=overrides)


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        settings = _settings_from_args(args)
        return command_daemon(settings, host=args.host, port=args.port)
    except (ProviderError, MemoryBackendError) as exc:
        parser.exit(status=2, message=f"runtime error: {exc}\n")
