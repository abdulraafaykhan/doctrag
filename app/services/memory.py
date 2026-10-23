"""Session-scoped history; the privacy helper is applied before every insert."""

from datetime import datetime

from app.db import messages_col
from app.sessions.store import aware, deadline, now_utc, require_session
from app.verification.masking import mask_sensitive


def _iso(value: datetime) -> str:
    return aware(value).isoformat()


async def save_message(
    session_id: str,
    role: str,
    content: str,
    *,
    sources: list[dict] | None = None,
    attachments: list[dict] | None = None,
    account_cards: list[dict] | None = None,
    awaiting_otp: bool = False,
    created_at: datetime | None = None,
) -> None:
    session = await require_session(session_id)
    await messages_col().insert_one(
        {
            "session_id": session_id,
            "role": role,
            "content": mask_sensitive(content, awaiting_otp=awaiting_otp),
            "sources": sources or [],
            "attachments": attachments or [],
            "account_cards": account_cards or [],
            "created_at": created_at or now_utc(),
            "expires_at": deadline(session["last_activity_at"]),
        }
    )


async def get_recent(session_id: str, limit: int) -> list[dict]:
    session = await require_session(session_id)
    if limit <= 0:
        return []
    rows = (
        await messages_col()
        .find({"session_id": session_id}, {"_id": 0})
        .sort("created_at", -1)
        .limit(limit)
        .to_list()
    )
    # Expired verification must not expose earlier private evidence through model history.
    if not session.get("verified"):
        rows = [
            row
            for row in rows
            if not row.get("account_cards")
            and not any(
                source.get("scope") == "user" for source in row.get("sources", [])
            )
        ]
    return [{"role": row["role"], "content": row["content"]} for row in reversed(rows)]


async def get_full_history(session_id: str) -> list[dict]:
    await require_session(session_id)
    rows = (
        await messages_col()
        .find({"session_id": session_id}, {"_id": 0})
        .sort("created_at", 1)
        .to_list()
    )
    for row in rows:
        row["created_at"] = _iso(row["created_at"])
    return rows
