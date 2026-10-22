"""Retention cleanup and interrupted-ingestion recovery."""
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from app.config import get_settings
from app.db import documents_col, chunks_col, messages_col, sessions_col
from app.services.audit import audit_event

log = logging.getLogger(__name__)


async def sweep_stuck_documents(now: datetime | None = None) -> int:
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(minutes=15)
    result = await documents_col().update_many(
        {"status": "processing", "created_at": {"$lt": cutoff}},
        {"$set": {"status": "failed", "error": "Processing was interrupted. Please upload the file again."}},
    )
    return result.modified_count


async def purge_expired(now: datetime | None = None) -> dict[str, int]:
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=get_settings().retention_days)
    counts = dict(sessions=0, messages=0, documents=0, chunks=0, failed_documents=0)
    inactive = {"updated_at": {"$lt": cutoff}}
    rows = await sessions_col().find(inactive).to_list()
    for row in rows:
        identity = {"_id": row["_id"], "user_id": row["user_id"]}
        # Recheck activity atomically: a chat touched since the scan must survive.
        removed = await sessions_col().delete_one({**identity, **inactive})
        if not removed.deleted_count:
            continue
        counts["sessions"] += 1
        result = await messages_col().delete_many({"session_id": row["_id"], "user_id": row["user_id"]})
        counts["messages"] += result.deleted_count
        fields = {"scope": "user", "owner_id": row["user_id"], "session_id": row["_id"]}
        for name, collection in (("documents", documents_col()), ("chunks", chunks_col())):
            result = await collection.delete_many(fields)
            counts[name] += result.deleted_count
    expired = {"scope": "user", "expires_at": {"$lt": now}}
    for row in await documents_col().find(expired).to_list():
        fields = {"scope": "user", "owner_id": row["owner_id"], "session_id": row["session_id"]}
        removed = await documents_col().delete_one({"_id": row["_id"], **fields, "expires_at": {"$lt": now}})
        if removed.deleted_count:
            counts["documents"] += 1
            result = await chunks_col().delete_many({"doc_id": str(row["_id"]), **fields})
            counts["chunks"] += result.deleted_count
            await sessions_col().update_one({"_id": row["session_id"], "user_id": row["owner_id"],
                "attachment_count": {"$gt": 0}}, {"$inc": {"attachment_count": -1}})
    counts["failed_documents"] = await sweep_stuck_documents(now)
    await audit_event("retention_purge", counts=counts)
    return counts


async def retention_loop() -> None:
    while True:
        await asyncio.sleep(3600)
        try:
            await purge_expired()
        except Exception as error:
            log.error("Retention cleanup failed: exception_type=%s", type(error).__name__)
