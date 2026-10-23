"""R1 exercises the HTTP path with fake Mongo, providers and a controllable clock."""

import asyncio
import json
import logging
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from app.config import get_settings
from app.routers import chat
from app.services import ingestion, maintenance, retrieval
from app.services.router import Plan
from app.sessions import store
from app.tools import document_tools
from app.tools.registry import ToolContext, execute, registry, schemas
from app.verification import lockout, messages
from app.verification.masking import mask_sensitive
from scripts.generate_bank_data import generate_data
from scripts.seed_bank import build_seed


def run(coro):
    return asyncio.run(coro)


def new(world):
    return world.client.post("/sessions").json()["session_id"]


def headers(sid):
    return {"X-Session-Id": sid}


def post(world, sid, message, **changes):
    return world.client.post(
        "/chat",
        headers=headers(sid),
        json={"message": message, "stream": False, **changes},
    )


def verify(world, sid, account="880000001001", mobile="9000000101"):
    post(world, sid, "My balance?")
    post(world, sid, account)
    post(world, sid, mobile)
    return post(world, sid, "123456")


def test_anonymous_contract_and_header_is_authority(world):
    sid, other = new(world), new(world)
    assert len(sid) >= 40 and sid != other
    assert world.client.get(f"/sessions/{sid}/history").status_code == 404
    assert (
        world.client.get(f"/sessions/{sid}/history", headers=headers(other)).status_code
        == 404
    )
    assert world.client.get("/sessions").status_code == 405
    assert world.client.post("/auth/login").status_code == 404
    assert post(world, sid, "Hello", session_id=sid).status_code == 422
    assert (
        world.client.get(f"/sessions/{sid}/history", headers=headers(sid)).json() == []
    )
    assert not any("user_id" in row for row in world.sessions.rows)


def test_verification_replays_once_and_never_prompts_identifiers(world):
    sid = new(world)
    reply = verify(world, sid)
    assert (
        reply.status_code == 200
        and reply.json()["account_cards"][0]["type"] == "balances"
    )
    assert len(world.answers) == 1
    row = world.sessions.rows[0]
    assert row["verified"]["customer_id"] == "CUST-001" and row["flow"] == {
        "state": "idle"
    }
    history = world.client.get(f"/sessions/{sid}/history", headers=headers(sid)).json()
    encoded = json.dumps(history)
    for value in ("880000001001", "9000000101", "123456"):
        assert value not in encoded
    assert (
        "Account ending 1001" in encoded
        and "Mobile ending 0101" in encoded
        and "OTP entered" in encoded
    )
    prompted = json.dumps(world.plans + world.answers)
    for value in ("880000001001", "9000000101", "123456"):
        assert value not in prompted
    assert len([row for row in history if row["content"] == "My balance?"]) == 1
    assert "session_id" not in world.audit_log.rows[0]
    assert sid not in json.dumps(world.audit_log.rows, default=str)


@pytest.mark.parametrize(
    "account,mobile",
    [
        ("880000001001", "9000000199"),
        ("111111111111", "9000000101"),
        ("880000002001", "9000000101"),
    ],
)
def test_wrong_pair_same_generic_response(world, account, mobile):
    sid = new(world)
    post(world, sid, "My balance?")
    post(world, sid, account)
    assert post(world, sid, mobile).json()["answer"] == messages.RETRY
    assert not world.sessions.rows[0]["verified"]


@pytest.mark.parametrize("account", ["880000001001", "111111111111"])
def test_failures_accumulate_across_sessions_and_lock_even_unknown(world, account):
    for attempt in range(3):
        sid = new(world)
        post(world, sid, "My balance?")
        post(world, sid, account)
        answer = post(world, sid, "9000000199").json()["answer"]
        assert answer == (messages.FAILED if attempt == 2 else messages.RETRY)
    assert world.sessions.rows[-1]["flow"] == {"state": "idle"}
    row = world.verify_locks.rows[0]
    assert row["_id"] == lockout.account_hash(account) and "account" not in row
    sid = new(world)
    post(world, sid, "My balance?")
    post(world, sid, account)
    assert post(world, sid, "9000000101").json()["answer"] == messages.FAILED


