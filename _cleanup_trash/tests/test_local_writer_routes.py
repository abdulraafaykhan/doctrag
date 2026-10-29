"""Offline route checks; the HTTP boundary uses a mocked local server."""
import json
from types import SimpleNamespace

import httpx
import pytest

from app.agent import Runner
from app.chat import Chat
from app.config import get_settings


@pytest.mark.parametrize('route,message,verified,uploads,attachments', [
    ('search_my_documents', 'who is the beneficiary in the above image?', False, ['screenshot'], ()),
    ('search_bank_knowledge', 'what is the FD rate?', False, [], ()),
    ('get_balance', 'my balance', True, [], ()),
])
def test_local_code_route(monkeypatch, route, message, verified, uploads, attachments):
    cfg = get_settings()
    monkeypatch.setattr(cfg.models, 'agent', 'local/default')
    monkeypatch.setattr(cfg.secrets, 'local_llm_url', 'http://local.invalid/api/inference')
    calls, requests = [], []
    session = {'session_id': 'offline-writer', 'auth': {'state': 'VERIFIED' if verified else 'ANONYMOUS'}, 'history': [], 'uploads': uploads}

    class Registry:
        def __init__(self, *args, **kwargs):
            self.results = []
            self.tools = {route: SimpleNamespace(invoke=self.invoke)}

        def references_upload(self):
            return bool(attachments or uploads)

        def visible(self):
            return [route]

        def invoke(self, arguments):
            calls.append((route, arguments))
            result = {'ok': True, 'code': 'OK', 'display': 'Balance: INR 100.00' if route == 'get_balance' else '', 'data': {'items': [] if route == 'get_balance' else [{'n': 1, 'score': 0.9, 'text': '[SOURCE 1 | your file | Screenshot | page 1 | effective ]\nBeneficiary: Alice. FD rate: 7.25%.'}], 'no_good_match': False}}
            self.results.append((route, result))
            return result

    def respond(request):
        payload = json.loads(request.content)
        requests.append(payload)
        assert payload['prompt'].endswith(cfg.prompts['local_writer'].strip())
        assert '[SOURCE 1 |' in payload['prompt']
        assert '"tool_calls"' not in payload['prompt']
        text = 'Beneficiary: Alice [1].' if route == 'search_my_documents' else 'The FD rate is 7.25% [1].'
        return httpx.Response(200, json={'response': text + '\nUser: invented dialogue', 'prompt_tokens': 80, 'completion_tokens': 12})

    client = httpx.Client
    monkeypatch.setattr('app.local_llm.httpx.Client', lambda **kwargs: client(**{**kwargs, 'transport': httpx.MockTransport(respond)}))
    monkeypatch.setattr('app.chat.Registry', Registry)
    monkeypatch.setattr('app.agent.system_prompt', lambda *args: 'Answer using supplied bank sources only.')
    chat = Chat(store=SimpleNamespace(), runner=Runner())
    result = chat._turn(session, message, attachments)
    assert calls == [(route, {} if route == 'get_balance' else {'query': message})]
    assert len(requests) == (0 if route == 'get_balance' else 1)
    assert 'invented dialogue' not in result['reply']
    assert result['reply'] == ('Balance: INR 100.00' if route == 'get_balance' else 'Beneficiary: Alice [1].' if route == 'search_my_documents' else 'The FD rate is 7.25% [1].')
