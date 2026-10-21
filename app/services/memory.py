"""Owned chat sessions and messages."""
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import HTTPException

from app.db import messages_col, sessions_col


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def public_session(row: dict) -> dict:
    return {"session_id": row["_id"], "title": row["title"],
            "created_at": _iso(row["created_at"]), "updated_at": _iso(row["updated_at"])}


async def require_session(session_id: str, user_id: str) -> dict:
    row = await sessions_col().find_one({"_id": session_id, "user_id": user_id})
    if row is None:
        raise HTTPException(404, "Session not found")
    return row


async def create_session(user_id: str) -> dict:
    now = datetime.now(timezone.utc)
    row = {"_id": str(uuid4()), "user_id": user_id, "title": "", "created_at": now, "updated_at": now}
    await sessions_col().insert_one(row)
    return public_session(row)


async def save_message(session_id: str, role: str, content: str, *, user_id: str,
                       sources: list[dict] | None = None, created_at: datetime | None = None) -> None:
    await require_session(session_id, user_id)
    await messages_col().insert_one({"session_id": session_id, "user_id": user_id,
        "role": role, "content": content, "sources": sources or [],
        "created_at": created_at or datetime.now(timezone.utc)})
    await sessions_col().update_one({"_id": session_id, "user_id": user_id},
                                    {"$set": {"updated_at": datetime.now(timezone.utc)}})
    if role == "user":
        await sessions_col().update_one({"_id": session_id, "user_id": user_id, "title": ""},
                                        {"$set": {"title": content[:80]}})


async def get_recent(session_id: str, limit: int, *, user_id: str) -> list[dict]:
    await require_session(session_id, user_id)
    cursor = messages_col().find({"session_id": session_id, "user_id": user_id},
                                 {"_id": 0, "role": 1, "content": 1}).sort("created_at", -1).limit(limit)
    rows = await cursor.to_list()
    rows.reverse()
    return rows


async def get_full_history(session_id: str, *, user_id: str) -> list[dict]:
    await require_session(session_id, user_id)
    rows = await messages_col().find({"session_id": session_id, "user_id": user_id}, {"_id": 0}).sort("created_at", 1).to_list()
    for row in rows:
        row["created_at"] = _iso(row["created_at"])
    return rows


async def list_sessions(user_id: str, limit: int = 30) -> list[dict]:
    rows = await sessions_col().find({"user_id": user_id}).sort("updated_at", -1).limit(limit).to_list()
    return [public_session(row) for row in rows]


async def clear_session(session_id: str, *, user_id: str) -> int:
    await require_session(session_id, user_id)
    # Remove the session first so subsequent requests cannot continue it.
    await sessions_col().delete_one({"_id": session_id, "user_id": user_id})
    result = await messages_col().delete_many({"session_id": session_id, "user_id": user_id})
    return result.deleted_count
