"""Anonymous chat orchestration with parallel tools and one grounded answer."""

import asyncio
import json
import logging
import time
import traceback
from collections.abc import AsyncIterator
from contextlib import aclosing
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from sse_starlette.sse import EventSourceResponse

from app.config import get_settings
from app.logging_setup import otp_input
from app.schemas import (
    ChatError,
    ChatRequest,
    ChatResponse,
    MessageResponse,
    SessionDeleted,
    SessionResponse,
)
from app.services.attachments import attachment_events, resolve_attachments
from app.services.audit import audit_event
from app.services.citations import CitationFilter
from app.services.groq_runtime import AssistantBusy
from app.services.llm import (
    ACCOUNT_AMOUNT_MESSAGE,
    DATA_UNAVAILABLE_MESSAGE,
    FIXED_REPLIES,
    NOTHING_FOUND_MESSAGE,
    prepare_answer_context,
    stream_answer,
    valid_account_amounts,
)
from app.services.memory import get_full_history, get_recent, save_message
from app.services.prompting import PromptBudgetExceeded, build_history
from app.services.rate_limits import check_rate_limit, rate_message
from app.services.retrieval import ready_document_names
from app.services.router import plan_turn
from app.services.textfix import normalize
from app.sessions.store import (
    create_session,
    match_path,
    require_session,
    session_header,
    session_turn,
    touch_session,
    wipe,
)
from app.tools.registry import ToolContext, execute, registry
from app.verification import messages as verification_messages
from app.verification.flow import advance, start
from app.verification.intent import is_personal_request
from app.verification.masking import mask_sensitive

router = APIRouter(tags=["chat"])
log = logging.getLogger(__name__)
chat_windows = {}
ip_chat_windows = {}
CHAT_ERROR_MESSAGE = "The assistant could not complete your request. Please try again."


def _doc_source(h: dict) -> dict:
    loc = h.get("location") or (f"p.{h['page']}" if h.get("page") else "")
    return {
        "scope": h.get("scope", "shared"),
        "type": "document",
        "filename": h["filename"],
        "url": None,
        "location": loc,
        "page": h.get("page"),
        "kind": h.get("kind", "text"),
        "doc_id": h["doc_id"],
        "chunk_index": h["chunk_index"],
        "score": round(h["score"], 4),
        "text": h["text"],
        "snippet": h["text"][:400],
    }


async def fixed_events(
    session_id: str, answer: str, *, clear_history: bool = False
) -> AsyncIterator[dict]:
    await save_message(session_id, "assistant", answer)
    yield {
        "type": "meta",
        "session_id": session_id,
        "sources": [],
        "account_cards": [],
        "notes": [],
        "clear_history": clear_history,
    }
    yield {"type": "token", "t": answer}
    yield {"type": "done", "session_id": session_id, "cited": []}


