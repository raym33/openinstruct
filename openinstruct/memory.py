from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import getpass
import os
from pathlib import Path
import re
import sqlite3
import sys
from typing import Any, Dict, Iterable, List, Optional

from .config import Settings
from .providers import ProviderInfo


class MemoryBackendError(RuntimeError):
    pass


@dataclass
class MemoryRecord:
    text: str
    source: str = ""
    created_at: str = ""
    score: float = 0.0
    backend: str = ""
    session_name: str = ""
    agent_label: str = ""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_text(value: str, *, limit: int = 600) -> str:
    text = " ".join(str(value).strip().split())
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def _split_sentences(text: str) -> List[str]:
    chunks = re.split(r"(?<=[.!?])\s+", text.strip())
    return [chunk.strip(" -") for chunk in chunks if chunk.strip()]


def _candidate_lines(text: str) -> List[str]:
    lines = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        line = re.sub(r"^[-*+]\s+", "", line)
        line = re.sub(r"^\d+\.\s+", "", line)
        if line:
            lines.append(line)
    return lines


def extract_memory_facts(user_text: str, assistant_text: str, *, limit: int = 5) -> List[str]:
    durable_keywords = (
        "prefer",
        "prefers",
        "workflow",
        "convention",
        "decision",
        "remember",
        "always",
        "never",
        "path",
        "directory",
        "provider",
        "model",
        "workspace",
        "repo",
        "wiki",
        "raw/",
        "outputs/",
        "obsidian",
        "tailscale",
        "mac",
        "session",
        "memory",
        "knowledge",
        "kb",
        ".md",
        "should",
        "must",
        "use ",
        "uses ",
    )
    candidates: List[str] = []
    candidates.extend(_candidate_lines(assistant_text))
    if not candidates:
        candidates.extend(_split_sentences(assistant_text))

    facts: List[str] = []
    seen = set()
    for candidate in candidates:
        normalized = _normalize_text(candidate, limit=280)
        lower = normalized.lower()
        if len(normalized) < 20:
            continue
        if not any(keyword in lower for keyword in durable_keywords):
            continue
        if lower in seen:
            continue
        seen.add(lower)
        facts.append(normalized)
        if len(facts) >= limit:
            break

    user_lower = user_text.lower()
    if any(token in user_lower for token in ("remember", "recuerda", "prefiero", "prefer", "siempre", "never")):
        reminder = _normalize_text(user_text, limit=280)
        if reminder.lower() not in seen:
            facts.insert(0, f"User preference: {reminder}")

    return facts[:limit]


def render_memory_records(records: Iterable[MemoryRecord], *, title: str = "") -> str:
    items = list(records)
    if not items:
        return "(no memories)"
    lines: List[str] = []
    if title:
        lines.append(title)
        lines.append("")
    for index, record in enumerate(items, start=1):
        meta: List[str] = []
        if record.created_at:
            meta.append(record.created_at)
        if record.source:
            meta.append(record.source)
        if record.score:
            meta.append(f"score={record.score:.2f}")
        if record.session_name:
            meta.append(f"session={record.session_name}")
        lines.append(f"[{index}] {record.text}")
        if meta:
            lines.append(f"  {' | '.join(meta)}")
    return "\n".join(lines)


class BaseMemoryBackend:
    name = "none"

    def describe(self) -> str:
        return self.name

    def enabled(self) -> bool:
        return False

    def search(self, query: str, *, session_name: str, agent_label: str, limit: int = 5) -> List[MemoryRecord]:
        return []

    def recent(self, *, session_name: str, agent_label: str, limit: int = 8) -> List[MemoryRecord]:
        return []

    def recall(self, query: str, *, session_name: str, agent_label: str) -> List[str]:
        return [record.text for record in self.search(query, session_name=session_name, agent_label=agent_label)]

    def store(self, user_text: str, assistant_text: str, *, session_name: str, agent_label: str) -> None:
        return


class NullMemoryBackend(BaseMemoryBackend):
    def __init__(self, name: str = "none"):
        self.name = name


