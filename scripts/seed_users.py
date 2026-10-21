"""Seed three fictional demo users. Requires a live MongoDB connection."""
import asyncio
from datetime import datetime, timezone
from uuid import UUID

from app.auth.security import hash_password
from app.db import close_mongo, connect_to_mongo, ensure_indexes, users_col

# These customer ids are the contract for the Phase 4a bank seed.
DEMO_USERS = (
    ("10000000-0000-4000-8000-000000000001", "alice", "Demo-Alice-123!", "demo-customer-001"),
    ("10000000-0000-4000-8000-000000000002", "bob", "Demo-Bob-123!", "demo-customer-002"),
    ("10000000-0000-4000-8000-000000000003", "carol", "Demo-Carol-123!", "demo-customer-003"),
)


async def seed_users() -> None:
    for user_id, username, password, customer_id in DEMO_USERS:
        UUID(user_id)
        # Re-running preserves an existing account and never resets its password or status.
        await users_col().update_one({"username": username}, {"$setOnInsert": {
            "_id": user_id, "username": username, "password_hash": hash_password(password),
            "customer_id": customer_id, "status": "active", "created_at": datetime.now(timezone.utc),
        }}, upsert=True)


async def main() -> None:
    try:
        await connect_to_mongo()
        await ensure_indexes()
        await seed_users()
        print("Fictional demo users are ready. Existing users were preserved.")
    finally:
        await close_mongo()


if __name__ == "__main__":
    asyncio.run(main())