async def run_chat(
    req: ChatRequest, session: dict, history: list[dict], *, continue_flow: bool = False
) -> AsyncIterator[dict]:
    """At most two parallel tools feed exactly one grounded answer call."""
    session_id = session["_id"]
    try:
        s = get_settings()
        history = build_history(
            history,
            max_messages=s.history_max_messages,
            token_budget=s.history_token_budget,
            old_answer_trim_chars=s.old_answer_trim_chars,
        )
        async for event in attachment_events(req.attachment_ids, session_id):
            yield event
            if event["type"] == "error":
                return
        names = await ready_document_names(
            session_id=session_id, include_private=session.get("verified") is not None
        )

        yield {"type": "status", "text": "Thinking…"}
        clear_personal_request = is_personal_request(req.message)
        plan = None
        try:
            plan = await plan_turn(history, req.message, names)
        except (AssistantBusy, PromptBudgetExceeded):
            # A provider/budget failure must not prevent deterministic verification.
            session = await require_session(session_id)
            if session.get("verified") or not clear_personal_request:
                raise
        sources = []
        account_data = None
        cards = []
        notes = []
        calls = plan.tool_calls() if plan is not None else []
        # Gate the entire mixed turn before any private tool runs; replay it after OTP.
        session = await require_session(session_id)
        protected = [
            call for call in calls if registry[call.name].requires_verification
        ]
        # A misrouted document search or fixed reply must not bypass the verification flow.
        if not session.get("verified") and (protected or clear_personal_request):
            for call in protected:
                await audit_event(
                    "tool_call",
                    session_id=session_id,
                    tool=call.name,
                    outcome="blocked",
                )
                log.info("tool_blocked session=%s tool=%s", session_id[:6], call.name)
            if not protected:
                log.info("verification_safety_net session=%s", session_id[:6])
            prompt = await start(session, req.message)
            async for event in fixed_events(session_id, prompt):
                yield event
            return
        # A missing plan is handled only by the verification branch above.
        assert plan is not None
        fixed = FIXED_REPLIES.get(plan.action) if not calls else None
        if fixed is not None:
            await audit_event(
                "tool_call",
                session_id=session_id,
                tool="reply_without_data",
                outcome="ok",
                latency_ms=0,
            )
        if calls:
            ctx = ToolContext(session_id=session_id)
            results = await asyncio.gather(
                *(execute(call.name, ctx, call.arguments) for call in calls)
            )
            failed = False
            for call, result in zip(calls, results):
                if "error" in result:
                    failed = True
                elif call.name == "search_documents":
                    sources.extend(_doc_source(h) for h in result["documents"])
                else:
                    if account_data is None:
                        account_data = {}
                    for key, value in result.items():
                        if key != "display":
                            account_data.setdefault(key, []).extend(value)
                    cards.append(result["display"])
            # Empty account results are authoritative (e.g. no matching cards).
            if not sources and account_data is None:
                fixed = DATA_UNAVAILABLE_MESSAGE if failed else NOTHING_FOUND_MESSAGE
            elif failed:
                notes.append(DATA_UNAVAILABLE_MESSAGE)
        # A passage retrieved by two searches receives just one citation number.
        sources = list(
            {(src["doc_id"], src["chunk_index"]): src for src in sources}.values()
        )
        if fixed is None:
            prepared = prepare_answer_context(
                history,
                req.message,
                sources,
                account_data=account_data,
                unavailable=bool(notes),
            )
            history, sources = prepared.history, prepared.sources
            if not sources and account_data is None:
                fixed = NOTHING_FOUND_MESSAGE
        public = [dict(src) for src in sources]
        yield {
            "type": "meta",
            "session_id": session_id,
            "search_query": plan.question,
            "sources": public,
            "account_cards": cards,
            "notes": notes,
            "plan": {"action": plan.action, "tools": [call.name for call in calls]},
        }
        if fixed is not None:
            if fixed not in (FIXED_REPLIES["greeting"], FIXED_REPLIES["thanks"]):
                await audit_event(
                    "refusal",
                    session_id=session_id,
                    outcome="out_of_scope"
                    if plan.action == "out_of_scope"
                    else "nothing_found"
                    if fixed == NOTHING_FOUND_MESSAGE
                    else "data_unavailable",
                )
            if continue_flow:
                fixed += "\n\n" + verification_messages.CONTINUE
            yield {"type": "token", "t": fixed}
            await save_message(session_id, "assistant", fixed, sources=[])
            await audit_event("chat", session_id=session_id)
            yield {"type": "done", "session_id": session_id, "cited": []}
            return
        flt = CitationFilter(len(sources))
        parts: list[str] = []
        answer_options = {}
        if account_data is not None:
            answer_options["account_data"] = account_data
        if notes:
            answer_options["unavailable"] = True
        answer_stream = stream_answer(history, req.message, sources, **answer_options)
        async with aclosing(answer_stream):
            async for token in answer_stream:
                clean = normalize(
                    flt.feed(token)
                )  # plain ASCII hyphens/spaces so Markdown tables and rules render
                if clean:
                    parts.append(clean)
                    if account_data is None:
                        yield {"type": "token", "t": clean}
        tail = normalize(flt.flush())
        if tail:
            parts.append(tail)
            if account_data is None:
                yield {"type": "token", "t": tail}

        answer = "".join(parts)
        if account_data is not None:
            if not valid_account_amounts(answer, account_data, sources):
                answer = ACCOUNT_AMOUNT_MESSAGE
                flt.cited.clear()
                await audit_event(
                    "refusal", session_id=session_id, outcome="invalid_amount"
                )
            yield {"type": "token", "t": answer}
        if continue_flow:
            suffix = "\n\n" + verification_messages.CONTINUE
            answer += suffix
            yield {"type": "token", "t": suffix}
        stored_sources = [
            {
                **{k: v for k, v in src.items() if k != "snippet"},
                "cited": src["n"] in flt.cited,
            }
            for src in public
        ]
        # Persist only a complete assistant answer; input was already stored masked.
        await save_message(
            session_id, "assistant", answer, sources=stored_sources, account_cards=cards
        )
        await audit_event("chat", session_id=session_id)
        yield {"type": "done", "session_id": session_id, "cited": sorted(flt.cited)}
    except AssistantBusy as error:
        await audit_event("rate_limited", session_id=session_id, outcome="provider")
        yield {"type": "error", "detail": str(error)}
    except PromptBudgetExceeded:
        await audit_event("refusal", session_id=session_id, outcome="prompt_budget")
        yield {
            "type": "error",
            "detail": "The request is too large to answer. Please ask a shorter question.",
        }
    except Exception as e:  # noqa: BLE001
        # Trace locations aid diagnosis without logging exception messages or locals.
        locations = [
            (frame.f_code.co_name, line)
            for frame, line in traceback.walk_tb(e.__traceback__)
        ]
        log.error(
            "Chat failed: exception_type=%s locations=%s", type(e).__name__, locations
        )
        yield {"type": "error", "detail": CHAT_ERROR_MESSAGE}


