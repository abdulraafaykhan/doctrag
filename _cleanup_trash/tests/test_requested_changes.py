"""Narrow, offline checks for the six requested changes."""
import io
import json
from types import SimpleNamespace

import httpx
from fastapi.testclient import TestClient
from rich.console import Console

from app import usage
from app.config import ROOT
from app.llm import FakeLLM
from tests.memory_store import MemoryStore


def test_item1_fake_meter_without_native_import(monkeypatch):
    import app.llm as llm
    meter = usage.TokenMeter()
    monkeypatch.setattr(usage, 'meter', meter)
    monkeypatch.setattr(llm, 'meter', meter)
    with usage.token_scope('two-fakes'):
        fake = FakeLLM(['one', 'two'])
        fake.chat([]); fake.chat([])
    assert meter.session('two-fakes') == {'in': 0, 'out': 0, 'total': 0, 'calls': 2}
    assert all(event['cached'] for event in meter.events)


def fresh_meter(monkeypatch):
    import app.llm as llm
    import app.agent as agent
    value = usage.TokenMeter()
    import app.api as api
    for module in (usage, llm, agent, api):
        monkeypatch.setattr(module, 'meter', value)
    return value


def test_item1_fake_two_calls_and_exact_native_totals(monkeypatch):
    from app.agent import LoggedTransport
    meter = fresh_meter(monkeypatch)
    store = MemoryStore()
    store.put('sessions', {'session_id': 'meter-test'}, {'session_id': 'meter-test', 'counters': {}})
    with usage.token_scope('meter-test', store):
        fake = FakeLLM(['a', 'b'])
        fake.chat([]); fake.chat([])
        assert meter.session('meter-test') == {'in': 0, 'out': 0, 'total': 0, 'calls': 2}
        assert all(event['cached'] for event in meter.events)
        responses = iter([(11, 3), (7, 2)])
        def respond(request):
            incoming, outgoing = next(responses)
            return httpx.Response(200, json={'usage': {'prompt_tokens': incoming, 'completion_tokens': outgoing}, 'choices': []})
        transport = LoggedTransport(SimpleNamespace(reserve=lambda *args: None), [])
        transport.inner.close()
        transport.inner = httpx.MockTransport(respond)
        for _ in range(2):
            transport.handle_request(httpx.Request('POST', 'https://offline.test/chat', json={'model': 'stub', 'messages': []}))
        assert meter.session('meter-test') == {'in': 18, 'out': 5, 'total': 23, 'calls': 4}
        assert store.one('sessions', {'session_id': 'meter-test'})['counters']['tokens']['total'] == 23
        assert usage.response_tokens('meter-test')['request']['total'] == 23
        transport.close()


def test_item1_lifespan_prints_tokens_on_shutdown(monkeypatch):
    import app.main as main
    from app.chat import Chat
    from app.log import log
    from app.uploads import UploadWorker
    meter = fresh_meter(monkeypatch)
    meter.add('shutdown-test', 'stub', 'llm', 8, 2)
    output = io.StringIO()
    monkeypatch.setattr(log, 'console', Console(file=output, width=240, color_system=None))
    monkeypatch.setattr(main, 'health_checks', lambda: {'status': 'ok'})
    monkeypatch.setattr(main, 'close_db', lambda: None)
    monkeypatch.setattr(UploadWorker, 'start', lambda self: None)
    monkeypatch.setattr(UploadWorker, 'stop', lambda self: None)
    with TestClient(main.create_app(chat=Chat(MemoryStore(), llm=FakeLLM()))):
        pass
    assert 'SESSION TOKENS' in output.getvalue()
    assert 'PROCESS TOKENS' in output.getvalue()
    assert 'total=10' in output.getvalue()


