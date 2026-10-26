"""Idempotently upsert committed demo fixtures without clearing existing data."""

import json
import sys
from datetime import datetime
from pathlib import Path

from pymongo import ReplaceOne

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import ROOT
from app.db import close_db, collection, ping
from app.log import log


def seed() -> None:
    ping()
    for name, identity in (
        ("users", "user_id"),
        ("accounts", "account_id"),
        ("cards", "card_id"),
        ("transactions", "txn_id"),
    ):
        values = json.loads(
            (ROOT / "seed" / f"{name}.json").read_text(encoding="utf-8")
        )
        for value in values:
            for field in ("created_at", "posted_at"):
                if field in value:
                    value[field] = datetime.fromisoformat(value[field])
        with log.span("DB", "seed upsert", collection=name, count=len(values)):
            collection(name).bulk_write(
                [
                    ReplaceOne({identity: value[identity]}, value, upsert=True)
                    for value in values
                ],
                ordered=False,
            )


if __name__ == "__main__":
    try:
        seed()
    except Exception as exc:
        log.step("DB", "seed failed", error=type(exc).__name__, level="ERROR")
        raise SystemExit(1) from None
    finally:
        close_db()
