"""Account-hash failures survive New chat, including for nonexistent accounts."""

import hashlib
from datetime import timedelta

from pymongo import ReturnDocument

from app.config import get_settings
from app.db import verify_locks_col
from app.sessions.store import aware, now_utc


def account_hash(account: str) -> str:
    return hashlib.sha256(account.encode()).hexdigest()


async def is_locked(account: str) -> bool:
    key = account_hash(account)
    row = await verify_locks_col().find_one({"_id": key})
    if not row or not row.get("locked_until"):
        return False
    if aware(row["locked_until"]) > now_utc():
        return True
    # TTL can lag; lazy expiry also resets failures when the lock ends.
    await verify_locks_col().delete_one(
        {"_id": key, "locked_until": row["locked_until"]}
    )
    return False


async def failed_attempt(account: str) -> bool:
    if await is_locked(account):
        return True
    row = await verify_locks_col().find_one_and_update(
        {"_id": account_hash(account)},
        {"$inc": {"failures": 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    if row["failures"] < get_settings().verify_max_tries:
        return False
    until = now_utc() + timedelta(minutes=get_settings().verify_lock_minutes)
    await verify_locks_col().update_one(
        {"_id": row["_id"]}, {"$set": {"locked_until": until, "expires_at": until}}
    )
    return True


async def reset_failures(account: str) -> None:
    await verify_locks_col().delete_one({"_id": account_hash(account)})
