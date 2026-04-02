from __future__ import annotations

import argparse
import json
import shutil
from typing import Dict, Optional

from .agent import AgentRuntime
from .config import Settings, config_path, init_config, load_settings
from .daemon import command_daemon
from .knowledge import ingest_sources, init_knowledge_base, render_ingest_summary, render_knowledge_status
from .memory import MemoryBackendError, build_memory_backend
from .mobile import command_mobile_publish
from .providers import ProviderError, ProviderInfo, available_providers, instantiate_provider, select_provider
from .session import SessionStore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="openinstruct", description="CLI local para agentes LLM con Ollama o LM Studio")
    subparsers = parser.add_subparsers(dest="command")

    common_parent = argparse.ArgumentParser(add_help=False)
    common_parent.add_argument("--provider", default=None, choices=["auto", "ollama", "lmstudio"])
    common_parent.add_argument("--model", default=None)
    common_parent.add_argument("--memory-backend", default=None, choices=["none", "mem0", "sqlite"])
    common_parent.add_argument("--memory-policy", default=None, choices=["none", "selective", "all"])
    common_parent.add_argument("--workdir", default=None)
    common_parent.add_argument("--approval-policy", default=None, choices=["ask", "auto", "deny"])
    common_parent.add_argument("--ollama-url", default=None)
    common_parent.add_argument("--lmstudio-url", default=None)
    common_parent.add_argument("--max-steps", default=None, type=int)
    common_parent.add_argument("--max-agents", default=None, type=int)
    common_parent.add_argument("--task-retries", default=None, type=int)
    common_parent.add_argument("--temperature", default=None, type=float)
    common_parent.add_argument("--session", default=None)

    subparsers.add_parser("chat", parents=[common_parent], help="abre un REPL interactivo")

    run_parser = subparsers.add_parser("run", parents=[common_parent], help="ejecuta una tarea one-shot")
    run_parser.add_argument("task", nargs="+")
    resume_parser = subparsers.add_parser("resume-checkpoint", parents=[common_parent], help="reanuda un DAG desde un checkpoint")
    resume_parser.add_argument("run_id")

    subparsers.add_parser("doctor", parents=[common_parent], help="verifica proveedores y modelos")
    subparsers.add_parser("sessions", parents=[common_parent], help="lista sesiones guardadas")
    daemon_parser = subparsers.add_parser("daemon", parents=[common_parent], help="expone openinstructd por HTTP")
    daemon_parser.add_argument("--host", default="127.0.0.1")
    daemon_parser.add_argument("--port", default=8765, type=int)
    mobile_parser = subparsers.add_parser("mobile", help="utilidades para publicar la UI movil")
    mobile_subparsers = mobile_parser.add_subparsers(dest="mobile_command")
    mobile_publish = mobile_subparsers.add_parser(
        "publish",
        parents=[common_parent],
        help="arranca openinstructd y lo expone por tailscale serve",
    )
    mobile_publish.add_argument("--port", default=8765, type=int)
    mobile_publish.add_argument("--https-port", default=443, type=int)
    mobile_publish.add_argument("--path", default="/")
    mobile_publish.add_argument("--daemon-command", default="openinstructd")
    mobile_publish.add_argument("--tailscale-command", default="tailscale")
    mobile_publish.add_argument("--no-start-daemon", action="store_true")
    mobile_publish.add_argument("--reset", action="store_true")

    kb_parser = subparsers.add_parser("kb", help="operaciones de knowledge base sobre raw/wiki/outputs")
    kb_subparsers = kb_parser.add_subparsers(dest="kb_command")
    kb_init = kb_subparsers.add_parser("init", help="crea la estructura raw/wiki/outputs")
    kb_init.add_argument("--name", default="")
    kb_init.add_argument("--workdir", default=None)
    kb_subparsers.add_parser("status", parents=[common_parent], help="muestra el estado de la knowledge base")
    kb_subparsers.add_parser("ingest", parents=[common_parent], help="actualiza el manifiesto incremental de raw/")
    kb_compile = kb_subparsers.add_parser("compile", parents=[common_parent], help="compila raw/ en wiki/")
    kb_compile.add_argument("scope", nargs="*")
    kb_ask = kb_subparsers.add_parser("ask", parents=[common_parent], help="responde una pregunta y la archiva en markdown")
    kb_ask.add_argument("question", nargs="+")
    kb_ask.add_argument("--output", default="")
    kb_ask.add_argument("--format", default="markdown", choices=["markdown", "marp"])
    kb_lint = kb_subparsers.add_parser("lint", parents=[common_parent], help="audita la wiki")
    kb_lint.add_argument("--fix", action="store_true")

    config_parser = subparsers.add_parser("config", help="gestiona la configuración base")
    config_subparsers = config_parser.add_subparsers(dest="config_command")
    config_subparsers.add_parser("path", help="muestra la ruta del config")
    config_subparsers.add_parser("init", help="crea ~/.openinstruct/config.json si no existe")
    config_subparsers.add_parser("show", help="muestra la configuración efectiva")

    return parser


