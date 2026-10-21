"""Lazy bank connection; runtime credentials may be read-only."""
import certifi
from pymongo import AsyncMongoClient
from pymongo.asynchronous.database import AsyncDatabase
from app.config import get_settings

_client: AsyncMongoClient | None = None

def get_db() -> AsyncDatabase:
    global _client
    s = get_settings()
    if _client is None:
        _client = AsyncMongoClient(s.bank_mongodb_uri or s.mongodb_uri,
            tlsCAFile=certifi.where(), serverSelectionTimeoutMS=8000)
    return _client[s.bank_db_name]

async def close_bank() -> None:
    global _client
    if _client is not None:
        await _client.close()
        _client = None

async def ensure_indexes() -> None:
    db = get_db()
    for collection, key in (("customers", "customer_id"), ("accounts", "account_id"),
                            ("transactions", "txn_id"), ("cards", "card_id")):
        await db[collection].create_index(key, unique=True)
    await db.accounts.create_index("customer_id")
    await db.transactions.create_index([("account_id", 1), ("posted_at", -1)])
    await db.cards.create_index("customer_id")
