"""Provider-shaped mocks verify GPT-OSS request options without credentials or HTTP calls."""

import asyncio
import inspect
import json
from types import SimpleNamespace as NS

import pytest
from groq.resources.chat.completions import AsyncCompletions

from app.config import Settings, get_settings
from app.services import llm, router
from app.services.groq_models import reasoning_options


def run(coro):
    return asyncio.run(coro)


def response(name, args):
    return NS(
        choices=[
            NS(
                message=NS(
                    tool_calls=[NS(function=NS(name=name, arguments=json.dumps(args)))]
                )
            )
        ]
    )


def test_defaults_and_installed_sdk_support():
    assert Settings.model_fields["groq_model"].default == "openai/gpt-oss-120b"
    assert Settings.model_fields["groq_rewrite_model"].default == "openai/gpt-oss-20b"
    parameters = inspect.signature(AsyncCompletions.create).parameters
    assert {
        "max_completion_tokens",
        "include_reasoning",
        "reasoning_effort",
        "parallel_tool_calls",
    } <= parameters.keys()


def test_router_gpt_oss_batch_preserves_two_selections(monkeypatch):
    seen = []

    async def create(**kwargs):
        seen.append(kwargs)
        return response(
            "select_tools",
            {
                "calls": [
                    {"name": "search_documents", "arguments": {"query": "card fee"}},
                    {"name": "get_balances", "arguments": {}},
                ]
            },
        )

    monkeypatch.setattr(
        router, "client", lambda: NS(chat=NS(completions=NS(create=create)))
    )
    plan = run(router.plan_turn([], "Fee and my balance?", []))
    assert [call.name for call in plan.tool_calls()] == [
        "search_documents",
        "get_balances",
    ]
    assert len(seen) == 1
    request = seen[0]
    assert (
        request["model"] == "openai/gpt-oss-20b"
        and request["parallel_tool_calls"] is False
    )
    assert request["max_completion_tokens"] == 250 and "max_tokens" not in request
    assert (
        request["include_reasoning"] is False and request["reasoning_effort"] == "low"
    )
    assert "reasoning_format" not in request
    assert [tool["function"]["name"] for tool in request["tools"]] == [
        "select_tools",
        "reply_without_data",
    ]
    schema = json.dumps(request["tools"])
    for identity in (
        "customer_id",
        "user_id",
        "session_id",
        "account_id",
        "account_number",
        "mobile",
    ):
        assert identity not in schema
    assert "browser_search" not in schema and "code_interpreter" not in schema


def test_nested_arguments_reject_identity_and_keep_valid_selection():
    calls = router.selected_calls(
        "select_tools",
        {
            "calls": [
                {"name": "get_balances", "arguments": {"customer_id": "CUST-002"}},
                {"name": "search_documents", "arguments": {"query": "fees"}},
            ]
        },
    )
    assert [call.name for call in calls] == ["search_documents"]


def test_invalid_batch_retries_once_then_searches(monkeypatch):
    seen = []

    async def create(**kwargs):
        seen.append(kwargs)
        return response(
            "select_tools", {"calls": [{"name": "get_balances", "arguments": {}}] * 3}
        )

    monkeypatch.setattr(
        router, "client", lambda: NS(chat=NS(completions=NS(create=create)))
    )
    plan = run(router.plan_turn([], "My balance?", []))
    assert len(seen) == 2 and plan.action == "search_documents"


def test_gpt_oss_fixed_reply_still_works(monkeypatch):
    async def create(**kwargs):
        return response("reply_without_data", {"kind": "greeting"})

    monkeypatch.setattr(
        router, "client", lambda: NS(chat=NS(completions=NS(create=create)))
    )
    assert run(router.plan_turn([], "Hello", [])).action == "greeting"


def test_other_model_override_keeps_direct_tools(monkeypatch):
    monkeypatch.setenv("GROQ_REWRITE_MODEL", "custom-router")
    get_settings.cache_clear()
    seen = []

    async def create(**kwargs):
        seen.append(kwargs)
        return response("get_balances", {})

    monkeypatch.setattr(
        router, "client", lambda: NS(chat=NS(completions=NS(create=create)))
    )
    assert run(router.plan_turn([], "My balance?", [])).action == "get_balances"
    assert seen[0]["parallel_tool_calls"] is True
    assert (
        "include_reasoning" not in seen[0] and reasoning_options("custom-router") == {}
    )


@pytest.mark.parametrize("model", ["openai/gpt-oss-120b", "openai/gpt-oss-20b"])
def test_answer_stream_excludes_reasoning_and_uses_budget(monkeypatch, model):
    monkeypatch.setenv("GROQ_MODEL", model)
    get_settings.cache_clear()
    seen = []

    async def stream(create, **kwargs):
        seen.append(kwargs)
        yield NS(
            choices=[
                NS(
                    delta=NS(content=None, reasoning="private analysis"),
                    finish_reason=None,
                )
            ]
        )
        yield NS(
            choices=[
                NS(delta=NS(content="The fee is INR 200.00 [1]."), finish_reason=None)
            ]
        )
        yield NS(choices=[], usage={})
        yield NS(choices=[NS(delta=NS(content=None), finish_reason="stop")])

    monkeypatch.setattr(llm, "stream_groq", stream)
    monkeypatch.setattr(
        llm, "client", lambda: NS(chat=NS(completions=NS(create=object())))
    )

    async def collect():
        return [
            token
            async for token in llm.stream_answer(
                [],
                "Fee?",
                [
                    {
                        "filename": "fees.csv",
                        "text": "Fee INR 200.00",
                        "doc_id": "d",
                        "chunk_index": 0,
                        "score": 0.99,
                    }
                ],
            )
        ]

    assert run(collect()) == ["The fee is INR 200.00 [1]."]
    assert seen[0]["model"] == model and seen[0]["stream"] is True
    assert (
        seen[0]["include_reasoning"] is False and seen[0]["reasoning_effort"] == "low"
    )
    assert (
        seen[0]["max_completion_tokens"] == get_settings().answer_max_tokens
        and "max_tokens" not in seen[0]
    )