@router.post("/chat", response_model=ChatResponse | ChatError)
async def chat(
    req: ChatRequest, request: Request, session: Annotated[dict, Depends(session_turn)]
):
    session_id = session["_id"]
    # Only the direct peer is trusted; forwarded headers cannot select a rate-limit bucket.
    ip = request.client.host if request.client else "unknown"
    settings = get_settings()
    for windows, key, limit, outcome in (
        (chat_windows, session_id, settings.chat_rate_per_minute, "session"),
        (ip_chat_windows, ip, settings.chat_rate_per_ip_per_minute, "chat_ip"),
    ):
        retry = check_rate_limit(windows, key, limit, time.monotonic())
        if retry:
            await audit_event("rate_limited", session_id=session_id, outcome=outcome)
            raise HTTPException(
                429, rate_message(retry), headers={"Retry-After": str(retry)}
            )
    attached = await resolve_attachments(req.attachment_ids, session_id)
    session = await touch_session(session_id)
    state = session.get("flow", {}).get("state", "idle")
    otp_input.set(state == "awaiting_otp")
    # Parsing uses raw input. All subsequent history and model work uses masked input.
    flow_reply = await advance(session, req.message, ip)
    safe_message = mask_sensitive(req.message, awaiting_otp=state == "awaiting_otp")
    history = await get_recent(session_id, settings.history_max_messages)
    if not flow_reply.clear_history:
        await save_message(
            session_id,
            "user",
            safe_message,
            attachments=[
                {"id": str(row["_id"]), "filename": row["filename"]} for row in attached
            ],
        )
    req = req.model_copy(update={"message": flow_reply.replay or safe_message})
    events = (
        fixed_events(
            session_id, flow_reply.answer, clear_history=flow_reply.clear_history
        )
        if flow_reply.answer
        else run_chat(
            req,
            session,
            history,
            continue_flow=state != "idle" and flow_reply.replay is None,
        )
    )
    if req.stream:

        async def event_stream():
            async with aclosing(events) as streamed:
                async for ev in streamed:
                    yield {"event": ev["type"], "data": json.dumps(ev)}

        return EventSourceResponse(event_stream())

    # Non-streaming: collect everything and return one JSON object (handy for Swagger testing).
    answer, meta, done, error = [], {}, {}, None
    async for ev in events:
        if ev["type"] == "meta":
            meta = ev
        elif ev["type"] == "token":
            answer.append(ev["t"])
        elif ev["type"] == "done":
            done = ev
        elif ev["type"] == "error":
            error = ev["detail"]
    if error:
        return {"error": error}
    return {
        "session_id": meta.get("session_id"),
        "answer": "".join(answer),
        "clear_history": meta.get("clear_history", False),
        "cited": done.get("cited", []),
        "plan": meta.get("plan"),
        "notes": meta.get("notes", []),
        "account_cards": meta.get("account_cards", []),
        "sources": [
            {k: v for k, v in src.items() if k != "snippet"}
            for src in meta.get("sources", [])
        ],
    }


@router.post("/sessions", response_model=SessionResponse, status_code=201)
async def new_session():
    return await create_session()


@router.get("/sessions/{session_id}/history", response_model=list[MessageResponse])
async def history(session_id: str, session: Annotated[dict, Depends(session_header)]):
    match_path(session, session_id)
    return await get_full_history(session_id)


@router.delete("/sessions/{session_id}", response_model=SessionDeleted)
async def delete_session(
    session_id: str, session: Annotated[dict, Depends(session_turn)]
):
    match_path(session, session_id)
    counts = await wipe(session_id)
    return {
        "session_id": session_id,
        "messages_deleted": counts["messages"] if counts else 0,
    }
