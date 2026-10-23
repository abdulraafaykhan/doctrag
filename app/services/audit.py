"""Append-only events retain correlation without retaining bearer secrets or content."""

import hashlib
from datetime import datetime, timezone

from app.db import audit_log_col
from app.logging_setup import turn_id


async def audit_event(
    event: str,
    *,
    session_id: str | None = None,
    outcome: str = "ok",
    tool: str | None = None,
    latency_ms: int | None = None,
    counts: dict[str, int] | None = None,
) -> None:
    await audit_log_col().insert_one(
        {
            "ts": datetime.now(timezone.utc),
            "event": event,
            "turn_id": turn_id.get(),
            "session_prefix": session_id[:6] if session_id else None,
            "session_hash": hashlib.sha256(session_id.encode()).hexdigest()
            if session_id
            else None,
            "outcome": outcome,
            "tool": tool,
            "latency_ms": latency_ms,
            "counts": counts,
        }
    )
