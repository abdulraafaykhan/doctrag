"""Black-box runner tests use MockTransport only, with no application imports in the runner."""

import ast
import json
from pathlib import Path

import httpx

from scripts import run_eval as ev


def test_dataset_coverage_and_no_app_imports():
    cases = ev.load_cases(Path("eval/cases.jsonl"))
    assert 28 <= len(cases) <= 35 and len({c.id for c in cases}) == len(cases)
    assert {c.category for c in cases} == {
        "document_facts",
        "account_questions",
        "out_of_scope",
        "prompt_injection",
        "cross_user",
    }
    assert {c.user for c in cases} == {"Alice", "Bob"}
    assert any(
        c.attachment and "another" not in c.message and "CUST-002" in c.attachment.text
        for c in cases
    )
    assert {c.probe for c in cases if c.probe} == {
        "foreign_history",
        "foreign_chat",
        "foreign_delete",
    }
    tree = ast.parse(Path("scripts/run_eval.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        names = (
            [node.module or ""]
            if isinstance(node, ast.ImportFrom)
            else [n.name for n in node.names]
            if isinstance(node, ast.Import)
            else []
        )
        assert not any(
            n == "app"
            or n.startswith("app.")
            or n.startswith("scripts.")
            or n.startswith("crewai")
            for n in names
        )


def case(**changes):
    data = dict(
        id="test",
        category="account_questions",
        user="Alice",
        message="My balance?",
        checks={
            "expected_phrases": ["6,289.80"],
            "forbidden_phrases": ["6,889.80"],
            "citation": False,
            "account_card": True,
            "card_types": ["balances"],
        },
    )
    data.update(changes)
    return ev.EvalCase.model_validate(data)


def test_response_checks_inspect_cards_and_citations():
    payload = {
        "answer": "Your account shows INR 6,289.80.",
        "account_cards": [{"type": "balances"}],
        "cited": [],
        "sources": [],
    }
    assert ev.check_response(case(), 200, payload) == []
    leaked = {
        **payload,
        "account_cards": [{"type": "balances", "balance": "INR 6,889.80"}],
    }
    assert any("Forbidden" in f for f in ev.check_response(case(), 200, leaked))
    citation_case = case(
        category="document_facts", checks={"citation": True, "account_card": False}
    )
    good = {
        "answer": "Fee [1]",
        "cited": [1],
        "sources": [{"n": 1}],
        "account_cards": [],
    }
    assert ev.check_response(citation_case, 200, good) == []
    assert ev.check_response(citation_case, 200, {**good, "sources": []})
    assert ev.check_response(
        citation_case, 200, {"answer": "Fee", "cited": None, "sources": None}
    )


def test_fixed_reply_and_http_error_checks():
    fixed = case(
        checks={"fixed_reply": "Hello", "account_card": False, "citation": False}
    )
    assert not ev.check_response(fixed, 200, {"answer": "Hello"})
    assert ev.check_response(fixed, 200, {"answer": "Hello there"})
    assert ev.check_response(fixed, 429, {"detail": "Too fast"})
    assert ev.check_response(case(), 200, {"error": "busy"})


def test_anonymous_runner_verifies_over_http_and_redacts_bearer():
    seen = []

    def handle(request):
        seen.append(request)
        if request.url.path == "/sessions":
            return httpx.Response(201, json={"session_id": "bearer-test"})
        if request.url.path == "/chat":
            assert request.headers["X-Session-Id"] == "bearer-test"
            assert "session_id" not in json.loads(request.content)
            return httpx.Response(
                200, json={"answer": "ok", "account_cards": [{"type": "balances"}]}
            )
        return httpx.Response(200, json={})

    with httpx.Client(
        base_url="http://test", transport=httpx.MockTransport(handle)
    ) as client:
        runner = ev.HTTPOnlyRunner(client, interval_seconds=0)
        runner.create_session("Alice")
        runner.verify("Alice")
        assert (
            runner.redact("bearer-test 880000001001 9000000101")
            == "[REDACTED] [REDACTED] [REDACTED]"
        )
    assert [request.url.path for request in seen] == ["/sessions"] + ["/chat"] * 4
