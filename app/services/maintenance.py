"""Retention cleanup and interrupted-ingestion recovery."""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from app.config import get_settings
from app.db import chunks_col, documents_col, messages_col, sessions_col
from app.services.audit import audit_event

log = logging.getLogger(__name__)


async def sweep_stuck_documents(now: datetime | None = None) -> int:
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(minutes=15)
    result = await documents_col().update_many(
        {"status": "processing", "created_at": {"$lt": cutoff}},
        {
            "$set": {
                "status": "failed",
                "error": "Processing was interrupted. Please upload the file again.",
            }
        },
    )
    return result.modified_count


async def purge_expired(now: datetime | None = None) -> dict[str, int]:
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=get_settings().session_idle_wipe_minutes)
    counts = dict(sessions=0, messages=0, documents=0, chunks=0, failed_documents=0)
    from app.sessions.store import wipe

    for row in (
        await sessions_col().find({"last_activity_at": {"$lte": cutoff}}).to_list()
    ):
        deleted = await wipe(row["_id"], expired=True, cutoff=cutoff)
        if deleted is not None:
            counts["sessions"] += 1
            for name, count in deleted.items():
                counts[name] += count
    # TTL cannot cascade. An active parent wins if activity races child deadline updates.
    for name, collection in (
        ("messages", messages_col()),
        ("documents", documents_col()),
        ("chunks", chunks_col()),
    ):
        query = {"expires_at": {"$lte": now}}
        if name != "messages":
            query["scope"] = "user"
        candidates = await collection.find(query, {"session_id": 1}).to_list()
        for session_id in {row["session_id"] for row in candidates}:
            if await sessions_col().find_one({"_id": session_id}) is not None:
                continue
            result = await collection.delete_many({**query, "session_id": session_id})
            counts[name] += result.deleted_count
    counts["failed_documents"] = await sweep_stuck_documents(now)
    await audit_event("retention_purge", counts=counts)
    return counts


async def retention_loop() -> None:
    while True:
        await asyncio.sleep(3600)
        try:
            await purge_expired()
        except Exception as error:
            log.error(
                "Retention cleanup failed: exception_type=%s", type(error).__name__
            )
