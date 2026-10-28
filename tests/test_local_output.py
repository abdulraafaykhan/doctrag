"""Offline mocked-server regressions; intentionally not run in this task."""
import json
from types import SimpleNamespace
import httpx
from app.config import get_settings
from app.local_llm import LocalClient
from app.tools.registry import Registry


def client_for(monkeypatch, responses):
    monkeypatch.setattr(get_settings().secrets, 'local_llm_url', 'http://offline.test/inference')
    registry = Registry({'session_id': 'output-check', 'auth': {'state': 'VERIFIED'}, 'active_flow': None}, SimpleNamespace())
    outputs, calls = iter(responses), []
    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={'response': next(outputs), 'prompt_tokens': 12, 'completion_tokens': 8})
    return LocalClient(registry, transport=httpx.MockTransport(handler)), calls


def test_json_followed_by_invented_dialogue(monkeypatch):
    client, calls = client_for(monkeypatch, ['```json\n{"reply":"Hello!"}\n```\nUser: hm\nAssistant: invented'])
    assert client.chat([{'role': 'user', 'content': 'hello there'}]) == 'Hello!'
    assert len(calls) == 1 and calls[0]['max_tokens'] == 200
    assert client.parse('{"reply":"First answer"} trailing junk {"reply":"Invented"}') == 'First answer'


def test_empty_tool_rejected_then_retried(monkeypatch):
    invalid = '{"tool_calls":[{}],"reply":"Do not accept this reply"}'
    client, calls = client_for(monkeypatch, [invalid, '{"reply":"Valid retry"}'])
    logs = []
    monkeypatch.setattr('app.local_llm.log.step', lambda area, action, **fields: logs.append(fields))
    assert client.chat([{'role': 'user', 'content': 'Help with a cheque book'}]) == 'Valid retry'
    assert len(calls) == 2
    assert any(row.get('reason') == 'empty tool call' and row.get('raw_output') == invalid for row in logs)


def test_correct_chequebook_call_takes_precedence(monkeypatch):
    call = {'name': 'chequebook_set_slots', 'arguments': {'pages': 40, 'delivery': 'emergency'}}
    client, calls = client_for(monkeypatch, [json.dumps({'tool_calls': [call], 'reply': 'Ignore this reply'})])
    assert client.chat([{'role': 'user', 'content': 'I want a 40 page chequebook, urgent'}]) == {'tool_calls': [call]}
    assert len(calls) == 1
