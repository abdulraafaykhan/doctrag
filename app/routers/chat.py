"""Owned chat orchestration with parallel tools and one grounded answer."""
import asyncio
import logging
import traceback
import time
import json
from datetime import datetime, timezone
from contextlib import aclosing
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException
from typing import Annotated

from app.auth.security import CurrentUser, get_current_user
from app.services.audit import audit_event
from app.services.attachments import attachment_events, resolve_attachments
from sse_starlette.sse import EventSourceResponse

from app.config import get_settings
from app.schemas import ChatError, ChatRequest, ChatResponse, SessionResponse, SessionDeleted, MessageResponse
from app.services.citations import CitationFilter
from app.services.llm import FIXED_REPLIES, NOTHING_FOUND_MESSAGE, DATA_UNAVAILABLE_MESSAGE, ACCOUNT_AMOUNT_MESSAGE, stream_answer, valid_account_amounts, prepare_answer_context
from app.services.textfix import normalize
from app.services.memory import clear_session, create_session, require_session, touch_session, get_full_history, get_recent, list_sessions, save_message
from app.services.retrieval import ready_document_names
from app.services.router import plan_turn
from app.services.prompting import build_history, PromptBudgetExceeded
from app.services.groq_runtime import AssistantBusy
from app.services.rate_limits import check_rate_limit, rate_message
from app.tools.registry import ToolContext, execute
router = APIRouter(tags=["chat"], dependencies=[Depends(get_current_user)])
log = logging.getLogger(__name__)
chat_windows = {}
CHAT_ERROR_MESSAGE = "The assistant could not complete your request. Please try again."


def _doc_source(h: dict) -> dict:
    loc = h.get("location") or (f"p.{h['page']}" if h.get("page") else "")
    return {
        "type": "document", "filename": h["filename"], "url": None, "location": loc, "page": h.get("page"),
        "kind": h.get("kind", "text"), "doc_id": h["doc_id"], "chunk_index": h["chunk_index"],
        "score": round(h["score"], 4), "text": h["text"], "snippet": h["text"][:400],
    }


async def run_chat(req: ChatRequest, user: CurrentUser) -> AsyncIterator[dict]:
    """Events: status* -> meta -> token* -> done   (or error at any point)."""
    session_id = req.session_id
    asked_at = datetime.now(timezone.utc)

    try:
        s = get_settings()
        history = await get_recent(session_id, s.history_max_messages, user_id=user.user_id)
        history = build_history(history, max_messages=s.history_max_messages,
            token_budget=s.history_token_budget, old_answer_trim_chars=s.old_answer_trim_chars)
        async for event in attachment_events(req.attachment_ids, user.user_id, session_id):
            yield event
            if event["type"] == "error":
                return
        attached = await resolve_attachments(req.attachment_ids, user.user_id, session_id) if req.attachment_ids else []
        attachment_refs = [{"id": str(row["_id"]), "filename": row["filename"]} for row in attached]
        names = await ready_document_names(owner_id=user.user_id, session_id=session_id)

        yield {"type": "status", "text": "Thinking…"}
        plan = await plan_turn(history, req.message, names)
        sources = []
        account_data = None
        cards = []
        notes = []
        calls = plan.tool_calls()
        fixed = FIXED_REPLIES.get(plan.action) if not calls else None
        if fixed is not None:
            await audit_event("tool_call", user_id=user.user_id, session_id=session_id,
                              tool="reply_without_data", outcome="ok", latency_ms=0)
        if calls:
            ctx = ToolContext(user_id=user.user_id, customer_id=user.customer_id, session_id=session_id)
            results = await asyncio.gather(*(execute(call.name, ctx, call.arguments) for call in calls))
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
        sources = list({(src["doc_id"], src["chunk_index"]): src for src in sources}.values())
        if fixed is None:
            prepared = prepare_answer_context(history, req.message, sources,
                account_data=account_data, unavailable=bool(notes))
            history, sources = prepared.history, prepared.sources
            if not sources and account_data is None:
                fixed = NOTHING_FOUND_MESSAGE
        public = [{k: v for k, v in src.items() if k != "text"} for src in sources]
        yield {"type": "meta", "session_id": session_id, "search_query": plan.question,
               "sources": public, "account_cards": cards, "notes": notes,
               "plan": {"action": plan.action, "tools": [call.name for call in calls]}}
        if fixed is not None:
            if fixed not in (FIXED_REPLIES["greeting"], FIXED_REPLIES["thanks"]):
                await audit_event("refusal", user_id=user.user_id, session_id=session_id,
                    outcome="out_of_scope" if plan.action == "out_of_scope" else
                            "nothing_found" if fixed == NOTHING_FOUND_MESSAGE else "data_unavailable")
            yield {"type": "token", "t": fixed}
            await save_message(session_id, "user", req.message, created_at=asked_at, user_id=user.user_id, attachments=attachment_refs)
            await save_message(session_id, "assistant", fixed, sources=[], user_id=user.user_id)
            await audit_event("chat", user_id=user.user_id, session_id=session_id)
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
                clean = normalize(flt.feed(token))  # plain ASCII hyphens/spaces so Markdown tables and rules render
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
                await audit_event("refusal", user_id=user.user_id, session_id=session_id, outcome="invalid_amount")
            yield {"type": "token", "t": answer}
        stored_sources = [{**{k: v for k, v in src.items() if k != "snippet"}, "cited": src["n"] in flt.cited} for src in public]
        # Save only after a complete answer, so history never holds half-finished turns.
        await save_message(session_id, "user", req.message, created_at=asked_at, user_id=user.user_id, attachments=attachment_refs)
        await save_message(session_id, "assistant", answer, sources=stored_sources, user_id=user.user_id)
        await audit_event("chat", user_id=user.user_id, session_id=session_id)
        yield {"type": "done", "session_id": session_id, "cited": sorted(flt.cited)}
    except AssistantBusy as error:
        await audit_event("rate_limited", user_id=user.user_id, session_id=session_id, outcome="provider")
        yield {"type": "error", "detail": str(error)}
    except PromptBudgetExceeded:
        await audit_event("refusal", user_id=user.user_id, session_id=session_id, outcome="prompt_budget")
        yield {"type": "error", "detail": "The request is too large to answer. Please ask a shorter question."}
    except Exception as e:  # noqa: BLE001
        # Trace locations aid diagnosis without logging exception messages or locals.
        locations = [(frame.f_code.co_name, line) for frame, line in traceback.walk_tb(e.__traceback__)]
        log.error("Chat failed: exception_type=%s locations=%s", type(e).__name__, locations)
        yield {"type": "error", "detail": CHAT_ERROR_MESSAGE}