def test_item1_dev_launcher_forwards_sigint(tmp_path):
    import os
    import subprocess
    import queue
    from threading import Thread
    npm = tmp_path / 'fake-npm.js'
    npm.write_text('setInterval(() => {}, 1000);')
    javascript = "process.argv=['node','launcher',process.argv[1],process.argv[2],'scripts.ui_fixture:app','0']; import('./scripts/dev.mjs'); process.stdin.once('data',()=>{process.stdin.destroy();process.emit('SIGINT')});"
    child = subprocess.Popen(['node', '--input-type=module', '-e', javascript, str(ROOT / '.venv/Scripts/python.exe'), str(npm)], cwd=ROOT, env=os.environ.copy(), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8')
    lines, ready = [], queue.Queue()
    def output():
        for line in child.stdout:
            lines.append(line)
            (ROOT / 'data/grace_test.log').write_text(''.join(lines), encoding='utf-8')
            if 'Uvicorn running' in line:
                ready.put(True)
        ready.put(False)
    reader = Thread(target=output, daemon=True)
    reader.start()
    try:
        assert ready.get(timeout=600), ''.join(lines)
        child.stdin.write('SIGINT\n'); child.stdin.flush()
        assert child.wait(timeout=600) == 0
        reader.join(10)
        assert 'PROCESS TOKENS' in ''.join(lines)
        assert 'Application shutdown complete' in ''.join(lines)
        assert 'Traceback' not in ''.join(lines)
    finally:
        if child.poll() is None:
            child.kill()


def test_item2_trace_trees_and_structured_spans(monkeypatch):
    from app.main import create_app
    from app.chat import Chat
    from app.log import log
    from app.session import Sessions
    store = MemoryStore()
    for name in ('users', 'accounts', 'cards', 'transactions'):
        store.data[name] = json.loads((ROOT / 'seed' / f'{name}.json').read_text())
    output = io.StringIO()
    monkeypatch.setattr(log, 'console', Console(file=output, width=300, color_system=None))
    llm = FakeLLM([{'tool_calls': [{'name': 'chequebook_set_slots', 'arguments': {}}]}, ''])
    with TestClient(create_app(chat=Chat(store, llm=llm), checks=False)) as client:
        client.headers['x-client-id'] = 'trace-tab'
        sid = client.post('/api/session', json={'client_id': 'trace-tab'}).json()['session_id']
        for message in ('hi', 'I need a new chequebook'):
            row = store.one('sessions', {'session_id': sid})
            row['auth'].update(state='VERIFIED', user_id='usr_001', account_id='acc_001')
            Sessions(store).save(row)
            response = client.post('/api/chat', json={'session_id': sid, 'client_id': 'trace-tab', 'message': message})
            assert response.status_code == 200, response.text
    rendered = output.getvalue()
    (ROOT / 'data/requested_traces.txt').write_text(rendered, encoding='utf-8')
    assert rendered.count('TRACE TREE') == 2
    assert 'app.prepass.extract' in rendered
    assert 'app.agent.Runner.run' in rendered
    assert 'chequebook_set_slots' in rendered
    assert ' db=' in rendered and 'PROCESS TOKENS' in rendered
    path = ROOT / 'data/logs/app.log'
    spans = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if '"type": "span"' in line]
    assert spans
    required = {'trace_id', 'span_id', 'parent_span_id', 'name', 'area', 'module', 'function', 'start_time', 'duration_ms', 'status', 'attributes'}
    assert all(required <= span.keys() for span in spans)
    assert all(span['status'] in {'ok', 'error', 'skipped'} for span in spans)


