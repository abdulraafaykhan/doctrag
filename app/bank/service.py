"""Only this module reads customer bank data."""
from typing import Literal
from pydantic import BaseModel, ConfigDict
from app.bank.db import get_db

class Balance(BaseModel):
    model_config = ConfigDict(strict=True)
    type: str
    nickname: str
    masked_number: str
    currency: str
    balance_minor: int
    balance: str

def format_money(minor: int, currency: str) -> str:
    # Demo accounts use INR (two decimal minor units). No float conversion.
    if currency != "INR":
        raise ValueError("Unsupported currency")
    sign = "-" if minor < 0 else ""
    whole, fraction = divmod(abs(minor), 100)
    return f"{currency} {sign}{whole:,}.{fraction:02d}"

async def get_balances(customer_id: str) -> list[Balance]:
    rows = await get_db().accounts.find({"customer_id": customer_id},
        {"_id": 0, "type": 1, "nickname": 1, "account_number": 1,
         "currency": 1, "balance_minor": 1}).to_list()
    return [Balance(type=row["type"], nickname=row["nickname"],
        masked_number="****" + row["account_number"][-4:], currency=row["currency"],
        balance_minor=row["balance_minor"], balance=format_money(row["balance_minor"], row["currency"]))
        for row in rows]


AccountType = Literal["savings", "current", "credit_card"]


class RecentTransaction(BaseModel):
    model_config = ConfigDict(strict=True)
    type: AccountType
    nickname: str
    masked_number: str
    posted_at: str
    description: str
    direction: Literal["credit", "debit"]
    currency: str
    amount_minor: int
    amount: str


class CardStatus(BaseModel):
    model_config = ConfigDict(strict=True)
    kind: Literal["debit", "credit"]
    last4: str
    status: Literal["active", "blocked", "expired"]
    expiry_month: int
    expiry_year: int
    expiry: str


async def get_recent_transactions(
    customer_id: str, account_type: AccountType | None = None, limit: int = 5,
) -> list[RecentTransaction]:
    """Newest transactions across the caller's matching accounts, limited in total."""
    if type(limit) is not int or not 1 <= limit <= 20:
        raise ValueError("Limit must be between 1 and 20")
    if account_type not in (None, "savings", "current", "credit_card"):
        raise ValueError("Unsupported account type")
    query = {"customer_id": customer_id}
    if account_type is not None:
        query["type"] = account_type
    db = get_db()
    accounts = await db.accounts.find(query, {"_id": 0, "account_id": 1,
        "type": 1, "nickname": 1, "account_number": 1, "currency": 1}).to_list()
    owned = {row["account_id"]: row for row in accounts}
    if not owned:
        return []
    # Transaction rows have no customer_id; resolve permitted account ids first.
    rows = await db.transactions.find({"account_id": {"$in": list(owned)}},
        {"_id": 0, "account_id": 1, "posted_at": 1, "description": 1,
         "direction": 1, "amount_minor": 1}).sort("posted_at", -1).limit(limit).to_list()
    return [RecentTransaction(type=owned[row["account_id"]]["type"],
        nickname=owned[row["account_id"]]["nickname"],
        masked_number="****" + owned[row["account_id"]]["account_number"][-4:],
        posted_at=row["posted_at"].isoformat(), description=row["description"],
        direction=row["direction"], currency=owned[row["account_id"]]["currency"],
        amount_minor=row["amount_minor"],
        amount=format_money(row["amount_minor"], owned[row["account_id"]]["currency"]))
        for row in rows]


async def get_card_status(customer_id: str) -> list[CardStatus]:
    rows = await get_db().cards.find({"customer_id": customer_id},
        {"_id": 0, "kind": 1, "last4": 1, "status": 1,
         "expiry_month": 1, "expiry_year": 1}).to_list()
    return [CardStatus(kind=row["kind"], last4=row["last4"], status=row["status"],
        expiry_month=row["expiry_month"], expiry_year=row["expiry_year"],
        expiry=f"{row['expiry_month']:02d}/{row['expiry_year']}") for row in rows]
