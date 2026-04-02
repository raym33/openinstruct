from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Optional

DEFAULT_HOME = Path.home() / ".openinstruct"
DEFAULT_CONFIG = {
    "provider": "auto",
    "model": "",
    "ollama_base_url": "http://127.0.0.1:11434",
    "lmstudio_base_url": "http://127.0.0.1:1234/v1",
    "memory_backend": "none",
    "memory_policy": "selective",
    "workdir": str(Path.cwd()),
    "approval_policy": "ask",
    "max_steps": 8,
    "max_agents": 3,
    "task_retries": 1,
    "temperature": 0.2,
    "session": "",
}


@dataclass
class Settings:
    provider: str = "auto"
    model: str = ""
    ollama_base_url: str = "http://127.0.0.1:11434"
    lmstudio_base_url: str = "http://127.0.0.1:1234/v1"
    memory_backend: str = "none"
    memory_policy: str = "selective"
    workdir: Path = Path.cwd()
    approval_policy: str = "ask"
    max_steps: int = 8
    max_agents: int = 3
    task_retries: int = 1
    temperature: float = 0.2
    session: str = ""
    home: Path = DEFAULT_HOME

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["workdir"] = str(self.workdir)
        data["home"] = str(self.home)
        return data


def _read_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in config file: {path}") from exc


def _read_env() -> Dict[str, Any]:
    env = {
        "provider": os.getenv("OPENINSTRUCT_PROVIDER"),
        "model": os.getenv("OPENINSTRUCT_MODEL"),
        "ollama_base_url": os.getenv("OPENINSTRUCT_OLLAMA_URL"),
        "lmstudio_base_url": os.getenv("OPENINSTRUCT_LMSTUDIO_URL"),
        "memory_backend": os.getenv("OPENINSTRUCT_MEMORY_BACKEND"),
        "memory_policy": os.getenv("OPENINSTRUCT_MEMORY_POLICY"),
        "workdir": os.getenv("OPENINSTRUCT_WORKDIR"),
        "approval_policy": os.getenv("OPENINSTRUCT_APPROVAL_POLICY"),
        "max_steps": os.getenv("OPENINSTRUCT_MAX_STEPS"),
        "max_agents": os.getenv("OPENINSTRUCT_MAX_AGENTS"),
        "task_retries": os.getenv("OPENINSTRUCT_TASK_RETRIES"),
        "temperature": os.getenv("OPENINSTRUCT_TEMPERATURE"),
        "session": os.getenv("OPENINSTRUCT_SESSION"),
        "home": os.getenv("OPENINSTRUCT_HOME"),
    }
    return {key: value for key, value in env.items() if value not in (None, "")}


def _coerce_types(data: Dict[str, Any], home: Path) -> Settings:
    normalized = dict(DEFAULT_CONFIG)
    normalized.update(data)
    normalized["workdir"] = Path(normalized["workdir"]).expanduser().resolve()
    normalized["home"] = Path(normalized.get("home") or home).expanduser().resolve()
    normalized["max_steps"] = int(normalized["max_steps"])
    normalized["max_agents"] = int(normalized["max_agents"])
    normalized["task_retries"] = int(normalized["task_retries"])
    if normalized["task_retries"] < 0:
        raise ValueError("task_retries must be >= 0")
    normalized["memory_backend"] = str(normalized["memory_backend"]).strip().lower()
    if normalized["memory_backend"] not in {"none", "mem0", "sqlite"}:
        raise ValueError("memory_backend must be one of: none, mem0, sqlite")
    normalized["memory_policy"] = str(normalized["memory_policy"]).strip().lower()
    if normalized["memory_policy"] not in {"none", "selective", "all"}:
        raise ValueError("memory_policy must be one of: none, selective, all")
    normalized["temperature"] = float(normalized["temperature"])
    return Settings(**normalized)


def load_settings(overrides: Optional[Dict[str, Any]] = None, home: Optional[Path] = None) -> Settings:
    override_home = None
    if overrides:
        override_home = overrides.get("home")
    env_home = os.getenv("OPENINSTRUCT_HOME")
    home_path = Path(home or override_home or env_home or DEFAULT_HOME).expanduser()
    config_path = home_path / "config.json"
    merged: Dict[str, Any] = {}
    merged.update(_read_json(config_path))
    merged.update(_read_env())
    if overrides:
        merged.update({key: value for key, value in overrides.items() if value is not None})
    return _coerce_types(merged, home_path)


def init_config(home: Optional[Path] = None) -> Path:
    home_path = (home or DEFAULT_HOME).expanduser()
    home_path.mkdir(parents=True, exist_ok=True)
    config_path = home_path / "config.json"
    if not config_path.exists():
        config_path.write_text(json.dumps(DEFAULT_CONFIG, indent=2), encoding="utf-8")
    return config_path


def config_path(home: Optional[Path] = None) -> Path:
    home_path = (home or DEFAULT_HOME).expanduser()
    return home_path / "config.json"


def sanitize_session_name(value: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip())
    return clean.strip("-") or "session"
