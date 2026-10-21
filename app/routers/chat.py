"""Document-only multi-turn chat with cited streamed answers."""
import logging
import traceback
import json
from datetime import datetime, timezone
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends
from typing import Annotated

from app.auth.security import CurrentUser, get_current_user
from app.services.audit import audit_event
from sse_starlette.sse import EventSourceResponse

from app.config import get_settings
from app.schemas import ChatError, ChatRequest, ChatResponse, SessionResponse, SessionDeleted, MessageResponse
from app.services.citations import CitationFilter
from app.services.llm import FIXED_REPLIES, NOTHING_FOUND_MESSAGE, stream_answer
from app.services.textfix import normalize
from app.services.memory import clear_session, create_session, require_session, get_full_history, get_recent, list_sessions, save_message
from app.services.retrieval import ready_document_names, search
from app.services.router import plan_turn
router = APIRouter(tags=["chat"], dependencies=[Depends(get_current_user)])
log = logging.getLogger(__name__)
CHAT_ERROR_MESSAGE = "The assistant could not complete your request. Please try again."


def _doc_source(h: dict) -> dict:
    loc = h.get("location") or (f"p.{h['page']}" if h.get("page") else "")
    return {
        "type": "document", "filename": h["filename"], "url": None, "location": loc, "page": h.get("page"),
        "kind": h.get("kind", "text"), "doc_id": h["doc_id"], "chunk_index": h["chunk_index"],
        "score": round(h["score"], 4), "text": h["text"], "snippet": h["text"][:400],
    }


async def _documents(question: str, doc_ids: list[str] | None) -> list[dict]:
    """Apply the relevance threshold even when documents are explicitly selected."""
    s = get_settings()
    hits = await search(question, doc_ids=doc_ids)
    hits = [h for h in hits if h["score"] >= s.min_doc_score]
    return [_doc_source(h) for h in hits]


async def run_chat(req: ChatRequest, user: CurrentUser) -> AsyncIterator[dict]:
    """Events: status* -> meta -> token* -> done   (or error at any point)."""
    session_id = req.session_id
    asked_at = datetime.now(timezone.utc)

    try:
        s = get_settings()
        history = await get_recent(session_id, s.history_turns, user_id=user.user_id)
        doc_ids = req.doc_ids or None
        names = await ready_document_names(doc_ids)

        yield {"type": "status", "text": "Thinking…"}
        plan = await plan_turn(history, req.message, names)
        sources = []
        fixed = FIXED_REPLIES.get(plan.action)
        if fixed is None:
            yield {"type": "status", "text": "Searching your documents…"}
            sources = await _documents(plan.question, doc_ids)
            if not sources:
                fixed = NOTHING_FOUND_MESSAGE
        for i, source in enumerate(sources, start=1):
            source["n"] = i
        public = [{k: v for k, v in src.items() if k != "text"} for src in sources]
        yield {"type": "meta", "session_id": session_id, "search_query": plan.question,
               "sources": public, "notes": [], "plan": {"action": plan.action}}
        if fixed is not None:
            yield {"type": "token", "t": fixed}
            await save_message(session_id, "user", req.message, created_at=asked_at, user_id=user.user_id)
            await save_message(session_id, "assistant", fixed, sources=[], user_id=user.user_id)
            await audit_event("chat", user_id=user.user_id, session_id=session_id)
            yield {"type": "done", "session_id": session_id, "cited": []}
            return
        flt = CitationFilter(len(sources))
        parts: list[str] = []
        async for token in stream_answer(history, req.message, sources):
            clean = normalize(flt.feed(token))  # plain ASCII hyphens/spaces so Markdown tables and rules render
            if clean:
                parts.append(clean)
                yield {"type": "token", "t": clean}
        tail = normalize(flt.flush())
        if tail:
            parts.append(tail)
            yield {"type": "token", "t": tail}

        answer = "".join(parts)
        stored_sources = [{**{k: v for k, v in src.items() if k != "snippet"}, "cited": src["n"] in flt.cited} for src in public]
        # Save only after a complete answer, so history never holds half-finished turns.
        await save_message(session_id, "user", req.message, created_at=asked_at, user_id=user.user_id)
        await save_message(session_id, "assistant", answer, sources=stored_sources, user_id=user.user_id)
        await audit_event("chat", user_id=user.user_id, session_id=session_id)
        yield {"type": "done", "session_id": session_id, "cited": sorted(flt.cited)}
    except Exception as e:  # noqa: BLE001
        # Trace locations aid diagnosis without logging exception messages or locals.
        locations = [(frame.f_code.co_name, line) for frame, line in traceback.walk_tb(e.__traceback__)]
        log.error("Chat failed: exception_type=%s locations=%s", type(e).__name__, locations)
        yield {"type": "error", "detail": CHAT_ERROR_MESSAGE}


@router.post("/chat", response_model=ChatResponse | ChatError)
async def chat(req: ChatRequest, user: Annotated[CurrentUser, Depends(get_current_user)]):
    await require_session(req.session_id, user.user_id)
    if req.stream:

        async def event_stream():
            async for ev in run_chat(req, user):
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
    return {"session_id": session_id, "messages_deleted": await clear_session(session_id, user_id=user.user_id)}