def test_lock_expiry_and_success_reset_failures(world, monkeypatch):
    sid = new(world)
    post(world, sid, "My balance?")
    post(world, sid, "880000001001")
    post(world, sid, "9000000199")
    assert world.verify_locks.rows[0]["failures"] == 1
    post(world, sid, "880000001001")
    post(world, sid, "9000000101")
    post(world, sid, "654321")
    assert not world.verify_locks.rows
    now = datetime.now(timezone.utc)
    world.verify_locks.rows.append(
        {
            "_id": lockout.account_hash("111111111111"),
            "failures": 3,
            "locked_until": now - timedelta(seconds=1),
        }
    )
    assert (
        run(lockout.is_locked("111111111111")) is False and not world.verify_locks.rows
    )


@pytest.mark.parametrize("step", [0, 1, 2])
@pytest.mark.parametrize("word", ["cancel", "stop", "never mind"])
def test_cancel_every_step(world, step, word):
    sid = new(world)
    post(world, sid, "My balance?")
    if step >= 1:
        post(world, sid, "880000001001")
    if step >= 2:
        post(world, sid, "9000000101")
    assert post(world, sid, word).json()["answer"] == messages.CANCELLED
    assert world.sessions.rows[0]["flow"] == {"state": "idle"}


def test_otp_format_expiry_resend_and_no_try_cost(world, monkeypatch):
    sid = new(world)
    post(world, sid, "My balance?")
    post(world, sid, "8800-0000-1001")
    post(world, sid, "+91 90000 00101")
    assert post(world, sid, "12345").json()["answer"] == messages.OTP_FORMAT
    assert world.sessions.rows[0]["flow"]["tries"] == 0
    old = store.now_utc() - timedelta(minutes=6)
    world.sessions.rows[0]["flow"]["otp_sent_at"] = old
    assert post(world, sid, "123456").json()["answer"] == messages.OTP_EXPIRED
    assert "demo mode" in post(world, sid, "resend").json()["answer"]
    assert post(world, sid, "000000").json()["account_cards"]


def test_unrelated_question_keeps_pending_and_appends_continue(world):
    sid = new(world)
    post(world, sid, "My balance?")
    answer = post(world, sid, "Hello").json()["answer"]
    assert answer.endswith(messages.CONTINUE)
    assert world.sessions.rows[0]["flow"]["pending_question"] == "My balance?"


@pytest.mark.parametrize("idle,age", [(10, 10), (1, 30)])
def test_verification_idle_and_hard_expiry(world, monkeypatch, idle, age):
    sid = new(world)
    verify(world, sid)
    now = store.now_utc()
    verified = world.sessions.rows[0]["verified"]
    verified.update(
        verified_at=now - timedelta(minutes=age),
        last_verified_activity_at=now - timedelta(minutes=idle),
    )
    monkeypatch.setattr(store, "now_utc", lambda: now)
    before = world.sessions.rows[0]["last_activity_at"]
    world.client.get(f"/sessions/{sid}/history", headers=headers(sid))
    assert world.sessions.rows[0]["verified"] is None
    assert world.sessions.rows[0]["last_activity_at"] == before
    assert post(world, sid, "My balance?").json()["answer"] == messages.ACCOUNT


@pytest.mark.parametrize("lazy", [True, False])
def test_idle_wipe_cascades_and_keeps_audit(world, monkeypatch, lazy):
    sid = new(world)
    post(world, sid, "Hello")
    now = store.now_utc()
    world.sessions.rows[0]["last_activity_at"] = now - timedelta(minutes=30)
    world.documents.rows.append(
        {"scope": "user", "session_id": sid, "status": "ready", "created_at": now}
    )
    world.chunks.rows.append({"scope": "user", "session_id": sid})
    if lazy:
        response = world.client.get(f"/sessions/{sid}/history", headers=headers(sid))
        assert response.status_code == 404
    else:
        run(maintenance.purge_expired(now))
    assert (
        not world.sessions.rows
        and not world.messages.rows
        and not world.documents.rows
        and not world.chunks.rows
    )
    assert any(row["event"] == "session_expired" for row in world.audit_log.rows)


