"""Validated, server-context-only tool execution with content-free audit events."""
from dataclasses import dataclass
from collections.abc import Awaitable, Callable
from time import monotonic
from pydantic import BaseModel, ConfigDict
from app.services.audit import audit_event

class ToolContext(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)
    user_id: str
    customer_id: str
    session_id: str

class Arguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    arguments: type[BaseModel]
    handler: Callable[[ToolContext, BaseModel], Awaitable[dict]]
    read_only: bool = True

    def schema(self) -> dict:
        return {"type": "function", "function": {"name": self.name,
            "description": self.description, "parameters": self.arguments.model_json_schema()}}

registry: dict[str, ToolSpec] = {}

def schemas() -> list[dict]:
    return [spec.schema() for spec in registry.values()]

async def execute(name: str, ctx: ToolContext, arguments: dict) -> dict:
    start = monotonic()
    outcome = "error"
    try:
        spec = registry[name]
        args = spec.arguments.model_validate(arguments)
        result = await spec.handler(ctx, args)
        outcome = "ok"
        return result
    except Exception:
        return {"error": "data_unavailable"}
    finally:
        await audit_event("tool_call", user_id=ctx.user_id, session_id=ctx.session_id,
            tool=name if name in registry else "unknown", outcome=outcome,
            latency_ms=int((monotonic() - start) * 1000))
