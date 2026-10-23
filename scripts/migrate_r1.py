"""Preview or explicitly remove retired password users and legacy private chat data."""

import argparse
import asyncio
import hashlib

from app.db import close_mongo, connect_to_mongo, get_db


async def migrate(*, apply: bool = False) -> dict[str, int]:
    db = get_db()
    legacy = {"user_id": {"$exists": True}}
    private = {"scope": "user", "owner_id": {"$exists": True}}
    counts = {
        name: await db[name].count_documents(query)
        for name, query in (
            ("users", {}),
            ("sessions", legacy),
            ("messages", legacy),
            ("documents", private),
            ("chunks", private),
        )
    }
    if not apply:
        return counts
    # The old password system is retired; never assign its private chats to anonymous bearers.
    await db.drop_collection("users")
    for name, query in (
        ("sessions", legacy),
        ("messages", legacy),
        ("documents", private),
        ("chunks", private),
    ):
        await db[name].delete_many(query)
    # Retain audit events while removing historical bearer values and user identifiers.
    async for row in db.audit_log.find({"session_id": {"$exists": True}}):
        sid = row.get("session_id")
        fields = {
            "session_prefix": sid[:6] if sid else None,
            "session_hash": hashlib.sha256(sid.encode()).hexdigest() if sid else None,
        }
        await db.audit_log.update_one(
            {"_id": row["_id"]},
            {"$set": fields, "$unset": {"session_id": "", "user_id": ""}},
        )
    await db.audit_log.update_many(
        {"user_id": {"$exists": True}}, {"$unset": {"user_id": ""}}
    )
    return counts


async def main(apply: bool) -> None:
    try:
        await connect_to_mongo()
        counts = await migrate(apply=apply)
        print("Removed legacy rows:" if apply else "Preview only; legacy rows:", counts)
    finally:
        await close_mongo()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Delete the retired users and legacy private chat data.",
    )
    asyncio.run(main(parser.parse_args().apply))
