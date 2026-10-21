import asyncio
import json
import os
import sys
import types

import pytest

os.environ.update(MONGODB_URI="x", GROQ_API_KEY="x", GEMINI_API_KEY="x")
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
_db = sys.modules.setdefault("app.db", types.ModuleType("app.db"))
for name in ("documents_col", "chunks_col", "messages_col", "users_col", "sessions_col", "audit_log_col"):
    if not hasattr(_db, name):
        setattr(_db, name, lambda: None)
sys.modules["app.db"] = _db

from app.routers import chat
from app.schemas import ChatRequest
from app.services import llm, router
from app.services.router import Plan
from app.config import Settings, get_settings
from app.auth.security import CurrentUser, get_current_user
TEST_USER = CurrentUser(user_id="user-a", username="alice", customer_id="demo-customer-001")


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch):
    # Tests must not depend on or load the developer's local configuration file.
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    monkeypatch.setenv("GROQ_REWRITE_MODEL", "llama-3.1-8b-instant")
    monkeypatch.setenv("ANSWER_TEMPERATURE", "0.2")
    monkeypatch.setenv("MIN_DOC_SCORE", "0.6")
    monkeypatch.setenv("JWT_SECRET", "test-only-secret-with-at-least-32-characters")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def run(c):
    return asyncio.run(c)


def _chat(monkeypatch, plan, doc_hits, tokens, doc_ids=None, fail=False):
    saved, calls = [], []
    async def names(*a, **k): return ["report.pdf"]
    async def hist(*a, **k): return [{"role": "assistant", "content": "earlier answer [1]"}]
    async def pl(*a, **k): return plan
    async def srch(q, doc_ids=None): return doc_hits
    async def audit(*a, **kw): pass
    monkeypatch.setattr(chat, "audit_event", audit)
    async def save(sid, role, content, **kw): saved.append((role, content, kw))
    async def stream(history, q, sources):
        calls.append(sources)
        if fail:
            raise RuntimeError("PRIVATE message and token")
        for t in tokens: yield t
    for key, value in dict(ready_document_names=names, get_recent=hist, plan_turn=pl,
                           search=srch, save_message=save, stream_answer=stream).items():
        monkeypatch.setattr(chat, key, value)
    async def go():
        return [e async for e in chat.run_chat(ChatRequest(message="hi", session_id="s1", doc_ids=doc_ids), TEST_USER)]
    events = run(go())
    if plan.action != "search_documents" or not any(h["score"] >= 0.6 for h in doc_hits):
        assert calls == []
    return events, saved


HIT = lambda sc: {"filename": "report.pdf", "text": "Revenue was 10M.", "page": 2,
                  "doc_id": "d1", "chunk_index": 0, "score": sc, "location": "p.2", "kind": "text"}

def test_flow_docs_with_clean_citations(monkeypatch):
    evs, saved = _chat(monkeypatch, Plan(question="q"), [HIT(0.9), HIT(0.3)],
                       ["Revenue was 10M", "【1†L1-", "L4】 and 【7】."])
    kinds = [e["type"] for e in evs]
    assert kinds[0] == "status" and "meta" in kinds and kinds[-1] == "done"
    meta = next(e for e in evs if e["type"] == "meta")
    assert len(meta["sources"]) == 1 and meta["sources"][0]["n"] == 1 and "text" not in meta["sources"][0]  # weak hit dropped
    text = "".join(e["t"] for e in evs if e["type"] == "token")
    assert text == "Revenue was 10M[1] and." or text.replace(" .", ".") == "Revenue was 10M[1] and."
    assert evs[-1]["cited"] == [1]
    assert saved[1][1] == text and saved[1][2]["sources"][0]["cited"] is True


def test_textfix_normalises_lookalike_characters():
    from app.services.textfix import normalize
    assert normalize("|\u2011\u2011\u2011|") == "|---|"
    assert normalize("175\u202fB and 16\u2009k") == "175 B and 16 k"
    assert normalize("a\u200bb") == "ab"

@pytest.mark.parametrize("action,expected", [
    ("greeting", llm.GREETING_MESSAGE), ("thanks", llm.THANKS_MESSAGE),
    ("out_of_scope", llm.OUT_OF_SCOPE_MESSAGE),
])
def test_fixed_replies_are_saved_without_answer_call(monkeypatch, action, expected):
    events, saved = _chat(monkeypatch, Plan(question="q", action=action), [], ["UNEXPECTED"])
    assert "".join(e["t"] for e in events if e["type"] == "token") == expected
    assert saved[1][1] == expected
    assert events[-1] == {"type": "done", "session_id": "s1", "cited": []}


@pytest.mark.parametrize("hits", [[], [HIT(0.3)]])
@pytest.mark.parametrize("doc_ids", [None, ["d1"]])
def test_nothing_found_never_calls_answer_model(monkeypatch, hits, doc_ids):
    events, saved = _chat(monkeypatch, Plan(question="q"), hits, ["UNEXPECTED"], doc_ids=doc_ids)
    assert "".join(e["t"] for e in events if e["type"] == "token") == llm.NOTHING_FOUND_MESSAGE
    assert saved[1][1] == llm.NOTHING_FOUND_MESSAGE