def test_item3_six_digits_ocr_and_otp_history():
    from app.log import mask_text
    from app.prepass import extract
    from app.vision import postprocess_vision
    from app.ingest.chunking import chunk_elements
    from app.ingest.models import Element, DocumentMeta
    from app.chat import Chat
    from app.main import create_app
    assert mask_text('Ref 482913') == 'Ref 482913'
    assert extract('OTP 482913').text == 'OTP 482913'
    assert extract('OTP 482913').code == '482913'
    assert '9000000001' not in mask_text('mobile 9000000001')
    assert postprocess_vision({'ref': '482913'}, 'Ref 482913')['ref'] == '482913'
    document = {**DocumentMeta(title='Six digits').model_dump(), 'doc_id': 'six-digits', 'namespace': 'bank', 'owner_key': 'bank'}
    _, chunks = chunk_elements([Element(type='text', content='Ref 482913', page=1, source_method='text_layer')], document)
    assert 'Ref 482913' in chunks[0]['text']
    store = MemoryStore()
    for name in ('users', 'accounts', 'cards', 'transactions'):
        store.data[name] = json.loads((ROOT / 'seed' / f'{name}.json').read_text())
    llm = FakeLLM([{'tool_calls': [{'name': 'get_balance', 'arguments': {}}]}])
    with TestClient(create_app(chat=Chat(store, llm=llm), checks=False)) as client:
        client.headers['x-client-id'] = 'otp-demo'
        sid = client.post('/api/session', json={'client_id': 'otp-demo'}).json()['session_id']
        def send(text):
            return client.post('/api/chat', json={'session_id': sid, 'client_id': 'otp-demo', 'message': text})
        assert send('my balance 9000000001 000000000001').json()['auth_state'] == 'AWAITING_OTP'
        assert send('482913').json()['auth_state'] == 'VERIFIED'
        assert client.get('/api/session').json()['history'][-1]['message'] == '482913'
        assert '482913' not in json.dumps(llm.calls)
        assert '9000000001' not in json.dumps(llm.calls)


def test_item4_idle_deletes_owned_rows_files_and_heartbeat_never_touches(monkeypatch, tmp_path):
    from datetime import timedelta
    from app.config import get_settings
    from app.ingest.models import digest, utcnow
    from app.chat import Chat
    from app.main import create_app
    from app.uploads import UploadService
    import app.uploads as uploads
    monkeypatch.setitem(get_settings().session, 'idle_minutes', 0.05)
    monkeypatch.setattr(uploads, 'ROOT', tmp_path)
    store = MemoryStore()
    service = UploadService(store)
    with TestClient(create_app(chat=Chat(store, llm=FakeLLM()), uploads=service, checks=False)) as client:
        client.headers['x-client-id'] = 'idle-tab'
        sid = client.post('/api/session', json={'client_id': 'idle-tab'}).json()['session_id']
        initial = store.one('sessions', {'session_id': sid})['last_active_at']
        assert client.get('/api/session').status_code == 200
        assert store.one('sessions', {'session_id': sid})['last_active_at'] == initial
        assert store.one('sessions', {'session_id': sid})['last_seen'] >= initial
        for name in ('documents', 'sections', 'chunks', 'rate_tables', 'rate_rows', 'extractions'):
            store.put(name, {'id': name}, {'id': name, 'doc_id': 'idle-doc', 'owner_key': sid})
        store.put('pending_actions', {'id': 'pending'}, {'id': 'pending', 'session_id': sid})
        store.put('audit_log', {'id': 'audit'}, {'id': 'audit', 'session_id': sid})
        for directory in ('uploads', 'crops'):
            path = tmp_path / 'data' / directory / digest(sid) / 'file.txt'
            path.parent.mkdir(parents=True, exist_ok=True); path.write_text('private')
        cache = tmp_path / 'data/test_cache' / f'agent_{digest(sid)}_test.json'
        cache.parent.mkdir(parents=True, exist_ok=True); cache.write_text('{}')
        store.patch('sessions', {'session_id': sid}, {'$set': {'last_active_at': utcnow() - timedelta(seconds=4)}})
        service.cleanup()
        for name in ('documents', 'sections', 'chunks', 'rate_tables', 'rate_rows', 'extractions', 'sessions', 'pending_actions'):
            assert store.count(name, {}) == 0, name
        assert store.count('audit_log', {}) == 1
        assert not cache.exists()
        assert not list((tmp_path / 'data/uploads').glob('*/*'))
        assert not list((tmp_path / 'data/crops').glob('*/*'))
        for response in (client.get('/api/session'), client.post('/api/chat', json={'session_id': sid, 'client_id': 'idle-tab', 'message': 'hi'})):
            assert response.status_code == 410
            assert response.json()['code'] == 'SESSION_EXPIRED'
        expired_upload = client.post('/api/upload', data={'session_id': sid, 'client_id': 'idle-tab'}, files={'file': ('expired.txt', b'expired', 'text/plain')})
        assert expired_upload.status_code == 410 and expired_upload.json()['code'] == 'SESSION_EXPIRED'
        fresh = client.post('/api/session', json={'client_id': 'idle-tab'}).json()['session_id']
        assert fresh != sid
        store.patch('sessions', {'session_id': fresh}, {'$set': {'last_active_at': utcnow() - timedelta(seconds=4)}})
        assert client.post('/api/session', json={'client_id': 'idle-tab'}).json()['session_id'] != fresh
        assert store.one('sessions', {'session_id': fresh}) is None


