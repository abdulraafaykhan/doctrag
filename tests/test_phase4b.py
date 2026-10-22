"""Phase 4b: isolated bank reads, plain handlers, and parallel mixed turns."""
import ast
import asyncio
import copy
import json
import inspect
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from pydantic import ValidationError
from test_chat import HIT, TEST_USER, isolated_settings
from test_phase2 import Collection
from scripts.seed_bank import build_seed
from app.bank import service
from app.tools import account_tools, document_tools, registry as reg
from app.services import router, llm
from app.routers import chat
from app.schemas import ChatRequest


def run(coroutine):
    return asyncio.run(coroutine)


@pytest.fixture
def bank(monkeypatch):
    data = build_seed()
    db = NS()
    for name, rows in data.items():
        collection = Collection()
        collection.rows = copy.deepcopy(rows)
        setattr(db, name, collection)
    monkeypatch.setattr(service, "get_db", lambda: db)
    return db


@pytest.mark.parametrize("customer,count", [("demo-customer-001", 1), ("demo-customer-002", 2), ("demo-customer-003", 3)])
def test_new_service_isolation(bank, customer, count):
    rows = run(service.get_recent_transactions(customer))
    owned = [a for a in bank.accounts.rows if a["customer_id"] == customer]
    assert len(rows) == 5
    assert {r.masked_number for r in rows} <= {"****" + a["account_number"][-4:] for a in owned}
    assert [r.posted_at for r in rows] == sorted((r.posted_at for r in rows), reverse=True)
    assert bank.accounts.queries[-1] == {"customer_id": customer}
    assert set(bank.transactions.queries[-1]["account_id"]["$in"]) == {a["account_id"] for a in owned}
    for row in rows:
        assert type(row.amount_minor) is int
        assert row.amount == service.format_money(row.amount_minor, row.currency)
        assert not {"account_id", "customer_id", "account_number", "txn_id"} & row.model_dump().keys()
    cards = run(service.get_card_status(customer))
    assert len(cards) == count
    assert bank.cards.queries[-1] == {"customer_id": customer}
    assert all(c.expiry == "12/2029" and c.status == "active" for c in cards)
    assert {c.last4 for c in cards} == {c["last4"] for c in bank.cards.rows if c["customer_id"] == customer}
    assert len(run(service.get_balances(customer))) == count


def test_transaction_type_limit_and_empty(bank):
    rows = run(service.get_recent_transactions("demo-customer-002", "current", 20))
    assert len(rows) == 20 and {r.type for r in rows} == {"current"}
    assert bank.accounts.queries[-1] == {"customer_id":"demo-customer-002", "type":"current"}
    assert run(service.get_recent_transactions("demo-customer-002", "credit_card")) == []
    assert run(service.get_recent_transactions("missing")) == []
    assert run(service.get_card_status("missing")) == []


@pytest.mark.parametrize("args", [{"limit":0}, {"limit":21}, {"limit":True}, {"limit":"5"},
    {"account_type":"foreign"}, {"account_id":"foreign"}, {"customer_id":"foreign"}, {"user_id":"foreign"}])
def test_transaction_arguments_reject_invalid(args):
    with pytest.raises(ValidationError):
        account_tools.TransactionArguments.model_validate(args)


@pytest.mark.parametrize("limit", [0, 21, True, "5"])
def test_service_checks_limit_without_database(monkeypatch, limit):
    monkeypatch.setattr(service, "get_db", lambda: pytest.fail("Invalid arguments must not access DB"))
    with pytest.raises(ValueError):
        run(service.get_recent_transactions("trusted", limit=limit))


