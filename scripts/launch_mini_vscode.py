#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
EXTENSION_SOURCE = REPO_ROOT / "apps" / "vscode-extension"
DEFAULT_PROFILE = Path.home() / ".openinstruct" / "studio" / "default"
APP_CANDIDATES = [
    Path("/Applications/Code - OSS.app"),
    Path("/Applications/Visual Studio Code.app"),
    Path("/Applications/VSCodium.app"),
]


def detect_app(explicit: str = "") -> Path:
    if explicit:
        path = Path(explicit).expanduser().resolve()
        if not path.exists():
            raise FileNotFoundError(f"App not found: {path}")
        return path
    for candidate in APP_CANDIDATES:
        if candidate.exists():
            return candidate
    raise FileNotFoundError("No Code - OSS / VS Code app found in /Applications")


def ensure_extension_link(target_dir: Path) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    destination = target_dir / "openinstruct-local"
    if destination.is_symlink() and destination.resolve() == EXTENSION_SOURCE.resolve():
        return
    if destination.exists():
        if destination.is_symlink() or destination.is_file():
            destination.unlink()
        else:
            shutil.rmtree(destination)
    try:
        destination.symlink_to(EXTENSION_SOURCE, target_is_directory=True)
    except OSError:
        shutil.copytree(EXTENSION_SOURCE, destination)


def write_settings(profile_dir: Path, args: argparse.Namespace) -> Path:
    user_dir = profile_dir / "User"
    user_dir.mkdir(parents=True, exist_ok=True)
    settings_path = user_dir / "settings.json"
    settings = {
        "window.titleBarStyle": "custom",
        "workbench.startupEditor": "none",
        "workbench.activityBar.location": "left",
        "openinstruct.autoStart": True,
        "openinstruct.server.command": args.daemon_command,
        "openinstruct.server.args": args.daemon_args,
        "openinstruct.server.host": args.host,
        "openinstruct.server.port": args.port,
        "openinstruct.provider": args.provider,
        "openinstruct.model": args.model,
        "openinstruct.approvalPolicy": args.approval_policy,
        "openinstruct.memoryBackend": args.memory_backend,
        "openinstruct.memoryPolicy": args.memory_policy,
        "openinstruct.maxAgents": args.max_agents,
        "openinstruct.taskRetries": args.task_retries,
    }
    settings_path.write_text(json.dumps(settings, indent=2, ensure_ascii=True), encoding="utf-8")
    return settings_path


def launch_app(app_path: Path, profile_dir: Path, workspace: Path) -> None:
    extensions_dir = profile_dir / "extensions"
    command = [
        "open",
        "-na",
        str(app_path),
        "--args",
        "--user-data-dir",
        str(profile_dir),
        "--extensions-dir",
        str(extensions_dir),
        str(workspace),
    ]
    subprocess.run(command, check=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Lanza un mini VS Code local con OpenInstruct integrado")
    parser.add_argument("--app", default="", help="Ruta a Code - OSS.app, Visual Studio Code.app o VSCodium.app")
    parser.add_argument("--workspace", default=str(Path.cwd()), help="Workspace a abrir")
    parser.add_argument("--profile-dir", default=str(DEFAULT_PROFILE), help="Directorio de perfil portable")
    parser.add_argument("--provider", default="auto", choices=["auto", "ollama", "lmstudio"])
    parser.add_argument("--model", default="")
    parser.add_argument("--approval-policy", default="ask", choices=["ask", "auto", "deny"])
    parser.add_argument("--memory-backend", default="none", choices=["none", "mem0", "sqlite"])
    parser.add_argument("--memory-policy", default="selective", choices=["none", "selective", "all"])
    parser.add_argument("--max-agents", default=3, type=int)
    parser.add_argument("--task-retries", default=1, type=int)
    parser.add_argument("--daemon-command", default="openinstructd")
    parser.add_argument("--daemon-args", action="append", default=[])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8765, type=int)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    app_path = detect_app(args.app)
    workspace = Path(args.workspace).expanduser().resolve()
    profile_dir = Path(args.profile_dir).expanduser().resolve()
    if not workspace.exists():
        parser.error(f"Workspace not found: {workspace}")
    extensions_dir = profile_dir / "extensions"
    ensure_extension_link(extensions_dir)
    settings_path = write_settings(profile_dir, args)
    print(f"Using app: {app_path}")
    print(f"Profile: {profile_dir}")
    print(f"Settings: {settings_path}")
    launch_app(app_path, profile_dir, workspace)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