def test_item6_cached_pdf_extractions_chunks_and_vector_reuse(monkeypatch, tmp_path):
    import base64
    from app.config import get_settings
    from app.ingest.models import DocumentMeta, digest
    from app.ingest.parsers import parse_file
    from app.ingest.images import ImageCascade
    from app.ingest.pipeline import Pipeline
    from app.ingest.embeddings import Embeddings
    from scripts.inspect_db import run, PURPOSES
    from scripts.make_sample_docs import HEADERS, RATE_ROWS
    from tests.test_gate1 import image_fixture, FakeEmbeddings
    for module in ('images', 'pipeline', 'embeddings'):
        monkeypatch.setattr(f'app.ingest.{module}.ROOT', tmp_path)
    store = MemoryStore()
    source = ROOT / 'data/bank_docs/rates_image.pdf'
    elements, _, _ = parse_file(source)
    blob = base64.b64decode(next(element.content['base64'] for element in elements if element.type == 'image'))
    blocks = []
    for row_index, row in enumerate([HEADERS, *RATE_ROWS]):
        for column, text in enumerate(row):
            x, y = 20 + column * 250, 30 + row_index * 55
            blocks.append(([[x, y], [x + 150, y], [x + 150, y + 24], [x, y + 24]], text, .99))
    wrong = [list(row) for row in RATE_ROWS]
    wrong[0][2] = '7.61%'
    fake = FakeLLM([json.dumps({'columns': HEADERS, 'rows': wrong, 'overall_confidence': .99})])
    cascade = ImageCascade(store, fake, lambda image: blocks)
    cascade.extract(blob, 'bank', mode='ingest')
    assert len(fake.calls) == 1
    pipeline = Pipeline(store, images=cascade, embeddings=FakeEmbeddings())
    result = pipeline.ingest(source, DocumentMeta(title='Cached sample image PDF', doc_type='rate_card', product='fixed_deposit'))
    assert len(fake.calls) == 1, 'Cached ingestion must never call the provider'
    assert result['job']['state'] == 'done'
    assert pipeline.ingest(source, DocumentMeta(title='Cached sample image PDF'))['skipped']
    extraction = store.one('extractions', {'doc_id': result['doc_id']})
    assert extraction['compare']['agreed_count'] > 0
    assert extraction['compare']['disagreed_count'] > 0
    assert extraction['needs_review']
    for chunk in store.find('chunks', {'doc_id': result['doc_id']}):
        assert {'filename', 'doc_title', 'page', 'section_path', 'kind', 'source_method', 'extraction_id', 'needs_review', 'review_flags', 'content_hash', 'embedding', 'embedding_dims', 'embedding_model', 'created_at', 'expires_at'} <= chunk.keys()
        if chunk['source_method'] in {'ocr', 'vlm'}:
            assert chunk['extraction_id'] == extraction['extraction_id']
        else:
            assert chunk['extraction_id'] is None
        assert chunk['embedding_dims'] == 768 and chunk['embedding']
        assert not chunk['text'].lstrip().startswith('{')
    slip_blob, slip_blocks = image_fixture(False)
    slip_blocks = [(box, text, confidence) for (box, _, confidence), text in zip(slip_blocks * 2, ['Ref: 482913', 'Amount: 1250'], strict=False)]
    # Keep distinct lines so fields are compared against their own OCR labels.
    slip_blocks[1] = ([[20, 150], [300, 150], [300, 175], [20, 175]], 'Amount: 1250', .99)
    slip_fake = FakeLLM([json.dumps({'fields': {'Ref': {'value': '482913'}, 'Amount': {'value': '1258'}}, 'overall_confidence': .99})])
    ImageCascade(store, slip_fake, lambda image: slip_blocks).extract(slip_blob, 'bank', mode='ingest', doc_id='offline-slip', filename='offline-slip.png')
    store.put('documents', {'doc_id': 'offline-slip'}, {'doc_id': 'offline-slip', 'filename': 'offline-slip.png'})
    slip = store.one('extractions', {'doc_id': 'offline-slip'})
    assert slip['compare']['agreed_count'] >= 1 and slip['compare']['disagreed_count'] >= 1
    assert slip['vlm']['structured']['fields']['Amount']['value'] is None
    assert 'Ref: 482913' in slip['result']['text'] and '"fields"' not in slip['result']['text']
    output = io.StringIO()
    for doc in (result['doc_id'], 'offline-slip'):
        run(['extraction', doc], store=store, console=Console(file=output, width=135, color_system=None))
    (ROOT / 'data/requested_extractions.txt').write_text(output.getvalue(), encoding='utf-8')
    text = 'Unique chunk reuse without a provider 482913'
    vector = [1.] + [0.] * 767
    store.put('chunks', {'chunk_id': 'reuse'}, {'chunk_id': 'reuse', 'content_hash': digest(text), 'embedding_model': get_settings().models.embed, 'embedding': vector})
    client = SimpleNamespace(embed=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('provider called')))
    assert Embeddings(store, client, pace=False).embed([text]) == [vector]
    assert len(PURPOSES) == 15
    assert not any(store.data.get(name) for name in ('ingest_jobs', 'image_cache', 'embed_cache'))


