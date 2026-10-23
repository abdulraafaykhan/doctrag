"""Reproducible fictional bank fixtures; no database or provider access."""

import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

SEED_DIRECTORY = Path(__file__).resolve().parents[1] / "seed_data" / "bank"
SCENARIOS = (
    "Savings account and active debit card",
    "Savings and current accounts",
    "Savings account and credit card",
    "Two savings accounts under one mobile",
    "Blocked debit card",
    "Cheque book ineligible",
    "Incomplete KYC",
    "Pending cheque book request",
    "New account with little activity",
    "Senior citizen with a long history",
)


def generate_data() -> dict[str, list[dict]]:
    """Keep all dates and randomness fixed so generated JSON can be reviewed."""
    rng = random.Random(42)
    data = {
        name: []
        for name in (
            "customers",
            "accounts",
            "cards",
            "transactions",
            "cheque_books",
            "service_requests",
        )
    }
    as_of = datetime(2026, 10, 1, tzinfo=timezone.utc)
    for number, scenario in enumerate(SCENARIOS, 1):
        customer_id = f"CUST-{number:03}"
        data["customers"].append(
            {
                "customer_id": customer_id,
                "full_name": f"Harbourline Demo Customer {number:02}",
                "mobile": f"9000000{100 + number}",
                "email": f"demo{number:02}@example.invalid",
                "date_of_birth": "1950-04-12" if number == 10 else "1992-05-16",
                "address": {
                    "line1": f"Demo flat {number}, Fictional Harbour House",
                    "line2": "Demonstration address only",
                    "locality": "Demo Nagar",
                    "city": "Mumbai",
                    "state": "Maharashtra",
                    "pincode": "400001",
                },
                "kyc_status": "incomplete" if number == 7 else "complete",
                "customer_since": "2010-01-01" if number == 10 else "2025-01-01",
                "scenario": scenario,
            }
        )
        for index in range(1, 3 if number in (2, 4) else 2):
            account_id = f"ACC-{number:03}-{index:02}"
            count = 2 if number == 9 else 180 if number == 10 else 40
            balance = 0
            for offset in range(count):
                # Signed integer entries are the source of each stored balance.
                amount = 500_000 if offset == 0 else rng.randint(100, 30_000)
                if offset and offset % 5:
                    amount = -amount
                balance += amount
                narration = (
                    "NEFT DEMO SALARY CREDIT"
                    if amount > 0
                    else rng.choice(
                        (
                            "UPI DEMO GROCER",
                            "IMPS DEMO PAYMENT",
                            "ATM CASH WITHDRAWAL",
                            "POS DEMO SHOP",
                            "ACH DEMO UTILITY",
                            "DEMO BANK CHARGES",
                        )
                    )
                )
                data["transactions"].append(
                    {
                        "txn_id": f"{account_id}-TX-{offset:04}",
                        "account_id": account_id,
                        "posted_at": (
                            as_of - timedelta(days=(count - offset) * 2)
                        ).isoformat(),
                        "narration": narration,
                        "amount_minor": amount,
                        "balance_after_minor": balance,
                        "currency": "INR",
                        "channel": narration.split()[0],
                        "reference": f"DEMO-{number:03}-{index}-{offset:04}",
                    }
                )
            data["accounts"].append(
                {
                    "account_id": account_id,
                    "customer_id": customer_id,
                    "account_number": f"880000{number:03}{index:03}",
                    "ifsc": "HRBL0000001",
                    "branch_name": "Fictional Harbour Mumbai Branch",
                    "type": "current" if number == 2 and index == 2 else "savings",
                    "status": "active",
                    "opened_on": "2026-09-27" if number == 9 else "2025-01-01",
                    "currency": "INR",
                    "min_balance_minor": 100_000,
                    "cheque_book_eligible": number != 6,
                    "nickname": f"Demo account {index}",
                    "balance_minor": balance,
                }
            )
            data["cards"].append(
                {
                    "card_id": f"CARD-{number:03}-{index:02}",
                    "customer_id": customer_id,
                    "account_id": account_id,
                    "type": "debit",
                    "network": "RuPay",
                    "last4": f"{number:02}{index:02}",
                    "status": "blocked" if number == 5 else "active",
                    "expiry": "12/2029",
                    "currency": "INR",
                    "daily_atm_limit_minor": 1_000_000,
                    "daily_pos_limit_minor": 2_500_000,
                    "online_enabled": True,
                    "international_enabled": False,
                    "contactless_enabled": True,
                }
            )
        if number == 3:
            data["cards"].append(
                {
                    "card_id": "CARD-003-CREDIT",
                    "customer_id": customer_id,
                    "account_id": None,
                    "type": "credit",
                    "network": "Visa",
                    "last4": "7303",
                    "status": "active",
                    "expiry": "06/2029",
                    "currency": "INR",
                    "credit_limit_minor": 10_000_000,
                    "outstanding_minor": 125_000,
                    "daily_atm_limit_minor": 100_000,
                    "daily_pos_limit_minor": 2_500_000,
                    "online_enabled": True,
                    "international_enabled": False,
                    "contactless_enabled": True,
                }
            )
    data["cheque_books"].append(
        {
            "cheque_book_id": "CHQ-001",
            "account_id": "ACC-001-01",
            "leaves": 25,
            "issued_on": "2026-08-01",
            "series_start": "100001",
            "series_end": "100025",
            "status": "issued",
        }
    )
    # Seed future-flow records without adding any service-write tool in R1.
    data["service_requests"].append(
        {
            "reference": "HB-CHQ-2026-000123",
            "customer_id": "CUST-008",
            "type": "cheque_book",
            "status": "processing",
            "created_at": "2026-09-29T10:00:00+00:00",
            "updated_at": "2026-09-30T10:00:00+00:00",
            "details": {"account_id": "ACC-008-01", "leaves": 50},
        }
    )
    return data


def main() -> None:
    SEED_DIRECTORY.mkdir(parents=True, exist_ok=True)
    for name, rows in generate_data().items():
        (SEED_DIRECTORY / f"{name}.json").write_text(
            json.dumps(rows, indent=2) + "\n", encoding="utf-8"
        )
    print("Deterministic fictional bank JSON generated; no live services were called.")


if __name__ == "__main__":
    main()
