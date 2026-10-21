import asyncio
import json
from types import SimpleNamespace as NS
import pytest
from test_chat import isolated_settings, TEST_USER
from app.bank import service
from app.tools import registry as reg
from app.services import router, llm
from app.routers import chat
from app.schemas import ChatRequest
from scripts.seed_bank import build_seed, CUSTOMER_IDS
import ast
from pathlib import Path
DEMO_USERS = next(ast.literal_eval(n.value) for n in ast.parse(Path("scripts/seed_users.py").read_text(encoding="utf-8")).body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "DEMO_USERS" for t in n.targets))

def run(c): return asyncio.run(c)

def test_schemas_no_identity():
    assert set(reg.registry) == {"get_balances", "search_documents"}
    for schema in reg.schemas():
        fields = json.dumps(schema["function"]["parameters"]).lower()
        assert not any(word in fields for word in ("customer", "user_id", "session_id", "account_number", "account_id"))

def test_seed_consistency():
    data = build_seed()
    assert CUSTOMER_IDS == tuple(row[3] for row in DEMO_USERS)
    assert len(data["customers"]) == 3
    for customer in CUSTOMER_IDS:
        assert 1 <= sum(a["customer_id"] == customer for a in data["accounts"]) <= 3
    for a in data["accounts"]:
        tx = [t for t in data["transactions"] if t["account_id"] == a["account_id"]]
        assert len(tx) >= 30
        balance = 0
        for t in tx:
            assert type(t["amount_minor"]) is int and t["amount_minor"] > 0
            balance += t["amount_minor"] * (1 if t["direction"] == "credit" else -1)
            assert balance == t["balance_after_minor"]
        assert a["balance_minor"] == balance
        assert any(c["account_id"] == a["account_id"] for c in data["cards"])

def test_balances_filter(monkeypatch):
    class Accounts:
        def find(self, query, projection):
            assert query == {"customer_id": "trusted"}
            class Cursor:
                async def to_list(self): return [dict(type="savings", nickname="Demo", account_number="1234567890", currency="INR", balance_minor=12345)]
            return Cursor()
    monkeypatch.setattr(service, "get_db", lambda: NS(accounts=Accounts()))
    row = run(service.get_balances("trusted"))[0].model_dump()
    assert row["balance"] == "INR 123.45" and row["masked_number"] == "****7890"
    assert "1234567890" not in json.dumps(row)

def test_router_retry(monkeypatch):
    calls = []
    async def create(**kw):
        calls.append(kw)
        assert "tools" in kw and "response_format" not in kw
        if len(calls) == 1: raise RuntimeError("private")
        return NS(choices=[NS(message=NS(tool_calls=[NS(function=NS(name="get_balances", arguments='{}'))]))])
    monkeypatch.setattr(router, "client", lambda: NS(chat=NS(completions=NS(create=create))))
    assert run(router.plan_turn([], "balance", [])).action == "get_balances"
    assert len(calls) == 2

@pytest.mark.parametrize("name,args", [("unknown", "{}"), ("get_balances", '{"customer_id":"foreign"}'), ("search_documents", '{"query":" "}')])
def test_router_validation_fallback(monkeypatch, name, args):
    calls = []
    async def create(**kw):
        calls.append(kw)
        return NS(choices=[NS(message=NS(tool_calls=[NS(function=NS(name=name, arguments=args))]))])
    monkeypatch.setattr(router, "client", lambda: NS(chat=NS(completions=NS(create=create))))
    assert run(router.plan_turn([], "original", [])) == router.Plan(question="original")
    assert len(calls) == 2

def test_failure_audited(monkeypatch):
    events = []
    async def audit(event, **kw): events.append((event, kw))
    monkeypatch.setattr(reg, "audit_event", audit)
    assert run(reg.execute("get_balances", reg.ToolContext(user_id="u", customer_id="c", session_id="s"), {"customer_id":"foreign"})) == {"error":"data_unavailable"}
    assert events[0][0] == "tool_call" and events[0][1]["outcome"] == "error"
    assert "foreign" not in json.dumps(events)

@pytest.mark.parametrize("bad", [False, True])
def test_account_buffered_one_answer(monkeypatch, bad):
    calls, saved = [], []
    async def hist(*a, **kw): return []
    async def names(**kw): return []
    async def attachments(*a):
        if False: yield {}
    async def plan(*a): return router.Plan(question="balance", action="get_balances")
    async def execute(name, ctx, args):
        assert ctx.customer_id == TEST_USER.customer_id and args == {}
        return {"accounts":[{"balance":"INR 123.45"}], "display":{"type":"balances"}}
    async def answer(*a, **kw):
        calls.append(kw)
        yield "Your account shows INR "
        yield "999.99" if bad else "123.45"
        calls.append("finished")
    async def save(*a, **kw): saved.append(a)
    async def audit(*a, **kw): pass
    for key, value in dict(get_recent=hist, ready_document_names=names, attachment_events=attachments, plan_turn=plan, execute=execute, stream_answer=answer, save_message=save, audit_event=audit).items(): monkeypatch.setattr(chat, key, value)
    async def go():
        events = []
        async for event in chat.run_chat(ChatRequest(message="balance", session_id="s"), TEST_USER):
            if event["type"] == "token": assert calls[-1] == "finished"
            events.append(event)
        return events
    events = run(go())
    assert [e["t"] for e in events if e["type"] == "token"] == [llm.ACCOUNT_AMOUNT_MESSAGE if bad else "Your account shows INR 123.45"]
    assert len(calls) == 2 and len(saved) == 2


def test_document_context_and_success_audit(monkeypatch):
    from app.tools import document_tools
    seen, events = [], []
    async def search(query, **kw):
        seen.append((query, kw))
        return [{"score": .9}, {"score": .1}]
    async def audit(event, **kw): events.append((event, kw))
    monkeypatch.setattr(document_tools, "search", search)
    monkeypatch.setattr(reg, "audit_event", audit)
    ctx = reg.ToolContext(user_id="trusted-user", customer_id="trusted-customer", session_id="trusted-session")
    result = run(reg.execute("search_documents", ctx, {"query":"minimum balance"}))
    assert result == {"documents":[{"score":.9}]}
    assert seen == [("minimum balance", {"owner_id":"trusted-user", "session_id":"trusted-session"})]
    assert events[0][1]["outcome"] == "ok"
    assert "minimum balance" not in json.dumps(events)


def test_account_answer_model_receives_result_once(monkeypatch):
    calls = []
    async def create(**kw):
        calls.append(kw)
        async def tokens(): yield NS(choices=[NS(delta=NS(content="your account shows INR 123.45"))])
        return tokens()
    monkeypatch.setattr(llm, "client", lambda: NS(chat=NS(completions=NS(create=create))))
    async def go(): return [t async for t in llm.stream_answer([], "balance", [], account_data={"accounts":[{"balance":"INR 123.45"}]})]
    assert run(go()) == ["your account shows INR 123.45"]
    assert len(calls) == 1 and "INR 123.45" in calls[0]["messages"][-1]["content"]


def test_only_first_valid_tool_honoured(monkeypatch):
    async def create(**kw):
        return NS(choices=[NS(message=NS(tool_calls=[NS(function=NS(name="get_balances", arguments="{}")), NS(function=NS(name="search_documents", arguments=json.dumps({"query":"fees"})))]))])
    monkeypatch.setattr(router, "client", lambda: NS(chat=NS(completions=NS(create=create))))
    assert run(router.plan_turn([], "balance and fees", [])).action == "get_balances"
