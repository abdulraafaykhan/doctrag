"""Append-only, content-free audit events."""
from datetime import datetime, timezone
from typing import Literal

from app.db import audit_log_col


async def audit_event(event: Literal["login_ok", "login_fail", "chat"], *, user_id: str | None = None,
                      session_id: str | None = None, outcome: str = "ok") -> None:
    await audit_log_col().insert_one({"ts": datetime.now(timezone.utc), "event": event,
        "user_id": user_id, "session_id": session_id, "tool": None,
        "outcome": outcome, "detail": "", "latency_ms": None})
