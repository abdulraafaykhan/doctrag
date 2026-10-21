"""Chat history stored in MongoDB, so conversations survive restarts and the server stays stateless."""
from datetime import datetime, timezone

from app.db import messages_col


def _iso(dt: datetime) -> str:
    """Mongo returns naive UTC datetimes; mark them as UTC so browsers parse them correctly."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


async def save_message(
    session_id: str,
    role: str,
    content: str,
    sources: list[dict] | None = None,
    created_at: datetime | None = None,
) -> None:
    await messages_col().insert_one(
        {
            "session_id": session_id,
            "role": role,
            "content": content,
            "sources": sources or [],
            "created_at": created_at or datetime.now(timezone.utc),
        }
    )


async def get_recent(session_id: str, limit: int) -> list[dict]:
    """Last `limit` messages of a session, oldest first, as [{'role', 'content'}]."""
    cursor = (
        messages_col()
        .find({"session_id": session_id}, {"_id": 0, "role": 1, "content": 1})
        .sort("created_at", -1)
        .limit(limit)
    )
    rows = await cursor.to_list()
    rows.reverse()
    return rows


async def get_full_history(session_id: str) -> list[dict]:
    cursor = messages_col().find({"session_id": session_id}, {"_id": 0}).sort("created_at", 1)
    rows = await cursor.to_list()
    for r in rows:
        r["created_at"] = _iso(r["created_at"])
    return rows


async def list_sessions(limit: int = 30) -> list[dict]:
    """Most recently active conversations: id, title (first message), last activity, message count."""
    pipeline = [
        {"$sort": {"created_at": 1}},
        {
            "$group": {
                "_id": "$session_id",
                "title": {"$first": "$content"},
                "updated_at": {"$last": "$created_at"},
                "messages": {"$sum": 1},
            }
        },
        {"$sort": {"updated_at": -1}},
        {"$limit": limit},
    ]
    cursor = await messages_col().aggregate(pipeline)
    rows = await cursor.to_list()
    return [
        {
            "session_id": r["_id"],
            "title": (r["title"] or "")[:80],
            "updated_at": _iso(r["updated_at"]),
            "messages": r["messages"],
        }
        for r in rows
    ]


async def clear_session(session_id: str) -> int:
    result = await messages_col().delete_many({"session_id": session_id})
    return result.deleted_count