"""Two local-only tool checks; Groq/Gemini generation and embedding are excluded."""
import json
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.agent import Runner
from app.config import ROOT, get_settings
from app.ingest.store import Store
from app.db import close_db
from app.log import log
from app.session import Sessions
from app.tools.registry import Registry
from app.usage import token_scope
from app.local_llm import LocalClient
from tests.memory_store import MemoryStore


def main():
    cfg = get_settings()
    if not cfg.secrets.local_llm_url:
        cfg.secrets.local_llm_url = os.environ['LOCAL_LLM_URL']
    store = MemoryStore()
    for name in ('users', 'accounts'):
        store.data[name] = json.loads((ROOT / 'seed' / f'{name}.json').read_text())
    # Exact lookups use existing Atlas rows; searches return NO_DOCUMENTS rather than call an embedding API.
    atlas = Store()
    for name in ('rate_rows', 'rate_tables'):
        store.data[name] = atlas.find(name, {'owner_key': 'bank', 'lifecycle': 'active'})
    results = []
    raw_responses = []
    original_request = LocalClient.request
    def capture(self, prompt):
        text = original_request(self, prompt)
        raw_responses.append(text)
        return text
    LocalClient.request = capture
    for message in ('what is the FD rate for 12 months', 'I want a cheque book'):
        session = Sessions(store).load()
        session['auth'].update(state='VERIFIED', user_id='usr_001', account_id='acc_001')
        registry = Registry(session, store)
        # An explicit model prevents all external-provider fallbacks during this check.
        runner = Runner(model='local/default')
        raw_responses.clear()
        with token_scope(session['session_id'], store), log.request('local check'):
            result = runner.run(session, message, registry)
        results.append({'message': message, 'reply': result['reply'], 'tools': [name for name, _ in registry.results], 'raw_responses': list(raw_responses), 'usage': runner.usage})
        print(json.dumps(results[-1], default=str, ensure_ascii=False), flush=True)
    (ROOT / 'data/local_llm_check.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    try:
        main()
    finally:
        close_db()
