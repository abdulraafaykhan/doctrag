"""Owned attachment lookup and readiness events."""

import asyncio
import time
from collections.abc import AsyncIterator

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import HTTPException

from app.config import get_settings
from app.db import documents_col
from app.services.document_store import user_scope
from app.sessions.store import require_session, require_verified


async def resolve_attachments(ids: list[str], session_id: str) -> list[dict]:
    session = await require_session(session_id)
    if ids:
        require_verified(session)
    try:
        object_ids = [ObjectId(value) for value in dict.fromkeys(ids)]
    except (InvalidId, TypeError):
        raise HTTPException(404, "Attachment not found") from None
    if not object_ids:
        return []
    rows = (
        await documents_col()
        .find({**user_scope(session_id), "_id": {"$in": object_ids}})
        .to_list()
    )
    if len(rows) != len(object_ids):
        raise HTTPException(404, "Attachment not found")
    by_id = {row["_id"]: row for row in rows}
    return [by_id[value] for value in object_ids]


async def attachment_events(ids: list[str], session_id: str) -> AsyncIterator[dict]:
    if not ids:
        return
    deadline = time.monotonic() + get_settings().attachment_wait_seconds
    while True:
        rows = await resolve_attachments(ids, session_id)
        if any(row["status"] == "failed" for row in rows):
            yield {
                "type": "error",
                "detail": "An attached file failed to process. Please upload it again.",
            }
            return
        if all(row["status"] == "ready" for row in rows):
            return
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            yield {
                "type": "error",
                "detail": "Attached files are still processing. Please try again when they are ready.",
            }
            return
        yield {
            "type": "status",
            "text": "Waiting for attached files to finish processing…",
        }
        await asyncio.sleep(min(1.0, remaining))