def test_new_chat_wipes_and_audit_survives(world):
    sid = new(world)
    post(world, sid, "Hello")
    assert (
        world.client.delete(f"/sessions/{sid}", headers=headers(sid)).status_code == 200
    )
    assert not world.sessions.rows and not world.messages.rows
    assert world.audit_log.rows[-1]["event"] == "session_wiped"


def test_account_tools_recheck_session_and_ignore_context_customer(world, monkeypatch):
    sid = new(world)
    seen = []

    async def handler(ctx, args):
        seen.append(ctx.customer_id)
        return {"ok": True}

    spec = registry["get_balances"]
    monkeypatch.setitem(registry, "get_balances", replace(spec, handler=handler))
    assert (
        run(
            execute(
                "get_balances", ToolContext(session_id=sid, customer_id="CUST-010"), {}
            )
        )["error"]
        == "verification_required"
    )
    assert not seen
    verify(world, sid)
    run(
        execute("get_balances", ToolContext(session_id=sid, customer_id="CUST-010"), {})
    )
    assert seen and set(seen) == {"CUST-001"}
    schema = json.dumps(schemas())
    for field in (
        "user_id",
        "customer_id",
        "account_id",
        "session_id",
        "account_number",
        "mobile",
    ):
        assert field not in schema


def test_attachment_verification_and_hidden_after_expiry(world):
    sid = new(world)
    result = world.client.post(
        f"/sessions/{sid}/attachments",
        headers=headers(sid),
        files={"files": ("a.txt", b"hello")},
    )
    assert (
        result.status_code == 403
        and result.json()["detail"]["code"] == "verification_required"
    )
    assert retrieval.visibility_filter(sid, False) == {"scope": "shared"}


def test_customer_change_deletes_private_files(world):
    sid = new(world)
    verify(world, sid)
    world.documents.rows.append({"session_id": sid, "scope": "user"})
    world.chunks.rows.append({"session_id": sid, "scope": "user"})
    world.sessions.rows[0]["verified"] = None
    response = verify(world, sid, "880000002001", "9000000102")
    assert response.json()["clear_history"] is True
    assert world.messages.rows and all(
        row["content"] == messages.CUSTOMER_CHANGED for row in world.messages.rows
    )
    assert world.audit_log.rows[-2]["event"] == "customer_changed" or any(
        row["event"] == "customer_changed" for row in world.audit_log.rows
    )
    assert not world.documents.rows and not world.chunks.rows
    assert world.sessions.rows[0]["verified"]["customer_id"] == "CUST-002"


@pytest.mark.parametrize(
    "value",
    [
        "880000001001",
        "8800 0000 1001",
        "8800-0000-1001",
        "9000000101",
        "+91 90000 00101",
        "919000000101",
    ],
)
def test_mask_account_and_mobile(value):
    out = mask_sensitive(value)
    assert value not in out and ("ending 1001" in out or "ending 0101" in out)


def test_six_digits_only_masked_at_otp_step():
    assert (
        mask_sensitive("Pincode 400001 reference 123456 amount 654321")
        == "Pincode 400001 reference 123456 amount 654321"
    )
    assert mask_sensitive("OTP 123456", awaiting_otp=True) == "OTP OTP entered"
    assert mask_sensitive("INR 1,234,567.89") == "INR 1,234,567.89"


def test_masking_applies_to_general_chat_before_router(world):
    sid = new(world)
    post(
        world, sid, "My reference 400001 and mobile +91 9000000101 account 880000001001"
    )
    assert "400001" in world.plans[0][1]
    assert (
        "880000001001" not in world.plans[0][1]
        and "9000000101" not in world.plans[0][1]
    )


