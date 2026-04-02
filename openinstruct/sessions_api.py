from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, Optional

if TYPE_CHECKING:
    from .agent import AgentRuntime


class ManagedSessionsAPI:
    def __init__(self, runtime: "AgentRuntime"):
        self.runtime = runtime

    def list(self, prefix: Optional[str] = None) -> Dict[str, Any]:
        return {"sessions": self.runtime.managed_sessions_snapshot(prefix=prefix)}

    def status(self, session_id: str) -> Dict[str, Any]:
        return self.runtime.managed_session_status_payload(session_id)

    def history(self, session_id: str, limit: int = 8) -> Dict[str, Any]:
        return self.runtime.managed_session_history_payload(session_id, limit=limit)

    def spawn(
        self,
        prompt: str,
        *,
        session_id: str = "",
        write: bool = False,
        title: str = "",
        prefix: str = "sess",
        visibility: str = "tree",
    ) -> Dict[str, Any]:
        if write and self.runtime.settings.approval_policy != "auto":
            raise ValueError("Managed sessions may mutate the workspace only when approval policy is 'auto'.")
        approval_policy = "auto" if write else "deny"
        session = self.runtime.spawn_managed_session(
            prompt,
            session_id=session_id or None,
            prefix=prefix,
            title=title or None,
            approval_policy=approval_policy,
            visibility=visibility,
        )
        return self.status(session.session_id)

    def send(self, session_id: str, prompt: str) -> Dict[str, Any]:
        item = self.runtime.send_managed_session_input(session_id, prompt)
        return {
            "session_id": session_id,
            "message_id": item.message_id,
            "status": item.status,
            "prompt": item.prompt,
            "queued_at": item.queued_at,
        }
