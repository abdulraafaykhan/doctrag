"""Misrouting personal requests must never bypass server-side verification."""

import json
from types import SimpleNamespace as NS

import pytest

from app.routers import chat
from app.services import llm, router
from app.services.groq_runtime import AssistantBusy
from app.services.prompting import PromptBudgetExceeded
from app.tools import document_tools
from app.verification import messages
from app.verification.intent import is_personal_request

PERSONAL = [
    "what is myaccount balance",
    "what is myacount balnce",
    "What is my balance?",
    "show my account",
    "show my recent transactions",
    "is my debit card active?",
    "where is my cheque book?",
    "mycard status",
    "my crad status",
    "my chequebook status",
]


@pytest.mark.parametrize("message", PERSONAL)
def test_personal_intent_recognizes_joined_words_and_one_typo(message):
    assert is_personal_request(message)


@pytest.mark.parametrize(
    "message",
    [
        "What is the minimum balance?",
        "What is the debit card fee?",
        "How do cheque books work?",
        "Hello",
        "Write a poem",
        "my car is blue",
        "my cars are blue",
        "my carts are full",
        "my favourite movie",
        "Tell me a myth about balance",
        "my cardigan is green",
    ],
)
def test_general_questions_do_not_trigger_safety_net(message):
    assert not is_personal_request(message)


@pytest.mark.parametrize(
    "message", ["what is myaccount balance", "what is myacount balnce"]
)
@pytest.mark.parametrize("action", ["search_documents", "out_of_scope", "greeting"])
@pytest.mark.parametrize("stream", [False, True])
def test_misrouted_request_starts_verification_without_tools_or_answer(
    world, monkeypatch, message, action, stream
):
    async def plan(*args):
        return router.Plan(question=message, action=action)

    async def forbidden(*args, **kwargs):
        pytest.fail("No tool may run before this verification prompt")

    async def answer(*args, **kwargs):
        pytest.fail("No generated refusal may replace verification")
        yield "unreachable"

    monkeypatch.setattr(chat, "plan_turn", plan)
    monkeypatch.setattr(chat, "execute", forbidden)
    monkeypatch.setattr(chat, "stream_answer", answer)
    sid = world.client.post("/sessions").json()["session_id"]
    result = world.client.post(
        "/chat",
        headers={"X-Session-Id": sid},
        json={"message": message, "stream": stream},
    )
    assert result.status_code == 200
    if stream:
        events = [
            json.loads(line[6:])
            for line in result.text.splitlines()
            if line.startswith("data: ")
        ]
        assert [event["type"] for event in events] == [
            "status",
            "meta",
            "token",
            "done",
        ]
        assert (
            next(event["t"] for event in events if event["type"] == "token")
            == messages.ACCOUNT
        )
    else:
        assert result.json()["answer"] == messages.ACCOUNT
    assert world.sessions.rows[0]["flow"]["state"] == "awaiting_account"
    assert world.sessions.rows[0]["flow"]["pending_question"] == message
    assert not world.sessions.rows[0]["verified"]
    assert any(row["event"] == "verify_started" for row in world.audit_log.rows)
    assert not any(
        row["event"] == "tool_call" and row["outcome"] == "ok"
        for row in world.audit_log.rows
    )


@pytest.mark.parametrize("selection", ["documents", "empty", "invalid"])
def test_unverified_gpt_oss_router_sees_all_account_tools_even_when_it_searches(
    world, monkeypatch, selection
):
    seen = []

    async def create(**kwargs):
        seen.append(kwargs)
        if selection == "empty":
            return NS(choices=[NS(message=NS(tool_calls=[]))])
        if selection == "invalid":
            return NS(
                choices=[
                    NS(
                        message=NS(
                            tool_calls=[
                                NS(
                                    function=NS(
                                        name="get_balances",
                                        arguments='{"customer_id":"CUST-002"}',
                                    )
                                )
                            ]
                        )
                    )
                ]
            )
        return NS(
            choices=[
                NS(
                    message=NS(
                        tool_calls=[
                            NS(
                                function=NS(
                                    name="select_tools",
                                    arguments=json.dumps(
                                        {
                                            "calls": [
                                                {
                                                    "name": "search_documents",
                                                    "arguments": {
                                                        "query": "account balance"
                                                    },
                                                }
                                            ]
                                        }
                                    ),
                                )
                            )
                        ]
                    )
                )
            ]
        )

    monkeypatch.setattr(
        router, "client", lambda: NS(chat=NS(completions=NS(create=create)))
    )
    monkeypatch.setattr(chat, "plan_turn", router.plan_turn)

    async def forbidden(*args, **kwargs):
        pytest.fail("Misrouted search must be intercepted")

    monkeypatch.setattr(chat, "execute", forbidden)
    sid = world.client.post("/sessions").json()["session_id"]
    response = world.client.post(
        "/chat",
        headers={"X-Session-Id": sid},
        json={"message": "what is myaccount balance", "stream": False},
    )
    assert response.json()["answer"] == messages.ACCOUNT and len(seen) == (
        1 if selection == "documents" else 2
    )
    choices = seen[0]["tools"][0]["function"]["parameters"]["properties"]["calls"][
        "items"
    ]["anyOf"]
    offered = {choice["properties"]["name"]["enum"][0] for choice in choices}
    assert {
        "get_balances",
        "get_recent_transactions",
        "get_card_status",
        "search_documents",
    } <= offered
    schema = json.dumps(choices)
    for field in (
        "customer_id",
        "session_id",
        "user_id",
        "account_id",
        "account_number",
        "mobile",
    ):
        assert field not in schema


