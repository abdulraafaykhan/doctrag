"""Multi-turn chat: plans each message, gathers documents and/or web pages, streams a cited answer."""
import asyncio
import json
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter
from sse_starlette.sse import EventSourceResponse

from app.config import get_settings
from app.schemas import ChatRequest
from app.services.citations import CitationFilter
from app.services.llm import stream_answer
from app.services.textfix import normalize
from app.services.memory import clear_session, get_full_history, get_recent, list_sessions, save_message
from app.services.retrieval import ready_document_names, search
from app.services.router import plan_turn
from app.services.web import build_web_sources, gather_web

router = APIRouter(tags=["chat"])

WEB_OFF_MESSAGE = (
    "This question needs the web, but **web search is turned off**, so I can only answer from your uploaded documents.\n\n"
    "Switch the 🌐 control next to the message box to **On** (always search the web) or **Auto** "
    "(I decide when to search), then ask again."
)
NO_DOCS_MESSAGE = (
    "Web search is turned off, so I work only from your uploaded documents, and none are ready yet.\n\n"
    "Upload a file on the left, or switch the 🌐 control to **Auto** or **On** to chat without documents."
)


def _doc_source(h: dict) -> dict:
    loc = h.get("location") or (f"p.{h['page']}" if h.get("page") else "")
    return {
        "type": "document", "filename": h["filename"], "url": None, "location": loc, "page": h.get("page"),
        "kind": h.get("kind", "text"), "doc_id": h["doc_id"], "chunk_index": h["chunk_index"],
        "score": round(h["score"], 4), "text": h["text"], "snippet": h["text"][:400],
    }


async def _documents(question: str, doc_ids: list[str] | None) -> list[dict]:
    """Vector search, keeping only passages that are plausibly relevant (unless the user picked documents)."""
    s = get_settings()
    hits = await search(question, doc_ids=doc_ids)
    if not doc_ids:
        hits = [h for h in hits if h["score"] >= s.min_doc_score]
    return [_doc_source(h) for h in hits]


async def run_chat(req: ChatRequest):
    """Events: status* -> meta -> token* -> done   (or error at any point)."""
    s = get_settings()
    session_id = req.session_id or uuid.uuid4().hex
    asked_at = datetime.now(timezone.utc)

    try:
        history = await get_recent(session_id, s.history_turns)
        doc_ids = req.doc_ids or None
        names = await ready_document_names(doc_ids)

        yield {"type": "status", "text": "Thinking…"}
        plan = await plan_turn(history, req.message, names, req.web)

        blocked = None
        if req.web == "off":  # pure document mode: never touch the web, say so when the web is what the user needs
            if plan.wants_web and not plan.wants_docs:
                blocked = WEB_OFF_MESSAGE
            elif not names:
                blocked = NO_DOCS_MESSAGE
        if blocked:
            yield {"type": "meta", "session_id": session_id, "search_query": plan.question, "sources": [], "notes": [],
                   "plan": {"mode": "off", "documents": False, "web": False, "blocked": "web_off" if blocked is WEB_OFF_MESSAGE else "no_documents", "urls": [], "web_query": ""}}
            yield {"type": "token", "t": blocked}
            await save_message(session_id, "user", req.message, created_at=asked_at)
            await save_message(session_id, "assistant", blocked, sources=[])
            yield {"type": "done", "session_id": session_id, "cited": []}
            return

        doc_task = asyncio.create_task(_documents(plan.question, doc_ids)) if plan.use_documents else None
        web_pages, notes = [], []
        if plan.search_web:
            yield {"type": "status", "text": "Reading " + (", ".join(u.split("//")[-1][:40] for u in plan.urls) if plan.urls else "the web") + "…"}
            result = await gather_web(plan.urls, plan.web_query)
            web_pages, notes = result.pages, result.notes
        if doc_task:
            yield {"type": "status", "text": "Searching your documents…"}

        doc_sources = await doc_task if doc_task else []
        web_sources = await build_web_sources(web_pages, plan.question)
        sources = doc_sources + web_sources
        for i, src in enumerate(sources, start=1):
            src["n"] = i
        public = [{k: v for k, v in src.items() if k != "text"} for src in sources]  # UI gets snippet, not full text
        yield {
            "type": "meta", "session_id": session_id, "search_query": plan.question, "sources": public, "notes": notes,
            "plan": {"mode": req.web, "documents": plan.use_documents, "web": plan.search_web, "urls": plan.urls, "web_query": plan.web_query},
        }

        flt = CitationFilter(len(sources))
        parts: list[str] = []
        async for token in stream_answer(history, req.message, sources, notes, plan.use_documents, req.web):
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
        await save_message(session_id, "user", req.message, created_at=asked_at)
        await save_message(session_id, "assistant", answer, sources=stored_sources)
        yield {"type": "done", "session_id": session_id, "cited": sorted(flt.cited)}
    except Exception as e:  # noqa: BLE001
        yield {"type": "error", "detail": str(e)[:300]}


@router.post("/chat")
async def chat(req: ChatRequest):
    if req.stream:

        async def event_stream():
            async for ev in run_chat(req):
                yield {"event": ev["type"], "data": json.dumps(ev)}

        return EventSourceResponse(event_stream())

    # Non-streaming: collect everything and return one JSON object (handy for Swagger testing).
    answer, meta, done, error = [], {}, {}, None
    async for ev in run_chat(req):
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


@router.get("/sessions")
async def sessions():
    """List recent conversations (for the sidebar)."""
    return await list_sessions()


@router.get("/sessions/{session_id}/history")
async def history(session_id: str):
    return await get_full_history(session_id)


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str):
    return {"session_id": session_id, "messages_deleted": await clear_session(session_id)}