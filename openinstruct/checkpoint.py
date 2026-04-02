from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from .config import sanitize_session_name


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class CheckpointStore:
    def __init__(self, home: Path):
        self.home = home.expanduser().resolve()
        self.checkpoints_dir = self.home / "checkpoints"

    def new_run_id(self, prefix: str = "dag") -> str:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        return f"{sanitize_session_name(prefix)}-{stamp}"

    def path_for(self, run_id: str) -> Path:
        safe_id = sanitize_session_name(run_id)
        return self.checkpoints_dir / f"{safe_id}.json"

    def save(self, run_id: str, payload: Dict[str, Any]) -> Path:
        self.checkpoints_dir.mkdir(parents=True, exist_ok=True)
        path = self.path_for(run_id)
        data = dict(payload)
        data["updated_at"] = utc_now()
        if "created_at" not in data:
            data["created_at"] = utc_now()
        path.write_text(json.dumps(data, indent=2, ensure_ascii=True), encoding="utf-8")
        return path

    def load(self, run_id: str) -> Dict[str, Any]:
        path = self.path_for(run_id)
        if not path.exists():
            raise FileNotFoundError(f"Checkpoint run '{run_id}' does not exist.")
        return json.loads(path.read_text(encoding="utf-8"))

    def list(self) -> List[str]:
        if not self.checkpoints_dir.exists():
            return []
        paths = sorted(self.checkpoints_dir.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True)
        return [path.stem for path in paths]
