"""Provider-free browser fixture: HTTP, sessions, files, flows and logs."""
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.chat import Chat
from app.config import ROOT, get_settings
from app.llm import FakeLLM
from app.main import create_app
from tests.memory_store import MemoryStore
from fastapi.routing import APIRoute


class BrowserLLM(FakeLLM):
    def chat(self, messages, **kwargs):
        super().chat(messages)
        if messages[-1]['role'] == 'tool':
            if json.loads(messages[-1]['content']).get('data', {}).get('items'):
                return 'The fixture rate is 7.10% [1].\n\n| Tenure | General | Senior | Effective | Product | Segment | Notes |\n|---|---|---|---|---|---|---|\n| 12 months | 7.10% | 7.60% | 2026-10-01 | Fixed deposit | General | Fixture only |'
            return ''
        text = messages[-1]['content'].rsplit('Customer: ', 1)[-1].lower()
        if 'fixture source' in text:
            return {'tool_calls': [{'name': 'search_bank_knowledge', 'arguments': {'query': 'fixture rate'}}]}
        if 'balance' in text:
            return {'tool_calls': [{'name': 'get_balance', 'arguments': {}}]}
        if 'chequebook' in text:
            return {'tool_calls': [{'name': 'chequebook_set_slots', 'arguments': {}}]}
        return 'Hello. How can I help with Harbourline Bank?'


store = MemoryStore()
for name in ('users', 'accounts', 'cards', 'transactions'):
    store.data[name] = json.loads((ROOT / 'seed' / f'{name}.json').read_text())
store.data['documents'].append({'doc_id': 'browser-fixture', 'title': 'Browser fixture rate table'})
store.data['chunks'].append({'chunk_id': 'browser-fixture', 'section_id': 'browser-fixture', 'doc_id': 'browser-fixture', 'namespace': 'bank', 'owner_key': 'bank', 'lifecycle': 'active', 'doc_type': 'rate_card', 'product': 'fixed_deposit', 'text': 'Fixture rate: 12 months, general 7.10%, senior 7.60%, effective 2026-10-01. Fixture only.', 'page': 1, 'version': '1', 'effective_date': '2026-10-01', 'confidence': 1.0, 'embedding': [1.0] + [0.0] * (get_settings().models.embed_dims - 1)})
app = create_app(chat=Chat(store, llm=BrowserLLM(), embeddings=FakeLLM()), checks=False)


def ready(attachment_id: str):
    store.patch('documents', {'attachment.id': attachment_id}, {'$set': {'attachment.status': 'Ready'}})
    return {'ok': True}


# Test-only route precedes the static mount; never exported by app.main.
app.router.routes.insert(0, APIRoute('/fixture/ready/{attachment_id}', ready, methods=['POST']))

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='127.0.0.1', port=8001, access_log=False)