def test_plain_service_and_handler_boundaries():
    paths = list(Path("app/bank").glob("*.py")) + [Path("app/tools/account_tools.py"), Path("app/tools/document_tools.py")]
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            imports = ([node.module or ""] if isinstance(node, ast.ImportFrom)
                       else [alias.name for alias in node.names] if isinstance(node, ast.Import) else [])
            for name in imports:
                assert not any(part in name.split(".") for part in ("router", "routers", "llm", "groq", "crewai"))
    for name in ("get_balances", "get_recent_transactions", "get_card_status"):
        spec = reg.registry[name]
        assert issubclass(spec.arguments, reg.Arguments) and spec.read_only
        assert list(inspect.signature(spec.handler).parameters) == ["ctx", "args"]


def response(calls):
    return NS(choices=[NS(message=NS(tool_calls=[NS(function=NS(name=name, arguments=json.dumps(args))) for name, args in calls]))])


def router_client(create):
    return lambda: NS(chat=NS(completions=NS(create=create)))


def test_router_keeps_two_valid_calls_and_arguments(monkeypatch):
    async def create(**kw):
        assert kw["parallel_tool_calls"] is True
        return response([("get_recent_transactions", {"limit":21}), ("unknown", {}),
            ("get_recent_transactions", {"account_type":"current", "limit":7}),
            ("get_card_status", {}), ("get_balances", {})])
    monkeypatch.setattr(router, "client", router_client(create))
    plan = run(router.plan_turn([], "activity and cards", []))
    assert [(c.name, c.arguments) for c in plan.tool_calls()] == [
        ("get_recent_transactions", {"account_type":"current", "limit":7}), ("get_card_status", {})]


def test_router_data_wins_over_fixed_reply(monkeypatch):
    async def create(**kw):
        return response([("reply_without_data", {"kind":"greeting"}), ("get_card_status", {})])
    monkeypatch.setattr(router, "client", router_client(create))
    plan = run(router.plan_turn([], "hello, my cards?", []))
    assert plan.action == "get_card_status"
    assert len(plan.tool_calls()) == 1


@pytest.fixture
def turn(monkeypatch, bank):
    state = NS(audits=[], answers=[], saved=[], tokens=[], plan=None, events=[])
    async def history(*a, **kw): return []
    async def names(**kw): return []
    async def attachments(*a):
        if False: yield {}
    async def plan(*a): return state.plan
    async def save(*a, **kw): state.saved.append((a, kw))
    async def audit(event, **kw): state.audits.append((event, kw))
    async def search(*a, **kw): return [dict(HIT(.9), text="The overdraft fee is INR 50.00.")]
    async def answer(history, question, sources, **kw):
        state.answers.append((sources, kw))
        for token in state.tokens:
            yield token
        state.finished = True
    for key, value in dict(get_recent=history, ready_document_names=names, attachment_events=attachments,
                            plan_turn=plan, save_message=save, audit_event=audit, stream_answer=answer).items():
        monkeypatch.setattr(chat, key, value)
    monkeypatch.setattr(reg, "audit_event", audit)
    monkeypatch.setattr(document_tools, "search", search)
    async def go():
        async for event in chat.run_chat(ChatRequest(message="test question", session_id="trusted-session"), TEST_USER):
            if event["type"] == "token" and state.answers and "account_data" in state.answers[0][1]:
                assert state.finished
            state.events.append(event)
        return state.events
    state.go = go
    return state


def set_plan(turn, *calls):
    turn.plan = router.Plan(question="overdraft fee", action=calls[0][0],
        tools=[router.ToolCall(name=name, arguments=args) for name, args in calls])