def test_item6_migration_preserves_jobs_vectors_images_and_unknown_collections():
    from scripts.migrate_storage import migrate
    from app.config import get_settings
    from app.ingest.models import digest
    store = MemoryStore()
    indexes = {}
    class Collection:
        def __init__(self, database, name): self.database, self.name = database, name
        def count_documents(self, query): return store.count(self.name, query)
        def find(self, query): return store.find(self.name, query)
        def find_one(self, query): return store.one(self.name, query)
        def update_one(self, query, change, **kwargs): return store.patch(self.name, query, change, **kwargs)
        def insert_one(self, row): store.data[self.name].append(row)
        def drop(self): store.data.pop(self.name, None)
        def rename(self, name, **kwargs): store.data[name] = store.data.pop(self.name)
        def index_information(self): return indexes
        def drop_index(self, name): indexes.pop(name)
        def create_index(self, *args, **kwargs): pass
    class Database:
        name = 'offline-migration'
        def list_collection_names(self): return list(store.data)
        def __getitem__(self, name): return Collection(self, name)
    db = Database()
    store.data['documents'] = [{'_id': 'doc', 'doc_id': 'doc', 'filename': 'old.pdf', 'title': 'Old document'}]
    store.data['ingest_jobs'] = [{'_id': 'job', 'doc_id': 'doc', 'state': 'paused_quota', 'stage': 'embed', 'cursor': 4, 'attempts': 2}]
    store.data['image_cache'] = [{'_id': 'image', 'image_hash': 'hash', 'owner_key': 'bank', 'result': {'text': 'cached'}}]
    store.data['chunks'] = [{'_id': 'chunk', 'doc_id': 'doc', 'text': 'Cached embedding', 'chunk_id': 'chunk'}]
    vector = [1.] + [0.] * 767
    key = f'{get_settings().models.embed}|768|document|{digest("Cached embedding")}'
    store.data['embed_cache'] = [{'key': key, 'embedding': vector}]
    store.data['unknown_keep'] = [{'safe': True}]
    migrate(db)
    assert store.data['ingest_jobs'] and store.data['image_cache']
    migrate(db, apply=True)
    assert not {'ingest_jobs', 'image_cache', 'embed_cache'} & set(store.data)
    assert store.one('documents', {'doc_id': 'doc'})['job']['cursor'] == 4
    assert store.one('chunks', {})['embedding'] == vector
    assert store.one('extractions', {})['result']['text'] == 'cached'
    assert store.one('extractions', {})['ocr'] == {}
    assert store.data['unknown_keep'] == [{'safe': True}]


