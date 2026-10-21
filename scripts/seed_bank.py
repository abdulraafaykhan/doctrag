"""Explicit, idempotent fictional bank seed; requires write credentials."""
import asyncio
from datetime import datetime, timedelta, timezone
from app.bank.db import get_db, ensure_indexes, close_bank
CUSTOMER_IDS = ("demo-customer-001", "demo-customer-002", "demo-customer-003")

def build_seed() -> dict[str, list[dict]]:
    data = {name: [] for name in ("customers", "accounts", "transactions", "cards")}
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for i, customer_id in enumerate(CUSTOMER_IDS, 1):
        data["customers"].append(dict(customer_id=customer_id, full_name=("Alice Demo", "Bob Demo", "Carol Demo")[i-1], email=f"demo{i}@example.invalid", created_at=start))
        for j in range(1, i+1):
            account_id = f"demo-account-{i}-{j}"
            balance = 0
            for n in range(36):
                credit = n % 6 == 0
                amount = 100000 + i*10000 + j*1000 if credit else 1000+n*13
                balance += amount if credit else -amount
                data["transactions"].append(dict(txn_id=f"{account_id}-txn-{n}", account_id=account_id, posted_at=start+timedelta(days=n), description="Fictional salary" if credit else "Fictional purchase", direction="credit" if credit else "debit", amount_minor=amount, currency="INR", category="income" if credit else "shopping", balance_after_minor=balance))
            data["accounts"].append(dict(account_id=account_id, customer_id=customer_id, type="savings" if j==1 else "current", nickname=f"Demo account {j}", account_number=f"00000000{i:02d}{j:02d}", currency="INR", balance_minor=balance, status="active", opened_at=start))
            data["cards"].append(dict(card_id=f"demo-card-{i}-{j}", customer_id=customer_id, account_id=account_id, network="Visa", last4=f"{i:02d}{j:02d}", kind="debit", status="active", expiry_month=12, expiry_year=2029))
    return data

async def main() -> None:
    try:
        await ensure_indexes()
        db = get_db()
        keys = dict(customers="customer_id", accounts="account_id", transactions="txn_id", cards="card_id")
        for collection, rows in build_seed().items():
            for row in rows:
                await db[collection].update_one({keys[collection]: row[keys[collection]]}, {"$set": row}, upsert=True)
        print("Fictional bank demo data is ready.")
    finally:
        await close_bank()

if __name__ == "__main__":
    asyncio.run(main())
