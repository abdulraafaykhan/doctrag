"""Generate obviously synthetic customer fixtures with consistent balances."""

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import ROOT, get_settings


def generate() -> None:
    cfg = get_settings().seed
    today = datetime.now(timezone.utc).replace(
        hour=12, minute=0, second=0, microsecond=0
    )
    records = {name: [] for name in ("users", "accounts", "cards", "transactions")}
    for number in range(1, cfg["customers"] + 1):
        user_id, account_id = f"usr_{number:03}", f"acc_{number:03}"
        balance = 10000000
        for index in range(cfg["transactions_per_account"]):
            credit = index % 3 == 0
            amount = (index + number) * 10000
            balance += amount if credit else -amount
            posted = today - timedelta(
                days=cfg["history_days"]
                - (index + 1) * cfg["history_days"] // cfg["transactions_per_account"]
            )
            records["transactions"].append(
                {
                    "txn_id": f"txn_{number:03}_{index + 1:03}",
                    "account_id": account_id,
                    "posted_at": posted.isoformat(),
                    "direction": "credit" if credit else "debit",
                    "amount_paise": amount,
                    "balance_after_paise": balance,
                    "description": f"DEMO {'credit' if credit else 'purchase'} {index + 1}",
                    "channel": "neft" if credit else "upi",
                    "reference": f"DEMO-{number:03}-{index + 1:03}",
                }
            )
        records["users"].append(
            {
                "user_id": user_id,
                "full_name": f"Demo Customer {number:02}",
                "mobile": f"900000{number:04}",
                "account_id": account_id,
                "address": {
                    "line1": f"DEMO ONLY Flat {number}",
                    "line2": "Fictional Harbourline Colony",
                    "city": "Mumbai",
                    "state": "Maharashtra",
                    "pin": "000000",
                },
                "status": "active",
                "created_at": (today - timedelta(days=cfg["history_days"])).isoformat(),
            }
        )
        records["accounts"].append(
            {
                "account_id": account_id,
                "account_number": f"00000000{number:04}",
                "user_id": user_id,
                "type": "savings" if number % 2 else "current",
                "balance_paise": balance,
                "currency": get_settings().bank["currency"],
                "status": "frozen" if number == cfg["customers"] else "active",
                "branch": "DEMO Mumbai",
                "opened_on": (today - timedelta(days=cfg["history_days"]))
                .date()
                .isoformat(),
            }
        )
        records["cards"].append(
            {
                "card_id": f"card_{number:03}",
                "user_id": user_id,
                "account_id": account_id,
                "network": "rupay",
                "card_type": "standard",
                "last4": f"{number:04}",
                "expiry_month": 12,
                "expiry_year": today.year + 3,
                "status": "blocked" if number == cfg["customers"] - 1 else "active",
                "daily_limit_paise": 5000000,
                "issued_on": (today - timedelta(days=cfg["history_days"]))
                .date()
                .isoformat(),
            }
        )
    (ROOT / "seed").mkdir(exist_ok=True)
    for name, values in records.items():
        (ROOT / "seed" / f"{name}.json").write_text(
            json.dumps(values, indent=2) + "\n", encoding="utf-8"
        )


if __name__ == "__main__":
    generate()