def test_item5_slow_reply_keeps_concurrent_upload_and_meter_updates():
    from app.session import Sessions
    store = MemoryStore()
    sessions = Sessions(store)
    stale = sessions.load()
    sid = stale['session_id']
    store.patch('sessions', {'session_id': sid}, {'$set': {'attachments': [{'id': 'during-reply'}], 'counters': {'uploads_count': 1, 'tokens': {'in': 5, 'out': 2, 'total': 7, 'calls': 1}}}})
    stale['history'] = [{'role': 'assistant', 'content': 'Slow reply'}]
    sessions.save(stale)
    current = store.one('sessions', {'session_id': sid})
    assert current['attachments'] == [{'id': 'during-reply'}]
    assert current['counters']['tokens']['total'] == 7
    assert current['counters']['uploads_count'] == 1
    store.delete('sessions', {'session_id': sid})
    sessions.save(stale)
    assert store.one('sessions', {'session_id': sid}) is None


def test_item1_gemini_usage_thoughts_groq_usage_and_thread_safety(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from app.llm import record_usage
    meter = fresh_meter(monkeypatch)
    with usage.token_scope('providers'):
        record_usage(SimpleNamespace(usage_metadata=SimpleNamespace(prompt_token_count=12, candidates_token_count=3, thoughts_token_count=2)), 'gemini-stub', 'vlm')
        record_usage(SimpleNamespace(usage=SimpleNamespace(prompt_tokens=7, completion_tokens=4)), 'groq-stub', 'vlm')
    assert meter.session('providers') == {'in': 19, 'out': 9, 'total': 28, 'calls': 2}
    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(lambda _: meter.add('threads', 'stub', 'llm', 2, 1), range(40)))
    assert meter.session('threads') == {'in': 80, 'out': 40, 'total': 120, 'calls': 40}


def test_item1_api_request_session_and_reload_totals(monkeypatch):
    from app.chat import Chat
    from app.main import create_app
    from app.llm import record_usage
    fresh_meter(monkeypatch)
    store = MemoryStore()
    def run(session, text, registry):
        record_usage(SimpleNamespace(usage=SimpleNamespace(prompt_tokens=11, completion_tokens=4)), 'offline-sdk-stub')
        return {'reply': 'A bank answer.', 'citations': []}
    with TestClient(create_app(chat=Chat(store, runner=SimpleNamespace(run=run)), checks=False)) as client:
        client.headers['x-client-id'] = 'tokens-tab'
        sid = client.post('/api/session', json={'client_id': 'tokens-tab'}).json()['session_id']
        for index in (1, 2):
            body = {'client_id': 'tokens-tab', 'message': 'Explain this banking policy'}
            if index == 1:
                body['session_id'] = sid
            response = client.post('/api/chat', json=body).json()
            assert response['tokens']['request'] == {'in': 11, 'out': 4, 'total': 15}
            assert response['tokens']['session'] == {'in': 11 * index, 'out': 4 * index, 'total': 15 * index, 'calls': index}
        saved = store.one('sessions', {'session_id': sid})['counters']['tokens']
        assert saved['total'] == 30
        fresh_meter(monkeypatch)
        restored = client.get('/api/session').json()['tokens']
        assert restored['request'] == {'in': 0, 'out': 0, 'total': 0}
        assert restored['session'] == saved
