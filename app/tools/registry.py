"""Validated, server-context-only tool execution with content-free audit events."""

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from time import monotonic

from pydantic import BaseModel, ConfigDict

from app.services.audit import audit_event


class ToolContext(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)
    customer_id: str | None = None
    # The server injects this context; model argument schemas never include it.
    session_id: str


class Arguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    arguments: type[BaseModel]
    handler: Callable[[ToolContext, BaseModel], Awaitable[dict]]
    requires_verification: bool = False

    def schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.arguments.model_json_schema(),
            },
        }


registry: dict[str, ToolSpec] = {}


def schemas() -> list[dict]:
    return [spec.schema() for spec in registry.values()]


async def execute(name: str, ctx: ToolContext, arguments: dict) -> dict:
    start = monotonic()
    outcome = "error"
    try:
        spec = registry[name]
        # Recheck authority immediately before execution, including verification expiry.
        from app.sessions.store import require_session

        session = await require_session(ctx.session_id)
        verified = session.get("verified")
        ctx = ToolContext(
            session_id=ctx.session_id,
            customer_id=verified["customer_id"] if verified else None,
        )
        if spec.requires_verification and ctx.customer_id is None:
            logging.getLogger(__name__).info(
                "tool_blocked session=%s tool=%s", ctx.session_id[:6], name
            )
            outcome = "blocked"
            return {"error": "verification_required"}
        args = spec.arguments.model_validate(arguments)
        result = await spec.handler(ctx, args)
        logging.getLogger(__name__).info(
            "tool_allowed session=%s tool=%s", ctx.session_id[:6], name
        )
        outcome = "ok"
        return result
    except Exception:
        return {"error": "data_unavailable"}
    finally:
        await audit_event(
            "tool_call",
            session_id=ctx.session_id,
            tool=name if name in registry else "unknown",
            outcome=outcome,
            latency_ms=int((monotonic() - start) * 1000),
        )