@pytest.mark.parametrize("finish", ["length", "stop"])
def test_empty_or_truncated_answer_fails_safely(monkeypatch, finish):
    async def stream(*args, **kwargs):
        yield NS(choices=[NS(delta=NS(content=None), finish_reason=finish)])

    monkeypatch.setattr(llm, "stream_groq", stream)
    monkeypatch.setattr(
        llm, "client", lambda: NS(chat=NS(completions=NS(create=object())))
    )

    async def collect():
        return [
            token
            async for token in llm.stream_answer(
                [], "Balance?", [], account_data={"accounts": []}
            )
        ]

    with pytest.raises(RuntimeError):
        run(collect())


def test_real_sdk_serializes_batch_request_with_mock_transport(monkeypatch):
    import httpx
    from groq import AsyncGroq

    seen = []

    def handle(request):
        body = json.loads(request.content)
        seen.append(body)
        return httpx.Response(
            200,
            json={
                "id": "fixture",
                "created": 0,
                "object": "chat.completion",
                "model": body["model"],
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "tool_calls",
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "selection",
                                    "type": "function",
                                    "function": {
                                        "name": "select_tools",
                                        "arguments": json.dumps(
                                            {
                                                "calls": [
                                                    {
                                                        "name": "get_balances",
                                                        "arguments": {},
                                                    }
                                                ]
                                            }
                                        ),
                                    },
                                }
                            ],
                        },
                    }
                ],
            },
        )

    async def go():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handle), trust_env=False
        ) as http:
            async with AsyncGroq(
                api_key="test-only", http_client=http, max_retries=0
            ) as sdk:
                monkeypatch.setattr(router, "client", lambda: sdk)
                return await router.plan_turn([], "My balance?", [])

    assert run(go()).action == "get_balances"
    assert len(seen) == 1 and seen[0]["parallel_tool_calls"] is False
    assert (
        seen[0]["include_reasoning"] is False
        and seen[0]["max_completion_tokens"] == 250
    )


def test_gpt_oss_mixed_turn_runs_tools_then_one_answer(world, monkeypatch):
    from app.bank.service import format_money
    from app.routers import chat
    from app.tools import document_tools

    client = world.client
    sid = client.post("/sessions").json()["session_id"]
    headers = {"X-Session-Id": sid}
    for value in ("My balance?", "880000001001", "9000000101", "123456"):
        assert (
            client.post(
                "/chat", headers=headers, json={"message": value, "stream": False}
            ).status_code
            == 200
        )
    seen = []
    balance = format_money(world.bank.accounts.rows[0]["balance_minor"], "INR")

    async def create(**kwargs):
        seen.append(kwargs)
        if not kwargs.get("stream"):
            return response(
                "select_tools",
                {
                    "calls": [
                        {
                            "name": "search_documents",
                            "arguments": {"query": "replacement fee"},
                        },
                        {"name": "get_balances", "arguments": {}},
                    ]
                },
            )

        async def chunks():
            yield NS(
                choices=[
                    NS(
                        delta=NS(content=None, reasoning="must stay private"),
                        finish_reason=None,
                    )
                ]
            )
            yield NS(
                choices=[
                    NS(
                        delta=NS(
                            content="The fee is INR 200.00 [1]. Your account shows "
                            + balance
                            + "."
                        ),
                        finish_reason=None,
                    )
                ]
            )
            yield NS(choices=[NS(delta=NS(content=None), finish_reason="stop")])

        return chunks()

    sdk = NS(chat=NS(completions=NS(create=create)))
    monkeypatch.setattr(router, "client", lambda: sdk)
    monkeypatch.setattr(llm, "client", lambda: sdk)
    monkeypatch.setattr(chat, "plan_turn", router.plan_turn)
    monkeypatch.setattr(chat, "stream_answer", llm.stream_answer)

    async def search(*args, **kwargs):
        return [
            {
                "filename": "fees.csv",
                "doc_id": "shared",
                "chunk_index": 0,
                "score": 0.99,
                "text": "Replacement fee INR 200.00",
            }
        ]

    monkeypatch.setattr(document_tools, "search", search)
    result = client.post(
        "/chat",
        headers=headers,
        json={"message": "Replacement fee and my balance?", "stream": False},
    ).json()
    assert (
        result["account_cards"]
        and result["cited"] == [1]
        and balance in result["answer"]
    )
    assert "must stay private" not in json.dumps(result)
    assert [call["model"] for call in seen] == [
        "openai/gpt-oss-20b",
        "openai/gpt-oss-120b",
    ]
    assert (
        "INR 200.00" in seen[1]["messages"][-1]["content"]
        and balance in seen[1]["messages"][-1]["content"]
    )