@pytest.mark.parametrize("scope", ["user", "shared"])
def test_ingestion_masks_private_before_embedding_only(world, monkeypatch, scope):
    from types import SimpleNamespace as NS

    from bson import ObjectId

    values = []
    monkeypatch.setattr(
        ingestion, "load_file", lambda *a, **kw: NS(sections=[], warnings=[])
    )
    raw = "Account 880000001001 mobile +91 9000000101 pincode 400001"
    monkeypatch.setattr(
        ingestion,
        "chunk_sections",
        lambda sections: [
            dict(
                text=raw,
                embed_text=raw,
                chunk_index=0,
                page=None,
                location="",
                kind="text",
            )
        ],
    )

    async def embed(texts):
        values.extend(texts)
        return [[0.0]]

    monkeypatch.setattr(ingestion, "embed_documents", embed)
    run(
        ingestion.ingest_document(
            str(ObjectId()),
            "file.txt",
            b"",
            metadata={"scope": scope, "session_id": "s"},
        )
    )
    assert ("880000001001" in values[0]) == (scope == "shared")
    assert "400001" in values[0]
    assert ("9000000101" in world.chunks.rows[0]["text"]) == (scope == "shared")


def test_logging_masks_values_and_bearer_path():
    from app.logging_setup import PrivacyFilter, otp_input

    record = logging.LogRecord(
        "test",
        20,
        "",
        1,
        "/sessions/%s/history account %s mobile %s OTP %s",
        ("secret-session-id-long", "880000001001", "+919000000101", "123456"),
        None,
    )
    token = otp_input.set(True)
    try:
        PrivacyFilter().filter(record)
    finally:
        otp_input.reset(token)
    text = record.getMessage()
    for secret in ("secret-session-id-long", "880000001001", "9000000101", "123456"):
        assert secret not in text
    assert "secret..." in text


def test_session_and_ip_limits_ignore_forwarded_ip(world, monkeypatch):
    monkeypatch.setenv("CHAT_RATE_PER_MINUTE", "2")
    monkeypatch.setenv("CHAT_RATE_PER_IP_PER_MINUTE", "3")
    get_settings.cache_clear()
    sid = new(world)
    assert post(world, sid, "Hello").status_code == 200
    assert post(world, sid, "Hello").status_code == 200
    denied = post(world, sid, "Hello")
    assert denied.status_code == 429 and "too quickly" in denied.json()["detail"]
    other = new(world)
    assert post(world, other, "Hello").status_code == 200
    response = world.client.post(
        "/chat",
        headers={**headers(other), "X-Forwarded-For": "8.8.8.8"},
        json={"message": "Hello", "stream": False},
    )
    assert response.status_code == 429 and response.headers["retry-after"]


def test_verification_ip_limit_separate_from_chat(world, monkeypatch):
    monkeypatch.setenv("VERIFY_RATE_PER_IP_PER_MINUTE", "1")
    get_settings.cache_clear()
    sid = new(world)
    post(world, sid, "My balance?")
    post(world, sid, "880000001001")
    assert post(world, sid, "9000000199").status_code == 200
    post(world, sid, "880000001001")
    assert post(world, sid, "9000000199").status_code == 429
    assert post(world, new(world), "Hello").status_code == 200


def test_seed_deterministic_balances_and_scenarios():
    data = build_seed()
    assert data == generate_data() == generate_data()
    assert [row["customer_id"] for row in data["customers"]] == [
        f"CUST-{i:03}" for i in range(1, 11)
    ]
    assert len({row["scenario"] for row in data["customers"]}) == 10
    for account in data["accounts"]:
        tx = [
            row
            for row in data["transactions"]
            if row["account_id"] == account["account_id"]
        ]
        assert account["balance_minor"] == sum(row["amount_minor"] for row in tx)
        assert all(
            type(row["amount_minor"]) is int and row["currency"] == "INR" for row in tx
        )
    assert (
        len([row for row in data["accounts"] if row["customer_id"] == "CUST-004"]) == 2
    )
    assert data["customers"][3]["mobile"] == "9000000104"
    assert (
        len([row for row in data["transactions"] if row["account_id"] == "ACC-009-01"])
        == 2
    )
    assert data["cards"][5]["status"] in ("active", "blocked")


def test_real_otp_seam_is_explicit(monkeypatch):
    from app.verification.otp import send_otp

    monkeypatch.setenv("OTP_MODE", "real")
    get_settings.cache_clear()
    with pytest.raises(NotImplementedError):
        send_otp()


