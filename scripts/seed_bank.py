"""Explicit, idempotent JSON seed; only the human runs this against Mongo."""

import asyncio
import json
from pathlib import Path

from app.bank.db import close_bank, ensure_indexes, get_db

SEED_DIRECTORY = Path(__file__).resolve().parents[1] / "seed_data" / "bank"
KEYS = {
    "customers": "customer_id",
    "accounts": "account_id",
    "transactions": "txn_id",
    "cards": "card_id",
    "cheque_books": "cheque_book_id",
    "service_requests": "reference",
}


def build_seed() -> dict[str, list[dict]]:
    # JSON is reviewed fixture data, never generated with live customer information.
    return {
        name: json.loads((SEED_DIRECTORY / f"{name}.json").read_text(encoding="utf-8"))
        for name in KEYS
    }


async def main() -> None:
    try:
        await ensure_indexes()
        db = get_db()
        for collection, rows in build_seed().items():
            for row in rows:
                await db[collection].update_one(
                    {KEYS[collection]: row[KEYS[collection]]},
                    {"$set": row},
                    upsert=True,
                )
        print("Ten fictional Harbourline customers are ready.")
    finally:
        await close_bank()


if __name__ == "__main__":
    asyncio.run(main())