def test_mixed_tools_parallel_one_answer(turn, monkeypatch):
    async def go():
        started = {name: asyncio.Event() for name in ("documents", "balances")}
        async def search(query, **kw):
            assert kw == {"owner_id":TEST_USER.user_id, "session_id":"trusted-session"}
            started["documents"].set()
            await asyncio.wait_for(started["balances"].wait(), 1)
            return [dict(HIT(.9), text="The overdraft fee is INR 50.00.")]
        async def balances(customer_id):
            assert customer_id == TEST_USER.customer_id
            started["balances"].set()
            await asyncio.wait_for(started["documents"].wait(), 1)
            return [service.Balance(type="savings", nickname="Demo", masked_number="****0001", currency="INR", balance_minor=12345, balance="INR 123.45")]
        monkeypatch.setattr(document_tools, "search", search)
        monkeypatch.setattr(account_tools, "get_balances", balances)
        return await turn.go()
    set_plan(turn, ("search_documents", {"query":"overdraft fee"}), ("get_balances", {}))
    turn.tokens = ["The fee is INR 50.00 [1]. ", "Your account shows INR 123.45."]
    events = run(go())
    assert len(turn.answers) == 1
    sources, kwargs = turn.answers[0]
    assert sources[0]["n"] == 1 and kwargs["account_data"]["accounts"][0]["balance"] == "INR 123.45"
    assert len([e for e in events if e["type"] == "token"]) == 1
    assert events[-1]["cited"] == [1]
    assert len([a for a in turn.audits if a[0] == "tool_call"]) == 2
    assert len(turn.saved) == 2


def test_transactions_and_cards_combined(turn):
    set_plan(turn, ("get_recent_transactions", {"limit":5}), ("get_card_status", {}))
    turn.tokens = ["Your card is active."]
    events = run(turn.go())
    assert events[-1]["type"] == "done" and len(turn.answers) == 1
    data = turn.answers[0][1]["account_data"]
    assert len(data["transactions"]) == 5 and len(data["cards"]) == 1
    assert {c["type"] for c in next(e for e in events if e["type"] == "meta")["account_cards"]} == {"transactions", "cards"}


@pytest.mark.parametrize("document_result,account_fails,expected_calls", [
    ("success", True, 1), ("empty", True, 0), ("error", True, 0), ("error", False, 1)])
def test_partial_and_total_failures(turn, monkeypatch, document_result, account_fails, expected_calls):
    async def search(*a, **kw):
        if document_result == "error": raise RuntimeError("PRIVATE")
        return [HIT(.9)] if document_result == "success" else []
    async def cards(customer_id):
        if account_fails: raise RuntimeError("PRIVATE")
        return []
    monkeypatch.setattr(document_tools, "search", search)
    monkeypatch.setattr(account_tools, "get_card_status", cards)
    set_plan(turn, ("search_documents", {"query":"fees"}), ("get_card_status", {}))
    turn.tokens = ["Available fact."]
    events = run(turn.go())
    assert events[-1]["type"] == "done"
    assert len(turn.answers) == expected_calls
    text = "".join(e["t"] for e in events if e["type"] == "token")
    if not expected_calls:
        assert text == llm.DATA_UNAVAILABLE_MESSAGE
    else:
        assert turn.answers[0][1]["unavailable"] is True
        assert next(e for e in events if e["type"] == "meta")["notes"] == [llm.DATA_UNAVAILABLE_MESSAGE]
    assert "PRIVATE" not in json.dumps(events) + json.dumps(turn.audits)
    assert len([a for a in turn.audits if a[0] == "tool_call"]) == 2


def test_empty_cards_are_authoritative(turn, monkeypatch):
    async def cards(customer_id): return []
    monkeypatch.setattr(account_tools, "get_card_status", cards)
    set_plan(turn, ("get_card_status", {}))
    turn.tokens = ["Your account shows no cards."]
    run(turn.go())
    assert len(turn.answers) == 1 and turn.answers[0][1]["account_data"] == {"cards":[]}


def test_transaction_amount_validation():
    data = {"transactions":[{"amount":"INR 25.50"}], "cards":[]}
    assert llm.valid_account_amounts("A debit of INR 25.50", data)
    assert not llm.valid_account_amounts("A debit of INR 99.00", data)
    assert llm.valid_account_amounts("Fee INR 50.00, debit INR 25.50", data, [{"text":"Fee INR 50.00"}])
    assert not llm.valid_account_amounts("Fee INR 51.00", data, [{"text":"Fee INR 50.00"}])