def test_same_customer_reverification_preserves_messages_and_files(world):
    sid = new(world)
    verify(world, sid)
    old_content = [row["content"] for row in world.messages.rows]
    world.documents.rows.append({"session_id": sid, "scope": "user"})
    world.chunks.rows.append({"session_id": sid, "scope": "user"})
    world.sessions.rows[0]["verified"] = None
    response = verify(world, sid)
    assert response.json()["clear_history"] is False
    assert all(
        content in [row["content"] for row in world.messages.rows]
        for content in old_content
    )
    assert world.documents.rows and world.chunks.rows
    assert not any(row["event"] == "customer_changed" for row in world.audit_log.rows)


def test_expired_verification_excludes_private_answer_context(world):
    from app.services.memory import get_recent

    sid = new(world)
    verify(world, sid)
    run(
        chat.save_message(
            sid, "assistant", "Private attachment fact", sources=[{"scope": "user"}]
        )
    )
    world.sessions.rows[0]["verified"] = None
    prompted = run(get_recent(sid, 20))
    assert not any(
        "Private attachment fact" in row["content"] or "INR" in row["content"]
        for row in prompted
    )


def test_tool_transaction_and_card_services_use_verified_customer(world):
    from app.bank import service

    tx = run(service.get_recent_transactions("CUST-002", "current", 3))
    assert len(tx) == 3 and {row.masked_number for row in tx} == {"****2002"}
    assert all(
        row.direction == ("credit" if row.amount_minor >= 0 else "debit") for row in tx
    )
    assert run(service.get_card_status("CUST-005"))[0].status == "blocked"
    assert len(run(service.get_card_status("CUST-003"))) == 2
    assert run(service.get_balances("missing")) == []


def test_seed_specific_scenarios():
    data = build_seed()
    assert data["customers"][6]["kyc_status"] == "incomplete"
    assert any(
        row["customer_id"] == "CUST-006" and not row["cheque_book_eligible"]
        for row in data["accounts"]
    )
    assert any(
        row["customer_id"] == "CUST-008" and row["status"] == "processing"
        for row in data["service_requests"]
    )
    assert any(
        row["customer_id"] == "CUST-005" and row["status"] == "blocked"
        for row in data["cards"]
    )


def test_native_router_validation_retry_and_fallback(monkeypatch):
    from types import SimpleNamespace as NS

    from app.services import router

    calls = []

    async def create(**kwargs):
        calls.append(kwargs)
        invalid = NS(
            function=NS(name="get_balances", arguments='{"customer_id":"CUST-002"}')
        )
        return NS(choices=[NS(message=NS(tool_calls=[invalid]))])

    monkeypatch.setattr(
        router, "client", lambda: NS(chat=NS(completions=NS(create=create)))
    )
    plan = run(router.plan_turn([], "My balance?", []))
    assert len(calls) == 2 and plan.action == "search_documents"
    assert plan.tool_calls()[0].arguments == {"query": "My balance?"}
    assert all("tools" in call and call["tool_choice"] == "required" for call in calls)


