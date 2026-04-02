from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from .config import sanitize_session_name


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SessionStore:
    def __init__(self, home: Path):
        self.home = home.expanduser().resolve()
        self.sessions_dir = self.home / "sessions"

    def default_name(self) -> str:
        return datetime.now().strftime("%Y%m%d-%H%M%S")

    def path_for(self, name: str) -> Path:
        safe_name = sanitize_session_name(name)
        return self.sessions_dir / f"{safe_name}.json"

    def save(self, name: str, payload: Dict[str, Any]) -> Path:
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        path = self.path_for(name)
        data = dict(payload)
        data["updated_at"] = utc_now()
        if "created_at" not in data:
            data["created_at"] = utc_now()
        path.write_text(json.dumps(data, indent=2, ensure_ascii=True), encoding="utf-8")
        return path

    def load(self, name: str) -> Dict[str, Any]:
        path = self.path_for(name)
        if not path.exists():
            raise FileNotFoundError(f"Session '{name}' does not exist.")
        return json.loads(path.read_text(encoding="utf-8"))

    def list(self) -> List[str]:
        if not self.sessions_dir.exists():
            return []
        paths = sorted(self.sessions_dir.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True)
        return [path.stem for path in paths]