def _settings_from_args(args: argparse.Namespace) -> Settings:
    overrides: Dict[str, object] = {
        "provider": getattr(args, "provider", None),
        "model": getattr(args, "model", None),
        "memory_backend": getattr(args, "memory_backend", None),
        "memory_policy": getattr(args, "memory_policy", None),
        "workdir": getattr(args, "workdir", None),
        "approval_policy": getattr(args, "approval_policy", None),
        "ollama_base_url": getattr(args, "ollama_url", None),
        "lmstudio_base_url": getattr(args, "lmstudio_url", None),
        "max_steps": getattr(args, "max_steps", None),
        "max_agents": getattr(args, "max_agents", None),
        "task_retries": getattr(args, "task_retries", None),
        "temperature": getattr(args, "temperature", None),
        "session": getattr(args, "session", None),
    }
    return load_settings(overrides=overrides)


def _build_runtime(settings: Settings) -> AgentRuntime:
    info = select_provider(
        preference=settings.provider,
        model=settings.model,
        ollama_base_url=settings.ollama_base_url,
        lmstudio_base_url=settings.lmstudio_base_url,
    )
    provider = instantiate_provider(info)
    memory_backend = build_memory_backend(settings, info)
    return AgentRuntime(settings=settings, provider_info=info, provider=provider, memory_backend=memory_backend)


def command_doctor(settings: Settings) -> int:
    print("local tools:")
    print(f"  git: {'OK' if shutil.which('git') else 'missing'}")
    print(f"  rg: {'OK' if shutil.which('rg') else 'missing'}")
    if settings.memory_backend != "none":
        try:
            provider_info = ProviderInfo(
                name=settings.provider if settings.provider in {"ollama", "lmstudio"} else "ollama",
                base_url=settings.ollama_base_url,
                model=settings.model,
            )
            if settings.memory_backend == "mem0":
                provider_info = select_provider(
                    preference=settings.provider,
                    model=settings.model,
                    ollama_base_url=settings.ollama_base_url,
                    lmstudio_base_url=settings.lmstudio_base_url,
                )
            backend = build_memory_backend(
                settings,
                provider_info,
            )
            print(f"memory backend: {backend.describe()}")
            print(f"memory policy: {settings.memory_policy}")
        except (ProviderError, MemoryBackendError) as exc:
            print("memory backend: unavailable")
            print(f"  configured: {settings.memory_backend}")
            print(f"  error: {exc}")
    else:
        print(f"memory backend: {settings.memory_backend}")
        print(f"memory policy: {settings.memory_policy}")
    for provider in available_providers(settings.ollama_base_url, settings.lmstudio_base_url):
        try:
            models = provider.list_models()
            print(f"{provider.name}: OK")
            print(f"  url: {provider.base_url}")
            print(f"  models: {', '.join(models) if models else '(none)'}")
            if settings.model:
                try:
                    selected = provider.resolve_model(settings.model)
                except ProviderError as exc:
                    selected = f"requested model unavailable ({exc})"
            else:
                selected = models[0] if models else "(none)"
            print(f"  default: {selected}")
        except ProviderError as exc:
            print(f"{provider.name}: unavailable")
            print(f"  url: {provider.base_url}")
            print(f"  error: {exc}")
    return 0