@pytest.mark.parametrize("mixed", [False, True])
def test_document_and_mixed_full_path_with_native_router(world, monkeypatch, mixed):
    from types import SimpleNamespace as NS

    from app.services import router

    sid = new(world)
    if mixed:
        verify(world, sid)
    world.answers.clear()
    seen = []

    async def create(**kwargs):
        selected = [
            NS(
                function=NS(
                    name="search_documents", arguments='{"query":"replacement fee"}'
                )
            )
        ]
        if mixed:
            selected.append(NS(function=NS(name="get_balances", arguments="{}")))
        return NS(choices=[NS(message=NS(tool_calls=selected))])

    monkeypatch.setattr(
        router, "client", lambda: NS(chat=NS(completions=NS(create=create)))
    )
    monkeypatch.setattr(chat, "plan_turn", router.plan_turn)

    async def search(query, **kwargs):
        seen.append(kwargs)
        return [
            dict(
                doc_id="shared-fees",
                filename="fees.csv",
                chunk_index=0,
                text="Replacement fee INR 200.00",
                score=0.99,
                scope="shared",
            )
        ]

    monkeypatch.setattr(document_tools, "search", search)
    answer_calls = []

    async def answer(history, question, sources, **kwargs):
        answer_calls.append(kwargs)
        yield "The replacement fee is INR 200.00 [1]."
        if mixed:
            yield (
                " Your account shows "
                + kwargs["account_data"]["accounts"][0]["balance"]
                + "."
            )

    monkeypatch.setattr(chat, "stream_answer", answer)
    response = post(
        world,
        sid,
        "Replacement fee and balance" if mixed else "Replacement fee?",
        stream=True,
    )
    events = [
        json.loads(line[6:])
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    assert events[-1]["type"] == "done" and len(answer_calls) == 1
    assert events[-1]["cited"] == [1]
    meta = next(event for event in events if event["type"] == "meta")
    assert meta["sources"][0]["text"] == "Replacement fee INR 200.00"
    assert bool(meta["account_cards"]) == mixed
    if mixed:
        assert len([event for event in events if event["type"] == "token"]) == 1
    assert seen[0]["session_id"] == sid and seen[0]["include_private"] == mixed


@pytest.mark.parametrize(
    "action,expected",
    [
        ("greeting", "Hello!"),
        ("thanks", "You're welcome!"),
        ("out_of_scope", "I can only help"),
    ],
)
def test_fixed_routes_no_answer_model(world, monkeypatch, action, expected):
    async def plan(*args):
        return Plan(question="q", action=action)

    monkeypatch.setattr(chat, "plan_turn", plan)
    response = post(world, new(world), "q")
    assert response.json()["answer"].startswith(expected) and not world.answers


def test_nothing_found_and_tool_failure_reply(world, monkeypatch):
    from app.services.llm import DATA_UNAVAILABLE_MESSAGE, NOTHING_FOUND_MESSAGE

    async def plan(*args):
        return Plan(question="q")

    async def empty(*args, **kwargs):
        return []

    monkeypatch.setattr(chat, "plan_turn", plan)
    monkeypatch.setattr(document_tools, "search", empty)
    sid = new(world)
    assert post(world, sid, "q").json()["answer"] == NOTHING_FOUND_MESSAGE

    async def fail(*args):
        raise RuntimeError("secret data must never appear")

    spec = registry["search_documents"]
    monkeypatch.setitem(registry, "search_documents", replace(spec, handler=fail))
    assert post(world, sid, "q").json()["answer"] == DATA_UNAVAILABLE_MESSAGE
    assert not world.answers


def test_invalid_account_amount_buffer_is_replaced(world, monkeypatch):
    sid = new(world)
    verify(world, sid)

    async def answer(*args, **kwargs):
        yield "Your balance is INR 999,999.99."

    monkeypatch.setattr(chat, "stream_answer", answer)
    response = post(world, sid, "My balance?", stream=True)
    assert (
        "999,999.99" not in response.text
        and "Please see your verified account details" in response.text
    )
    assert (
        "From your account" not in response.text
    )  # This label belongs to the frontend card.


def test_verified_upload_masking_dedupe_isolation_and_followup(world, monkeypatch):
    sid = new(world)
    verify(world, sid)

    async def embed(texts):
        return [[0.0] * 768 for _ in texts]

    monkeypatch.setattr(ingestion, "embed_documents", embed)
    path = f"/sessions/{sid}/attachments"
    response = world.client.post(
        path,
        headers=headers(sid),
        files={
            "files": (
                "note.txt",
                b"My code is Paperclip. Account 880000001001 mobile 9000000101 pincode 400001.",
            )
        },
    )
    assert response.status_code == 202
    doc_id = response.json()[0]["doc_id"]
    assert world.documents.rows[0]["status"] == "ready"
    assert "880000001001" not in world.chunks.rows[0]["text"]
    response = world.client.post(
        path,
        headers=headers(sid),
        files={
            "files": (
                "note.txt",
                b"My code is Paperclip. Account 880000001001 mobile 9000000101 pincode 400001.",
            )
        },
    )
    assert response.json()[0]["duplicate"] is True
    stranger = new(world)
    assert post(world, stranger, "q", attachment_ids=[doc_id]).status_code == 403
    assert world.client.get(path, headers=headers(stranger)).status_code == 404

    async def plan(*args):
        return Plan(question="What is the code?")

    async def search(query, **kwargs):
        assert kwargs["session_id"] == sid
        return [dict(world.chunks.rows[0], score=0.99)]

    async def answer(*args, **kwargs):
        yield "The code is Paperclip [1]."

    monkeypatch.setattr(chat, "plan_turn", plan)
    monkeypatch.setattr(document_tools, "search", search)
    monkeypatch.setattr(chat, "stream_answer", answer)
    response = post(world, sid, "What is the code?")
    assert "Paperclip" in response.json()["answer"] and response.json()["cited"] == [1]


def test_private_ingestion_cannot_restore_deleted_customer_files(world, monkeypatch):
    from bson import ObjectId

    from app.services import document_store

    sid = new(world)
    verify(world, sid)
    doc_id = ObjectId()
    world.documents.rows.append(
        {
            "_id": doc_id,
            "scope": "user",
            "session_id": sid,
            "attachment_customer_id": "CUST-001",
        }
    )

    async def ingest(*args, **kwargs):
        await store.delete_private_files(sid)
        world.sessions.rows[0]["attachment_customer_id"] = "CUST-002"
        world.chunks.rows.append(
            {"doc_id": str(doc_id), "scope": "user", "session_id": sid}
        )

    monkeypatch.setattr(document_store, "ingest_document", ingest)
    run(
        document_store.ingest_scoped(
            str(doc_id), "note.txt", b"x", scope="user", session_id=sid
        )
    )
    assert not world.chunks.rows and not world.documents.rows


def test_retrieval_filter_enforces_session_scope_and_ready_ids(world, monkeypatch):
    from bson import ObjectId

    sid = new(world)
    stranger = new(world)
    shared = ObjectId()
    private = ObjectId()
    foreign = ObjectId()
    world.documents.rows.extend(
        [
            {"_id": shared, "scope": "shared", "status": "ready"},
            {"_id": private, "scope": "user", "session_id": sid, "status": "ready"},
            {
                "_id": foreign,
                "scope": "user",
                "session_id": stranger,
                "status": "ready",
            },
        ]
    )
    seen = []

    async def aggregate(stages):
        seen.append(stages[0]["$vectorSearch"])
        from conftest import Cursor

        return Cursor([])

    async def embed(query):
        return [0.0]

    monkeypatch.setattr(world.chunks, "aggregate", aggregate, raising=False)
    monkeypatch.setattr(retrieval, "embed_query", embed)
    run(retrieval.search("q", session_id=sid, include_private=True))
    stage = seen[0]
    assert stage["filter"]["$and"][0] == {
        "$or": [{"scope": "shared"}, {"scope": "user", "session_id": sid}]
    }
    assert stage["filter"]["$and"][1] == {
        "doc_id": {"$in": [str(shared), str(private)]}
    }


def test_ttl_orphans_and_stuck_documents_swept(world):
    now = store.now_utc()
    old = now - timedelta(minutes=31)
    world.messages.rows.append({"session_id": "ttl-removed", "expires_at": old})
    world.documents.rows.extend(
        [
            {
                "scope": "user",
                "session_id": "ttl-removed",
                "expires_at": old,
                "status": "ready",
                "created_at": old,
            },
            {
                "scope": "shared",
                "status": "processing",
                "created_at": now - timedelta(minutes=16),
                "expires_at": None,
            },
        ]
    )
    world.chunks.rows.append(
        {"scope": "user", "session_id": "ttl-removed", "expires_at": old}
    )
    run(maintenance.purge_expired(now))
    assert not world.messages.rows and not world.chunks.rows
    assert (
        len(world.documents.rows) == 1 and world.documents.rows[0]["status"] == "failed"
    )


def test_migration_preview_and_apply_preserve_anonymous_and_shared(world, monkeypatch):

    from conftest import Collection

    from scripts import migrate_r1

    cols = {
        "users": Collection([{"_id": "retired"}]),
        "sessions": world.sessions,
        "messages": world.messages,
        "documents": world.documents,
        "chunks": world.chunks,
        "audit_log": world.audit_log,
    }
    sid = new(world)
    world.sessions.rows.append({"_id": "legacy", "user_id": "old"})
    world.messages.rows.extend(
        [{"session_id": "legacy", "user_id": "old"}, {"session_id": sid}]
    )
    world.documents.rows.extend(
        [
            {"scope": "shared"},
            {"scope": "user", "owner_id": "old"},
            {"scope": "user", "session_id": sid},
        ]
    )
    world.audit_log.rows.append(
        {
            "_id": "old-event",
            "session_id": "legacy-bearer",
            "user_id": "old",
            "event": "chat",
        }
    )

    class DB:
        def __getitem__(self, key):
            return cols[key]

        def __getattr__(self, key):
            return cols[key]

        async def drop_collection(self, key):
            cols[key].rows = []

    monkeypatch.setattr(migrate_r1, "get_db", lambda: DB())
    before = len(world.sessions.rows)
    counts = run(migrate_r1.migrate())
    assert counts["users"] == 1 and len(world.sessions.rows) == before
    run(migrate_r1.migrate(apply=True))
    assert not cols["users"].rows and [row["_id"] for row in world.sessions.rows] == [
        sid
    ]
    assert len(world.documents.rows) == 2 and len(world.messages.rows) == 1
    assert all(
        "session_id" not in row and "user_id" not in row for row in world.audit_log.rows
    )


def test_whole_private_sections_are_masked_before_chunking(world, monkeypatch):
    from bson import ObjectId

    from app.services.loaders import LoadResult, Section

    raw = "A" * 790 + " 880000001001 mobile 9000000101"
    seen = []
    monkeypatch.setattr(
        ingestion,
        "load_file",
        lambda *args, **kwargs: LoadResult(sections=[Section(raw, "private")]),
    )

    def chunks(sections):
        seen.append(sections[0].text)
        return [
            dict(
                text=sections[0].text,
                embed_text=sections[0].text,
                chunk_index=0,
                page=None,
                location="",
                kind="text",
            )
        ]

    async def embed(texts):
        return [[0.0]]

    monkeypatch.setattr(ingestion, "chunk_sections", chunks)
    monkeypatch.setattr(ingestion, "embed_documents", embed)
    run(
        ingestion.ingest_document(
            str(ObjectId()),
            "private.txt",
            b"",
            metadata={"scope": "user", "session_id": "s"},
        )
    )
    assert "880000001001" not in seen[0] and "9000000101" not in seen[0]


def test_bank_narration_and_nickname_masking_preserve_money(world):
    from app.bank.service import get_balances, get_recent_transactions

    world.bank.accounts.rows[0]["nickname"] = "Account 880000001001"
    rows = [
        row for row in world.bank.transactions.rows if row["account_id"] == "ACC-001-01"
    ]
    latest = max(rows, key=lambda row: row["posted_at"])
    latest["narration"] = "UPI 9000000101 account 880000001001 reference 400001"
    balances = run(get_balances("CUST-001"))
    tx = run(get_recent_transactions("CUST-001", limit=1))
    payload = json.dumps([row.model_dump() for row in balances + tx])
    assert (
        "880000001001" not in payload
        and "9000000101" not in payload
        and "400001" in payload
    )
    assert tx[0].amount_minor == latest["amount_minor"]


def test_cleanup_cannot_remove_expired_child_of_active_session(world):
    sid = new(world)
    old = store.now_utc() - timedelta(minutes=40)
    world.documents.rows.append(
        {
            "session_id": sid,
            "scope": "user",
            "expires_at": old,
            "created_at": old,
            "status": "ready",
        }
    )
    run(maintenance.purge_expired())
    assert world.documents.rows


def test_validation_response_does_not_echo_credentials(world):
    sid = new(world)
    response = post(
        world, sid, "123456", customer_id="880000001001", mobile="9000000101"
    )
    assert response.status_code == 422
    for raw in ("123456", "880000001001", "9000000101"):
        assert raw not in response.text


def test_request_turn_id_stays_present_during_sse(world, monkeypatch):
    from app.logging_setup import turn_id

    seen = []

    async def plan(*args):
        seen.append(turn_id.get())
        return Plan(question="Hello", action="greeting")

    monkeypatch.setattr(chat, "plan_turn", plan)
    response = post(world, new(world), "Hello", stream=True)
    assert (
        response.status_code == 200
        and seen
        and seen[0] != "startup"
        and len(seen[0]) == 8
    )