def test_chat_failure_is_generic_and_does_not_save_partial_answer(monkeypatch, caplog):
    events, saved = _chat(monkeypatch, Plan(question="q"), [HIT(0.8)], [], fail=True)
    assert events[-1] == {"type": "error", "detail": chat.CHAT_ERROR_MESSAGE}
    assert saved == []
    assert "RuntimeError" in caplog.text
    assert "PRIVATE" not in caplog.text


@pytest.mark.parametrize("output", ["no json", "[]", "null", "{}",
    '{"action":"unknown","question":"q"}', '{"action":"greeting","question":7}',
    '{"action":"search_documents","question":"  "}',
    '{"action":"greeting","question":"q","extra":true}'])
def test_malformed_router_falls_back_to_original_message(monkeypatch, output):
    async def create(**kw):
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=output))])
    monkeypatch.setattr(router, "client", lambda: types.SimpleNamespace(chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=create))))
    assert run(router.plan_turn([], "original message", [])) == Plan(question="original message")


def test_router_failure_falls_back(monkeypatch):
    def broken(): raise RuntimeError("private")
    monkeypatch.setattr(router, "client", broken)
    assert run(router.plan_turn([], "original", [])) == Plan(question="original")


def test_router_rewrites_document_followup(monkeypatch):
    async def create(**kw):
        assert kw["model"] == "llama-3.1-8b-instant"
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=types.SimpleNamespace(
            content=json.dumps({"action": "search_documents", "question": "What was revenue in the report?"})))])
    monkeypatch.setattr(router, "client", lambda: types.SimpleNamespace(chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=create))))
    assert run(router.plan_turn([], "and revenue?", ["report.pdf"])).question == "What was revenue in the report?"


def test_answer_prompt_and_history(monkeypatch):
    captured = {}
    async def create(**kw):
        captured.update(kw)
        async def tokens():
            yield types.SimpleNamespace(choices=[types.SimpleNamespace(delta=types.SimpleNamespace(content="Fact [1]"))])
        return tokens()
    monkeypatch.setattr(llm, "client", lambda: types.SimpleNamespace(chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=create))))
    async def go():
        return [t async for t in llm.stream_answer([{"role":"assistant", "content":"old [9]"}], "question", [{**HIT(.8), "n":1}])]
    assert run(go()) == ["Fact [1]"]
    assert captured["messages"][0]["content"] == llm.BANK_SYSTEM_PROMPT
    assert captured["messages"][1]["content"] == "old"
    assert captured["temperature"] == .2


def test_answer_empty_sources_guard(monkeypatch):
    def unexpected(): raise AssertionError("No answer call allowed")
    monkeypatch.setattr(llm, "client", unexpected)
    async def go(): return [t async for t in llm.stream_answer([], "q", [])]
    assert run(go()) == [llm.NOTHING_FOUND_MESSAGE]

@pytest.fixture
def api(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app = FastAPI()
    app.include_router(chat.router)
    app.dependency_overrides[get_current_user] = lambda: TEST_USER
    async def owned(*args): return {"_id": "s1", "user_id": TEST_USER.user_id}
    monkeypatch.setattr(chat, "require_session", owned)
    return TestClient(app)


@pytest.mark.parametrize("stream", [False, True])
def test_http_reply_contract(monkeypatch, api, stream):
    async def events(req, user):
        yield {"type": "status", "text": "Thinking…"}
        yield {"type": "meta", "session_id": "s1", "sources": [], "plan": {"action": "greeting"}}
        yield {"type": "token", "t": llm.GREETING_MESSAGE}
        yield {"type": "done", "session_id": "s1", "cited": []}
    monkeypatch.setattr(chat, "run_chat", events)
    response = api.post("/chat", json={"message": "Hello", "session_id": "s1", "stream": stream})
    assert response.status_code == 200
    if stream:
        assert "text/event-stream" in response.headers["content-type"]
        for event in ("status", "meta", "token", "done"):
            assert f"event: {event}" in response.text
    else:
        assert response.json()["answer"] == llm.GREETING_MESSAGE
        assert response.json()["session_id"] == "s1"


def test_root_redirect_and_swagger_file_picker(monkeypatch):
    async def noop(): pass
    for name in ("close_mongo", "connect_to_mongo", "ensure_indexes"):
        monkeypatch.setattr(_db, name, noop, raising=False)
    async def status(): return "READY"
    monkeypatch.setattr(_db, "vector_index_status", status, raising=False)
    from app import main
    async def sweep(): return 0
    monkeypatch.setattr(main, "sweep_stuck_documents", sweep)
    from fastapi.testclient import TestClient
    with TestClient(main.app) as client:
        response = client.get("/", follow_redirects=False)
        assert response.headers["location"] == "/docs"
        schema = client.get("/openapi.json").json()
        assert "web" not in schema["components"]["schemas"]["ChatRequest"]["properties"]
        body = schema["paths"]["/documents"]["post"]["requestBody"]["content"]["multipart/form-data"]["schema"]
        upload = schema["components"]["schemas"][body["$ref"].split("/")[-1]]
        assert upload["properties"]["files"]["items"]["format"] == "binary"