@router.post("/chat", response_model=ChatResponse | ChatError)
async def chat(req: ChatRequest, user: Annotated[CurrentUser, Depends(get_current_user)]):
    await require_session(req.session_id, user.user_id)
    await resolve_attachments(req.attachment_ids, user.user_id, req.session_id)
    retry_after = check_rate_limit(chat_windows, user.user_id, get_settings().chat_rate_per_minute, time.monotonic())
    if retry_after is not None:
        await audit_event("rate_limited", user_id=user.user_id, session_id=req.session_id, outcome="user")
        raise HTTPException(429, rate_message(retry_after), headers={"Retry-After": str(retry_after)})
    await touch_session(req.session_id, user.user_id)
    if req.stream:

        async def event_stream():
            async with aclosing(run_chat(req, user)) as events:
                async for ev in events:
                    yield {"event": ev["type"], "data": json.dumps(ev)}

        return EventSourceResponse(event_stream())

    # Non-streaming: collect everything and return one JSON object (handy for Swagger testing).
    answer, meta, done, error = [], {}, {}, None
    async for ev in run_chat(req, user):
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
        "cited": done.get("cited", []),
        "plan": meta.get("plan"),
        "notes": meta.get("notes", []),
        "account_cards": meta.get("account_cards", []),
        "sources": [{k: v for k, v in src.items() if k != "snippet"} for src in meta.get("sources", [])],
    }


@router.post("/sessions", response_model=SessionResponse, status_code=201)
async def new_session(user: Annotated[CurrentUser, Depends(get_current_user)]):
    return await create_session(user.user_id)


@router.get("/sessions", response_model=list[SessionResponse])
async def sessions(user: Annotated[CurrentUser, Depends(get_current_user)]):
    return await list_sessions(user.user_id)


@router.get("/sessions/{session_id}/history", response_model=list[MessageResponse])
async def history(session_id: str, user: Annotated[CurrentUser, Depends(get_current_user)]):
    return await get_full_history(session_id, user_id=user.user_id)


@router.delete("/sessions/{session_id}", response_model=SessionDeleted)
async def delete_session(session_id: str, user: Annotated[CurrentUser, Depends(get_current_user)]):
    count = await clear_session(session_id, user_id=user.user_id)
    await audit_event("session_deleted", user_id=user.user_id, session_id=session_id)
    return {"session_id": session_id, "messages_deleted": count}
