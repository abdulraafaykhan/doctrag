"""Only this module reads customer bank data."""
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