@pytest.mark.parametrize(
    "error", [AssistantBusy(20), PromptBudgetExceeded("test limit")]
)
def test_provider_or_prompt_failure_cannot_block_clear_personal_verification(
    world, monkeypatch, error
):
    async def plan(*args):
        raise error

    monkeypatch.setattr(chat, "plan_turn", plan)
    sid = world.client.post("/sessions").json()["session_id"]
    response = world.client.post(
        "/chat",
        headers={"X-Session-Id": sid},
        json={"message": "what is myaccount balance", "stream": False},
    )
    assert response.json()["answer"] == messages.ACCOUNT


def test_general_document_question_stays_unverified(world, monkeypatch):
    async def plan(*args):
        return router.Plan(question="minimum balance")

    async def search(*args, **kwargs):
        return []

    monkeypatch.setattr(chat, "plan_turn", plan)
    monkeypatch.setattr(document_tools, "search", search)
    sid = world.client.post("/sessions").json()["session_id"]
    response = world.client.post(
        "/chat",
        headers={"X-Session-Id": sid},
        json={"message": "What is the minimum balance?", "stream": False},
    )
    assert response.json()["answer"] == llm.NOTHING_FOUND_MESSAGE
    assert (
        world.sessions.rows[0]["flow"]["state"] == "idle"
        and world.sessions.rows[0]["verified"] is None
    )


def test_active_flow_and_pending_question_survive_misroute(world, monkeypatch):
    sid = world.client.post("/sessions").json()["session_id"]
    headers = {"X-Session-Id": sid}

    def post(message):
        return world.client.post(
            "/chat", headers=headers, json={"message": message, "stream": False}
        )

    post("My balance?")
    post("880000001001")
    original = dict(world.sessions.rows[0]["flow"])

    async def plan(*args):
        return router.Plan(question="my card", action="out_of_scope")

    monkeypatch.setattr(chat, "plan_turn", plan)
    assert post("my card status").json()["answer"] == messages.CONTINUE
    assert world.sessions.rows[0]["flow"] == original


def test_safety_net_does_not_prompt_verified_session_again(world, monkeypatch):
    sid = world.client.post("/sessions").json()["session_id"]
    headers = {"X-Session-Id": sid}
    for message in ("My balance?", "880000001001", "9000000101", "123456"):
        world.client.post(
            "/chat", headers=headers, json={"message": message, "stream": False}
        )
    result = world.client.post(
        "/chat",
        headers=headers,
        json={"message": "what is myaccount balance", "stream": False},
    ).json()
    assert result["account_cards"] and world.sessions.rows[0]["flow"]["state"] == "idle"


def test_prompts_and_fixed_replies_establish_harbourline_brand():
    assert (
        "Harbourline Bank" in llm.BANK_SYSTEM_PROMPT
        and "older branding" in llm.BANK_SYSTEM_PROMPT
    )
    assert (
        "Harbourline Bank" in llm.GREETING_MESSAGE
        and "Harbourline Bank" in router.PLANNER_PROMPT
    )
    assert "Lantern" not in llm.BANK_SYSTEM_PROMPT + router.PLANNER_PROMPT + "".join(
        llm.FIXED_REPLIES.values()
    )
    assert len(llm.BANK_SYSTEM_PROMPT) < 700


@pytest.mark.parametrize(
    "message", ["what is myaccount balance", "what is myacount balnce"]
)
def test_safety_net_pending_question_replays_after_otp(world, monkeypatch, message):
    seen = []

    async def plan(history, question, names):
        seen.append(question)
        return router.Plan(
            question=question,
            action="get_balances"
            if world.sessions.rows[0].get("verified")
            else "search_documents",
        )

    async def forbidden(*args, **kwargs):
        pytest.fail("Personal verification must precede any document search")

    monkeypatch.setattr(chat, "plan_turn", plan)
    monkeypatch.setattr(document_tools, "search", forbidden)
    sid = world.client.post("/sessions").json()["session_id"]
    headers = {"X-Session-Id": sid}
    for value in (message, "880000001001", "9000000101", "123456"):
        result = world.client.post(
            "/chat", headers=headers, json={"message": value, "stream": False}
        )
        assert result.status_code == 200
    assert result.json()["account_cards"][0]["type"] == "balances"
    assert seen == [message, message] and len(world.answers) == 1
    assert world.sessions.rows[0]["flow"] == {"state": "idle"}


@pytest.mark.parametrize("message", PERSONAL)
def test_all_clear_personal_subjects_start_verification_on_no_data_reply(
    world, monkeypatch, message
):
    async def plan(*args):
        return router.Plan(question=message, action="out_of_scope")

    monkeypatch.setattr(chat, "plan_turn", plan)
    sid = world.client.post("/sessions").json()["session_id"]
    result = world.client.post(
        "/chat",
        headers={"X-Session-Id": sid},
        json={"message": message, "stream": False},
    )
    assert result.json()["answer"] == messages.ACCOUNT