class Mem0MemoryBackend(BaseMemoryBackend):
    name = "mem0"

    def __init__(
        self,
        client: Any,
        *,
        user_id: str,
        agent_id: str,
        search_limit: int = 5,
    ):
        self.client = client
        self.user_id = user_id
        self.agent_id = agent_id
        self.search_limit = max(1, search_limit)

    def enabled(self) -> bool:
        return True

    def describe(self) -> str:
        return f"{self.name}(user_id={self.user_id},agent_id={self.agent_id})"

    def search(self, query: str, *, session_name: str, agent_label: str, limit: int = 5) -> List[MemoryRecord]:
        if not query.strip():
            return []
        try:
            payload = self.client.search(
                query=query,
                user_id=self.user_id,
                agent_id=self.agent_id,
                limit=max(1, limit),
            )
        except TypeError:
            payload = self.client.search(
                query,
                filters={"user_id": self.user_id, "agent_id": self.agent_id},
                limit=max(1, limit),
            )
        results = payload.get("results", payload) if isinstance(payload, dict) else payload
        memories: List[MemoryRecord] = []
        if not isinstance(results, list):
            return memories
        for item in results:
            if isinstance(item, str):
                text = item.strip()
                created_at = ""
                source = "mem0"
                score = 0.0
            elif isinstance(item, dict):
                text = str(
                    item.get("memory")
                    or item.get("text")
                    or item.get("content")
                    or item.get("value")
                    or ""
                ).strip()
                created_at = str(item.get("created_at") or item.get("updated_at") or "")
                source = str(item.get("source") or item.get("metadata", {}).get("source") or "mem0")
                try:
                    score = float(item.get("score") or item.get("similarity") or 0.0)
                except (TypeError, ValueError):
                    score = 0.0
            else:
                text = str(item).strip()
                created_at = ""
                source = "mem0"
                score = 0.0
            if text:
                memories.append(
                    MemoryRecord(
                        text=text,
                        created_at=created_at,
                        source=source,
                        score=score,
                        backend=self.name,
                        session_name=session_name,
                        agent_label=agent_label,
                    )
                )
            if len(memories) >= max(1, limit):
                break
        return memories

    def store(self, user_text: str, assistant_text: str, *, session_name: str, agent_label: str) -> None:
        messages = []
        if user_text.strip():
            messages.append({"role": "user", "content": user_text.strip()})
        if assistant_text.strip():
            messages.append({"role": "assistant", "content": assistant_text.strip()})
        if not messages:
            return
        try:
            self.client.add(
                messages,
                user_id=self.user_id,
                agent_id=self.agent_id,
                run_id=session_name,
                metadata={"source": "openinstruct", "agent_label": agent_label},
            )
        except TypeError:
            self.client.add(messages, user_id=self.user_id)


class SQLiteMemoryBackend(BaseMemoryBackend):
    name = "sqlite"

    def __init__(
        self,
        db_path: Path,
        *,
        user_id: str,
        agent_id: str,
        search_limit: int = 5,
    ):
        self.db_path = db_path.expanduser().resolve()
        self.user_id = user_id
        self.agent_id = agent_id
        self.search_limit = max(1, search_limit)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def enabled(self) -> bool:
        return True

    def describe(self) -> str:
        return f"{self.name}(path={self.db_path},user_id={self.user_id},agent_id={self.agent_id})"

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    session_name TEXT NOT NULL,
                    agent_label TEXT NOT NULL,
                    text TEXT NOT NULL,
                    source TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_memories_identity ON memories(user_id, agent_id, id DESC)"
            )
            conn.commit()

    def _row_to_record(self, row: sqlite3.Row, *, score: float = 0.0) -> MemoryRecord:
        return MemoryRecord(
            text=str(row["text"]),
            source=str(row["source"]),
            created_at=str(row["created_at"]),
            score=score,
            backend=self.name,
            session_name=str(row["session_name"]),
            agent_label=str(row["agent_label"]),
        )

    def recent(self, *, session_name: str, agent_label: str, limit: int = 8) -> List[MemoryRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT text, source, created_at, session_name, agent_label
                FROM memories
                WHERE user_id = ? AND agent_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (self.user_id, self.agent_id, max(1, limit)),
            ).fetchall()
        return [self._row_to_record(row) for row in rows]

    def search(self, query: str, *, session_name: str, agent_label: str, limit: int = 5) -> List[MemoryRecord]:
        if not query.strip():
            return self.recent(session_name=session_name, agent_label=agent_label, limit=limit)
        tokens = [token for token in re.findall(r"[A-Za-z0-9._/-]+", query.lower()) if len(token) >= 3]
        pattern_tokens = tokens[:6] or [query.lower()]
        clauses = " OR ".join(["LOWER(text) LIKE ?"] * len(pattern_tokens))
        params: List[Any] = [self.user_id, self.agent_id, *[f"%{token}%" for token in pattern_tokens]]
        sql = (
            "SELECT id, text, source, created_at, session_name, agent_label "
            "FROM memories WHERE user_id = ? AND agent_id = ? "
        )
        if clauses:
            sql += f"AND ({clauses}) "
        sql += "ORDER BY id DESC LIMIT 100"
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        scored: List[tuple[float, int, MemoryRecord]] = []
        for row in rows:
            lower = str(row["text"]).lower()
            score = float(sum(1 for token in pattern_tokens if token in lower))
            if session_name and row["session_name"] == session_name:
                score += 0.25
            if agent_label and row["agent_label"] == agent_label:
                score += 0.1
            scored.append((score, int(row["id"]), self._row_to_record(row, score=score)))
        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [record for _, _, record in scored[: max(1, limit)]]

    def store(self, user_text: str, assistant_text: str, *, session_name: str, agent_label: str) -> None:
        left = user_text.strip()
        right = assistant_text.strip()
        if left and right:
            text = f"User: {left}\nAssistant: {right}"
            source = "conversation"
        else:
            text = left or right
            source = "fact"
        if not text:
            return
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO memories (user_id, agent_id, session_name, agent_label, text, source, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (self.user_id, self.agent_id, session_name, agent_label, text, source, _utc_now()),
            )
            conn.commit()


