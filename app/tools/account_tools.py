"""Plain handlers; identity is supplied exclusively through ToolContext."""
from pydantic import Field
from app.bank.service import AccountType, get_balances, get_recent_transactions, get_card_status
from app.tools.registry import Arguments, ToolContext, ToolSpec, registry

async def balances(ctx: ToolContext, args: Arguments) -> dict:
    rows = [row.model_dump() for row in await get_balances(ctx.customer_id)]
    return {"accounts": rows, "display": {"type": "balances", "accounts": rows}}

registry["get_balances"] = ToolSpec("get_balances", "Get the signed-in customer's own account balances.", Arguments, balances)


class TransactionArguments(Arguments):
    account_type: AccountType | None = None
    limit: int = Field(default=5, ge=1, le=20)


async def recent_transactions(ctx: ToolContext, args: TransactionArguments) -> dict:
    rows = [row.model_dump() for row in await get_recent_transactions(
        ctx.customer_id, account_type=args.account_type, limit=args.limit)]
    return {"transactions": rows, "display": {"type": "transactions", "transactions": rows}}


async def card_status(ctx: ToolContext, args: Arguments) -> dict:
    rows = [row.model_dump() for row in await get_card_status(ctx.customer_id)]
    return {"cards": rows, "display": {"type": "cards", "cards": rows}}


registry["get_recent_transactions"] = ToolSpec("get_recent_transactions",
    "Get the newest transactions across your own accounts, optionally by account type; limit defaults to 5, maximum 20.",
    TransactionArguments, recent_transactions)
registry["get_card_status"] = ToolSpec("get_card_status",
    "Get your own cards' kind, masked last four digits, status and expiry.", Arguments, card_status)
