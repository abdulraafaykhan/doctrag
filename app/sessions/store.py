"""Anonymous bearer sessions and cascading deletion; no model or agent dependency."""

import asyncio
import logging
import secrets
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from typing import Annotated
from weakref import WeakValueDictionary

from fastapi import Depends, Header, HTTPException

from app.config import get_settings
from app.db import chunks_col, documents_col, messages_col, sessions_col
from app.services.audit import audit_event

log = logging.getLogger(__name__)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def aware(value: datetime) -> datetime:
    # Mongo's default decoder returns naive UTC datetimes.
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def deadline(activity: datetime) -> datetime:
    return aware(activity) + timedelta(minutes=get_settings().session_idle_wipe_minutes)


async def delete_private_files(session_id: str) -> dict[str, int]:
    fields = {"scope": "user", "session_id": session_id}
    documents = await documents_col().delete_many(fields)
    chunks = await chunks_col().delete_many(fields)
    await sessions_col().update_one(
        {"_id": session_id}, {"$set": {"attachment_count": 0}}
    )
    return {"documents": documents.deleted_count, "chunks": chunks.deleted_count}


async def wipe(
    session_id: str, *, expired: bool = False, cutoff: datetime | None = None
) -> dict[str, int] | None:
    query = {"_id": session_id}
    if cutoff is not None:
        query["last_activity_at"] = {"$lte": cutoff}
    removed = await sessions_col().delete_one(query)
    if not removed.deleted_count:
        return None
    # Removing the authority first prevents subsequent tools from using the session.
    messages = await messages_col().delete_many({"session_id": session_id})
    counts = {
        "messages": messages.deleted_count,
        **await delete_private_files(session_id),
    }
    event = "session_expired" if expired else "session_wiped"
    await audit_event(event, session_id=session_id, counts=counts)
    log.info("%s session=%s", event, session_id[:6])
    return counts


async def create_session() -> dict:
    now = now_utc()
    session_id = secrets.token_urlsafe(32)
    await sessions_col().insert_one(
        {
            "_id": session_id,
            "created_at": now,
            "last_activity_at": now,
            "expires_at": deadline(now),
            "verified": None,
            "flow": {"state": "idle"},
            "attachment_count": 0,
            "attachment_customer_id": None,
        }
    )
    await audit_event("session_created", session_id=session_id)
    log.info("session_created session=%s", session_id[:6])
    return {"session_id": session_id}


async def require_session(session_id: str) -> dict:
    row = await sessions_col().find_one({"_id": session_id})
    now = now_utc()
    if row is None:
        raise HTTPException(404, "Session not found")
    if "last_activity_at" not in row:
        await wipe(session_id, expired=True)
        raise HTTPException(404, "Session not found")
    if deadline(row["last_activity_at"]) <= now:
        await wipe(
            session_id,
            expired=True,
            cutoff=now - timedelta(minutes=get_settings().session_idle_wipe_minutes),
        )
        raise HTTPException(404, "Session not found")
    verified = row.get("verified")
    settings = get_settings()
    if verified and (
        aware(verified["last_verified_activity_at"])
        + timedelta(minutes=settings.verify_idle_minutes)
        <= now
        or aware(verified["verified_at"])
        + timedelta(minutes=settings.verify_max_minutes)
        <= now
    ):
        await sessions_col().update_one(
            {"_id": session_id, "verified": verified}, {"$set": {"verified": None}}
        )
        row["verified"] = None
        await audit_event("verify_expired", session_id=session_id)
        log.info("verify_expired session=%s", session_id[:6])
    return row


async def session_header(x_session_id: Annotated[str | None, Header()] = None) -> dict:
    if not x_session_id:
        raise HTTPException(404, "Session not found")
    return await require_session(x_session_id)


def match_path(row: dict, session_id: str) -> None:
    if row["_id"] != session_id:
        raise HTTPException(404, "Session not found")


def require_verified(row: dict) -> None:
    if row.get("verified") is None:
        raise HTTPException(
            403,
            {
                "code": "verification_required",
                "message": "Please verify in chat before attaching files. Ask about your account to begin.",
            },
        )


async def touch_session(session_id: str) -> dict:
    row = await require_session(session_id)
    now = now_utc()
    fields = {"last_activity_at": now, "expires_at": deadline(now)}
    if row.get("verified"):
        fields["verified.last_verified_activity_at"] = now
    await sessions_col().update_one({"_id": session_id}, {"$set": fields})
    # Child deadlines allow cleanup even if Mongo TTL removed the parent first.
    for collection in (messages_col(), documents_col(), chunks_col()):
        await collection.update_many(
            {"session_id": session_id}, {"$set": {"expires_at": deadline(now)}}
        )
    return await require_session(session_id)


# This local demo uses one worker; serialize each chat's state transitions and wipe.
_turn_locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()


async def session_turn(
    session: Annotated[dict, Depends(session_header)],
) -> AsyncIterator[dict]:
    session_id = session["_id"]
    lock = _turn_locks.setdefault(session_id, asyncio.Lock())
    async with lock:
        yield await require_session(session_id)