def _coerce_search_limit() -> int:
    raw = os.getenv("OPENINSTRUCT_MEMORY_SEARCH_LIMIT", os.getenv("OPENINSTRUCT_MEM0_SEARCH_LIMIT", "5")).strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return 5


def _memory_identity() -> tuple[str, str]:
    user_id = os.getenv("OPENINSTRUCT_MEM0_USER_ID", getpass.getuser()).strip() or "local-user"
    agent_id = os.getenv("OPENINSTRUCT_MEM0_AGENT_ID", "openinstruct").strip() or "openinstruct"
    return user_id, agent_id


def _provider_base_url(settings: Settings, provider_info: ProviderInfo) -> str:
    if provider_info.name == "ollama":
        return settings.ollama_base_url
    return settings.lmstudio_base_url


def _default_embed_model(provider_name: str) -> str:
    if provider_name == "ollama":
        return "nomic-embed-text"
    return "text-embedding-nomic-embed-text-v1.5"


def _build_mem0_config(settings: Settings, provider_info: ProviderInfo) -> Dict[str, Any]:
    provider_name = provider_info.name
    base_url = _provider_base_url(settings, provider_info)
    llm_model = os.getenv("OPENINSTRUCT_MEM0_LLM_MODEL", provider_info.model).strip() or provider_info.model
    embed_model = os.getenv("OPENINSTRUCT_MEM0_EMBED_MODEL", _default_embed_model(provider_name)).strip()
    llm_config: Dict[str, Any] = {
        "model": llm_model,
        "temperature": settings.temperature,
        "max_tokens": 2000,
    }
    embedder_config: Dict[str, Any] = {"model": embed_model}
    if provider_name == "ollama":
        llm_config["ollama_base_url"] = base_url
        embedder_config["ollama_base_url"] = base_url
    elif provider_name == "lmstudio":
        llm_config["lmstudio_base_url"] = base_url
        embedder_config["lmstudio_base_url"] = base_url
    else:
        raise MemoryBackendError(f"mem0 backend is not configured for provider '{provider_name}'")
    return {
        "llm": {"provider": provider_name, "config": llm_config},
        "embedder": {"provider": provider_name, "config": embedder_config},
    }


def build_memory_backend(
    settings: Settings,
    provider_info: ProviderInfo,
    *,
    backend_override: Optional[str] = None,
) -> BaseMemoryBackend:
    backend_name = str(backend_override or settings.memory_backend or "none").strip().lower()
    if backend_name in {"", "none"}:
        return NullMemoryBackend()
    user_id, agent_id = _memory_identity()
    search_limit = _coerce_search_limit()
    if backend_name == "sqlite":
        db_path = Path(os.getenv("OPENINSTRUCT_SQLITE_MEMORY_PATH", str(settings.home / "memory.sqlite3")))
        return SQLiteMemoryBackend(db_path, user_id=user_id, agent_id=agent_id, search_limit=search_limit)
    if backend_name != "mem0":
        raise MemoryBackendError(f"Unknown memory backend: {backend_name}")
    if sys.version_info < (3, 10):
        raise MemoryBackendError("mem0 requires Python 3.10+ in this setup.")
    try:
        from mem0 import Memory
    except ImportError as exc:  # pragma: no cover
        raise MemoryBackendError(
            "mem0 backend requested but the 'mem0ai' package is not installed. "
            "Install it with 'pip install mem0ai' or 'pip install -e .[mem0]'."
        ) from exc
    config = _build_mem0_config(settings, provider_info)
    try:
        client = Memory.from_config(config)
    except AttributeError as exc:  # pragma: no cover
        raise MemoryBackendError("Installed mem0 package does not expose Memory.from_config.") from exc
    return Mem0MemoryBackend(client, user_id=user_id, agent_id=agent_id, search_limit=search_limit)
