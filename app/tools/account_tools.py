from app.bank.service import get_balances
from app.tools.registry import Arguments, ToolContext, ToolSpec, registry

async def balances(ctx: ToolContext, args: Arguments) -> dict:
    rows = [row.model_dump() for row in await get_balances(ctx.customer_id)]
    return {"accounts": rows, "display": {"type": "balances", "accounts": rows}}

registry["get_balances"] = ToolSpec("get_balances", "Get the signed-in customer's own account balances.", Arguments, balances)