def command_sessions(settings: Settings) -> int:
    sessions = SessionStore(settings.home).list()
    if not sessions:
        print("(no sessions)")
        return 0
    for session in sessions:
        print(session)
    return 0


def command_config(args: argparse.Namespace) -> int:
    settings = load_settings()
    if args.config_command == "path":
        print(config_path(settings.home))
        return 0
    if args.config_command == "init":
        path = init_config(settings.home)
        print(path)
        return 0
    if args.config_command == "show":
        print(json.dumps(settings.to_dict(), indent=2, ensure_ascii=True))
        return 0
    raise SystemExit("Use one of: path, init, show")


def command_kb(args: argparse.Namespace) -> int:
    kb_command = args.kb_command
    if kb_command == "init":
        settings = load_settings(overrides={"workdir": getattr(args, "workdir", None)})
        payload = init_knowledge_base(settings.workdir, name=args.name)
        print(
            "knowledge base initialized\n"
            f"root={payload['root']}\n"
            f"config={payload['config_path']}\n"
            f"manifest={payload['manifest_path']}\n"
            f"raw={payload['raw_dir']}\n"
            f"wiki={payload['wiki_dir']}\n"
            f"outputs={payload['outputs_dir']}"
        )
        return 0

    settings = _settings_from_args(args)
    if kb_command == "status":
        print(render_knowledge_status(settings.workdir))
        return 0
    if kb_command == "ingest":
        payload = ingest_sources(settings.workdir)
        print(render_ingest_summary(settings.workdir, payload))
        return 0
    runtime = _build_runtime(settings)
    if kb_command == "compile":
        print(runtime.run_knowledge_compile(scope=" ".join(args.scope).strip()))
        return 0
    if kb_command == "ask":
        print(
            runtime.run_knowledge_ask(
                " ".join(args.question),
                output_path=args.output,
                output_format=args.format,
            )
        )
        return 0
    if kb_command == "lint":
        print(runtime.run_knowledge_lint(fix=bool(args.fix)))
        return 0
    raise SystemExit("Use one of: init, status, ingest, compile, ask, lint")


def command_mobile(args: argparse.Namespace) -> int:
    if args.mobile_command != "publish":
        raise SystemExit("Use: openinstruct mobile publish")
    settings = _settings_from_args(args)
    return command_mobile_publish(
        settings,
        port=args.port,
        https_port=args.https_port,
        path=args.path,
        daemon_command=args.daemon_command,
        tailscale_command=args.tailscale_command,
        no_start_daemon=bool(args.no_start_daemon),
        reset=bool(args.reset),
    )


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    command = args.command or "chat"

    if command == "config":
        return command_config(args)
    if command == "kb":
        return command_kb(args)
    if command == "mobile":
        return command_mobile(args)

    settings = _settings_from_args(args)

    try:
        if command == "doctor":
            return command_doctor(settings)
        if command == "sessions":
            return command_sessions(settings)
        if command == "daemon":
            return command_daemon(settings, host=args.host, port=args.port)
        if command == "run":
            runtime = _build_runtime(settings)
            result = runtime.run_task(" ".join(args.task))
            print(result)
            return 0
        if command == "resume-checkpoint":
            runtime = _build_runtime(settings)
            result = runtime.resume_from_checkpoint(args.run_id, max_agents=settings.max_agents)
            print(result.output)
            return 0 if result.ok else 1
        if command == "chat":
            runtime = _build_runtime(settings)
            return runtime.repl()
    except (ProviderError, MemoryBackendError) as exc:
        parser.exit(status=2, message=f"runtime error: {exc}\n")

    parser.print_help()
    return 0
