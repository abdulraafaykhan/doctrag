"""Local adapter and fallback checks; every HTTP request is a MockTransport call."""
import json
from types import SimpleNamespace
import httpx
import pytest
from app.agent import Runner
from app.config import get_settings
from app.local_llm import LocalClient
from app.session import Sessions
from app.tools.registry import Registry
from app.usage import token_scope
from tests.memory_store import MemoryStore


def setup_local(monkeypatch, handler):
    import app.local_llm as module
    monkeypatch.setattr(get_settings().secrets, 'local_llm_url', 'http://offline.test/inference')
    original = module.LocalClient
    monkeypatch.setattr(module, 'LocalClient', lambda *args, **kwargs: original(*args, **kwargs, transport=httpx.MockTransport(handler)))
    store = MemoryStore()
    session = Sessions(store).load()
    registry = Registry(session, store)
    return session, registry


@pytest.mark.parametrize('reported', [True, False])
def test_valid_tool_then_reply(monkeypatch, reported):
    bodies = []
    outputs = iter([{'tool_calls': [{'name': 'search_bank_knowledge', 'arguments': {'query': 'policy', 'user_id': 'ignored', 'account_id': 'ignored'}}]}, {'reply': 'Policy information is available.'}, 'Plain repaired answer.'])
    def handler(request):
        body = json.loads(request.content); bodies.append(body)
        value = next(outputs)
        result = {'response': json.dumps(value) if isinstance(value, dict) else value}
        if reported:
            result.update(prompt_tokens=10, completion_tokens=5)
        return httpx.Response(200, json=result)
    session, registry = setup_local(monkeypatch, handler)
    registry.tools['search_bank_knowledge']._callback = lambda **kwargs: {'ok': True, 'code': 'OK', 'data': {'items': []}, 'display': ''}
    runner = Runner(model='local/default')
    with token_scope(session['session_id'], registry.store):
        answer = runner.run(session, 'Explain bank policy', registry)
    assert answer['reply'] == 'Policy information is available.'
    assert [name for name, _ in registry.results] == ['search_bank_knowledge']
    assert len(bodies) == 2
    assert all(set(body) == {'server', 'prompt', 'max_tokens'} and body['server'] == '154' for body in bodies)
    assert all(body['prompt'].endswith('Assistant:') for body in bodies)
    assert 'Tool:' in bodies[1]['prompt'] and '"code": "OK"' in bodies[1]['prompt']
    assert all(value['estimated'] == (not reported) for value in runner.usage)
    assert sum(value['prompt_tokens'] for value in runner.usage) == (20 if reported else sum((len(body['prompt']) + 3) // 4 for body in bodies))
    with pytest.raises(ValueError):
        LocalClient(registry).parse(json.dumps({'tool_calls': [{'name': 'search_bank_knowledge', 'arguments': {'query': []}}]}))
    assert runner._native('local/default', [{'role': 'system', 'content': 'Repair the answer.'}], registry, repair=True) == 'Plain repaired answer.'
    assert 'ONLY tools currently available' not in bodies[-1]['prompt']


@pytest.mark.parametrize('failure', ['bad_tool', 'malformed_json', 'timeout'])
def test_local_failure_falls_back(monkeypatch, failure):
    attempts, models = [], []
    def handler(request):
        attempts.append(request)
        if failure == 'timeout':
            raise httpx.ReadTimeout('offline timeout')
        text = 'not JSON' if failure == 'malformed_json' else json.dumps({'tool_calls': [{'name': 'submit_everything', 'arguments': {}}]})
        return httpx.Response(200, json={'response': text})
    session, registry = setup_local(monkeypatch, handler)
    original = Runner._native
    def native(self, model, messages, registry, **kwargs):
        models.append(model)
        if model.startswith('local/'):
            return original(self, model, messages, registry, **kwargs)
        # The fallback branch is stubbed: no Groq/Gemini SDK or network call.
        return 'Fallback answer.'
    monkeypatch.setattr(Runner, '_native', native)
    result = Runner().run(session, 'Explain bank policy', registry)
    assert result['reply'] == 'Fallback answer.'
    assert models == ['local/default', get_settings().models.agent_fallbacks[0]]
    assert len(attempts) == (1 if failure == 'timeout' else 2)
    assert not registry.results