@pytest.mark.parametrize("stream", [False, True])
def test_http_native_router_tools_one_answer(turn, monkeypatch, stream):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.auth.security import get_current_user

    routed, answered = [], []
    balance = service.format_money(build_seed()["accounts"][0]["balance_minor"], "INR")
    async def route_create(**kw):
        routed.append(kw)
        return response([("search_documents", {"query":"overdraft fee"}), ("get_balances", {})])
    async def answer_create(**kw):
        answered.append(kw)
        async def chunks():
            for token in ("The fee is INR 50.00 [1]. ", f"Your account shows {balance}."):
                yield NS(choices=[NS(delta=NS(content=token))])
        return chunks()
    async def owned(*a): return {"_id":"trusted-session", "user_id":TEST_USER.user_id}
    async def resolve(*a): return []
    async def touch(*a): pass
    monkeypatch.setattr(router, "client", router_client(route_create))
    monkeypatch.setattr(llm, "client", router_client(answer_create))
    monkeypatch.setattr(chat, "plan_turn", router.plan_turn)
    monkeypatch.setattr(chat, "stream_answer", llm.stream_answer)
    monkeypatch.setattr(chat, "require_session", owned)
    monkeypatch.setattr(chat, "resolve_attachments", resolve)
    monkeypatch.setattr(chat, "touch_session", touch)
    app = FastAPI()
    app.include_router(chat.router)
    app.dependency_overrides[get_current_user] = lambda: TEST_USER
    with TestClient(app) as client:
        result = client.post("/chat", json={"session_id":"trusted-session",
            "message":"What is the overdraft fee and my balance?", "stream":stream})
    assert result.status_code == 200
    assert len(routed) == 1 and len(answered) == 1
    content = answered[0]["messages"][-1]["content"]
    assert "[1]" in content and "INR 50.00" in content and "ACCOUNT DATA" in content
    assert "****0101" in content
    assert TEST_USER.customer_id not in content and TEST_USER.user_id not in content
    tool_events = [kw for name, kw in turn.audits if name == "tool_call"]
    assert {kw["tool"] for kw in tool_events} == {"search_documents", "get_balances"}
    assert all(kw["outcome"] == "ok" for kw in tool_events)
    if stream:
        assert result.text.count("event: token") == 1
        assert "account_cards" in result.text
    else:
        assert result.json()["plan"]["tools"] == ["search_documents", "get_balances"]
        assert result.json()["account_cards"][0]["type"] == "balances"
        assert result.json()["cited"] == [1]
        assert balance in result.json()["answer"] and "INR 50.00" in result.json()["answer"]


def test_card_status_preserves_blocked_and_expired(bank):
    rows = [c for c in bank.cards.rows if c["customer_id"] == "demo-customer-002"]
    rows[0]["status"] = "blocked"
    rows[1]["status"] = "expired"
    cards = run(service.get_card_status("demo-customer-002"))
    assert [c.status for c in cards] == ["blocked", "expired"]


def test_no_data_both_searches_empty(turn, monkeypatch):
    async def search(*a, **kw): return []
    monkeypatch.setattr(document_tools, "search", search)
    set_plan(turn, ("search_documents", {"query":"one"}), ("search_documents", {"query":"two"}))
    events = run(turn.go())
    assert turn.answers == []
    assert [e["t"] for e in events if e["type"] == "token"] == [llm.NOTHING_FOUND_MESSAGE]


def test_duplicate_document_sources_numbered_once(turn):
    set_plan(turn, ("search_documents", {"query":"one"}), ("search_documents", {"query":"two"}))
    turn.tokens = ["The fee is INR 50.00 [1]."]
    events = run(turn.go())
    assert len(turn.answers) == 1 and len(turn.answers[0][0]) == 1
    assert next(e for e in events if e["type"] == "meta")["sources"][0]["n"] == 1
