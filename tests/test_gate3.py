"""Gate 3 HTTP contracts, privacy, ownership and file lifecycle without providers."""

import json
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.chat import Chat
from app.config import ROOT, get_settings
from app.ingest.models import utcnow
from app.llm import FakeLLM
from app.main import create_app
from app.uploads import UploadService
from tests.memory_store import MemoryStore


@pytest.fixture
def services():
    store = MemoryStore()
    for name in ('users', 'accounts', 'cards', 'transactions'):
        store.data[name] = json.loads((ROOT / 'seed' / f'{name}.json').read_text())
    llm = FakeLLM(['Hello. How can I help?'])
    chat = Chat(store, llm=llm)
    uploads = UploadService(store)
    app = create_app(chat=chat, uploads=uploads, checks=False)
    with TestClient(app) as client:
        client.headers[get_settings().ui['client_header']] = 'tab-a'
        state = client.post('/api/session', json={'client_id': 'tab-a'}).json()
        yield client, store, uploads, state['session_id'], llm


def send(client, sid, text, **extra):
    return client.post('/api/chat', json={'session_id': sid, 'client_id': 'tab-a', 'message': text, **extra})


def upload(client, sid, name='gate3.txt', content=b'Fictional sample note. No real banking terms.'):
    return client.post('/api/upload', data={'session_id': sid, 'client_id': 'tab-a'}, files={'file': (name, content)})


def test_session_chat_restore_and_second_tab(services):
    client, store, uploads, sid, llm = services
    assert send(client, sid, 'hi', request_id='one').status_code == 200
    cached = send(client, sid, 'hi', request_id='one')
    assert cached.status_code == 200 and not llm.calls
    state = client.get('/api/session').json()
    assert state['history'][0]['message'] == 'hi'
    assert state['history'][0]['response']['reply_markdown']
    assert client.post('/api/session', json={'client_id': 'tab-b', 'session_id': sid}).status_code == 409
    assert client.post('/api/chat', json={'session_id': sid, 'client_id': 'tab-b', 'message': 'hi'}).status_code == 409
    assert client.post('/api/session', json={'client_id': 'tab-a', 'session_id': sid}).status_code == 200


def test_upload_file_ownership_delete_and_pending_restore(services):
    client, store, uploads, sid, llm = services
    response = upload(client, sid)
    assert response.status_code == 200, response.text
    card = response.json()
    assert card['attachment_id'] == card['id']
    key = card['id']
    assert client.get(f'/api/upload/{key}').json()['status'] == 'Uploading'
    assert client.get(f'/api/files/{key}').content.startswith(b'Fictional')
    assert client.get('/api/session').json()['attachments'][0]['id'] == key
    with TestClient(client.app) as stranger:
        assert stranger.get(f'/api/files/{key}').status_code == 404
        stranger.post('/api/session', json={'client_id': 'stranger'})
        assert stranger.get(f'/api/files/{key}').status_code == 404
        assert stranger.get(f'/api/upload/{key}', headers={get_settings().ui['client_header']: 'stranger'}).status_code == 404
    assert client.delete(f'/api/upload/{key}').status_code == 200
    assert client.get(f'/api/files/{key}').status_code == 404
    assert client.get('/api/session').json()['attachments'] == []


def test_expired_attachment_stays_in_transcript_and_cannot_be_read(services):
    client, store, uploads, sid, llm = services
    card = upload(client, sid).json()
    doc = store.one('documents', {'attachment.id': card['id']})
    doc['attachment']['status'] = 'Ready'
    store.put('documents', {'doc_id': doc['doc_id']}, doc)
    result = send(client, sid, 'hi', attachment_ids=[card['id']])
    assert result.status_code == 200, result.text
    store.patch('documents', {'doc_id': doc['doc_id']}, {'$set': {'expires_at': utcnow() - timedelta(seconds=1)}})
    assert client.get(f"/api/files/{card['id']}").status_code == 404
    assert client.get(f"/api/upload/{card['id']}").status_code == 404
    history = client.get('/api/session').json()['history']
    assert history[0]['attachments'][0]['status'] == get_settings().ui['expired']
    uploads.cleanup()
    assert client.get('/api/session').json()['history'][0]['attachments'][0]['status'] == get_settings().ui['expired']


def test_otp_history_never_stores_secrets_and_bad_uploads_are_json(services):
    client, store, uploads, sid, llm = services
    assert send(client, sid, 'my balance 9000000001 000000000001').json()['auth_state'] == 'AWAITING_OTP'
    llm.replies = iter([{'tool_calls': [{'name': 'get_balance', 'arguments': {}}]}, ''])
    response = send(client, sid, '123456')
    assert response.status_code == 200
    assert response.json()['auth_state'] == 'VERIFIED'
    assert '₹57,600.00' in response.json()['reply_markdown']
    public = json.dumps(client.get('/api/session').json())
    assert '123456' in public
    assert all(secret not in public for secret in ('9000000001', '000000000001'))
    assert upload(client, sid, 'bad.exe').status_code == 422
    assert upload(client, sid, 'bad.pdf', b'not a PDF').status_code == 422
    assert set(client.post('/api/chat', json={}).json()) == {'error', 'message'}


def test_ui_event_processing_gate_and_files_only(services):
    client, store, uploads, sid, llm = services
    assert client.post('/api/ui-event', json={'session_id': sid, 'event': 'click send', 'detail': {'otp': '123456'}}).status_code == 200
    card = upload(client, sid).json()
    assert send(client, sid, '', attachment_ids=[card['id']]).status_code == 409
    store.patch('documents', {'attachment.id': card['id']}, {'$set': {'attachment.status': 'Ready'}})
    assert send(client, sid, '', attachment_ids=[card['id']]).status_code == 200
    assert client.get('/api/no-such-route').status_code == 404


def test_password_reset_question_is_not_a_credential(services):
    from app.prepass import extract
    client, store, uploads, sid, llm = services
    assert extract('How do I reset my mobile banking password?').text.endswith('password?')
    assert '[secret]' in extract('password: hunter2').text
    assert send(client, sid, 'How do I reset my mobile banking password?').status_code == 200
    assert len(llm.calls) == 1


@pytest.mark.parametrize('text', ['Can a frozen account request a cheque book?', 'How many cheque leaves can I request?', 'What fee applies to a replacement standard debit card?', 'How long does normal debit card delivery take?', 'How many salary slips and bank statements are needed for home loans?'])
def test_policy_questions_do_not_require_login(services, text):
    client, store, uploads, sid, llm = services
    response = send(client, sid, text)
    assert response.json()['auth_state'] == 'ANONYMOUS'
    assert len(llm.calls) == 1


def test_busy_session_and_expired_session_have_safe_errors(services):
    client, store, uploads, sid, llm = services
    store.patch('sessions', {'session_id': sid}, {'$set': {'lease_until': utcnow() + timedelta(minutes=1)}})
    response = send(client, sid, 'hi')
    assert response.status_code == 409
    assert set(response.json()) == {'error', 'message'}
    assert not llm.calls
    store.patch('sessions', {'session_id': sid}, {'$set': {'expires_at': utcnow() - timedelta(seconds=1)}})
    assert client.get('/api/session').status_code == 410
    assert send(client, sid, 'hi').json()['code'] == 'SESSION_EXPIRED'


def test_other_tab_cannot_bypass_binding_with_new_chat_or_ui_event(services):
    client, store, uploads, sid, llm = services
    assert client.post('/api/session', json={'client_id': 'tab-b', 'new_chat': True}).status_code == 409
    response = client.post('/api/ui-event', headers={get_settings().ui['client_header']: 'tab-b'}, json={'session_id': sid, 'event': 'send'})
    assert response.status_code == 409
    assert client.post('/api/session', json={'client_id': 'tab-a', 'new_chat': True}).status_code == 200
    assert send(client, sid, 'hi').status_code == 404


def test_health_returns_dependency_state_without_credentials(services, monkeypatch):
    import app.main as main
    client, store, uploads, sid, llm = services
    monkeypatch.setattr(main, 'health_checks', lambda: {'status': 'ok', 'db': 'ok', 'indexes': {'ready': True}, 'quota_remaining': {}, 'models_configured': {}})
    assert client.get('/api/health').status_code == 200
    monkeypatch.setattr(main, 'health_checks', lambda: {'status': 'degraded'})
    assert client.get('/api/health').status_code == 503


def test_sent_file_deletion_keeps_a_deleted_card(services):
    client, store, uploads, sid, llm = services
    card = upload(client, sid).json()
    store.patch('documents', {'attachment.id': card['id']}, {'$set': {'attachment.status': 'Ready'}})
    assert send(client, sid, 'hi', attachment_ids=[card['id']]).status_code == 200
    assert client.delete(f"/api/upload/{card['id']}").status_code == 200
    restored = client.get('/api/session').json()
    assert restored['history'][0]['attachments'][0]['status'] == get_settings().ui['deleted']
    assert not restored['attachments']


def test_concurrent_retry_cannot_execute_the_same_turn_twice(services):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    client, store, uploads, sid, llm = services
    entered, release = Event(), Event()
    original = llm.chat
    def blocked(messages, **kwargs):
        entered.set()
        assert release.wait(10)
        return original(messages, **kwargs)
    llm.chat = blocked
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(send, client, sid, 'help me', request_id='concurrent')
        assert entered.wait(10)
        second = send(client, sid, 'hi', request_id='concurrent')
        release.set()
        assert first.result().status_code == 200
    assert second.status_code == 409
    assert send(client, sid, 'hi', request_id='concurrent').status_code == 200
    assert len(llm.calls) == 1


def test_numeric_units_match_source_words_without_crossing_units():
    from app.verify import check
    evidence = [('search_bank_knowledge', {'data': {'text': 'Debt cap 50 percent; income INR 25000.'}})]
    assert check('Debt cap 50%; income ₹25,000.', evidence)[0]
    assert not check('Debt cap ₹50.', evidence)[0]
    assert not check('Income 25000%.', evidence)[0]


def test_request_summaries_and_image_ingest(services, monkeypatch):
    import io
    from PIL import Image, ImageDraw
    from app.ingest.embeddings import Embeddings
    from app.ingest.images import ImageCascade
    from app.ingest.pipeline import Pipeline
    from app.log import log, request_context
    from app.session import Sessions
    client, store, uploads, sid, llm = services
    summaries = []
    original = log.step
    def capture(area, action, **fields):
        state = request_context.get()
        if state is not None and action.startswith(('/api/chat  total=', '/api/upload  total=', 'upload  total=')):
            summaries.append(state['id'] + '  ' + action)
        original(area, action, **fields)
    monkeypatch.setattr(log, 'step', capture)
    assert send(client, sid, 'hi').status_code == 200
    session = store.one('sessions', {'session_id': sid})
    session['auth'].update(state='VERIFIED', user_id='usr_001', account_id='acc_001')
    Sessions(store).save(session)
    llm.replies = iter([{'tool_calls': [{'name': 'chequebook_set_slots', 'arguments': {'pages': 40}}]}])
    assert send(client, sid, 'Request a cheque book with 40 pages').status_code == 200
    client.app.state.api.chat.embeddings = FakeLLM()
    store.put('documents', {'doc_id': 'smoke-bank'}, {'doc_id': 'smoke-bank', 'title': 'Fictional bank FAQ'})
    store.put('chunks', {'chunk_id': 'smoke-neft'}, {
        'chunk_id': 'smoke-neft', 'section_id': 'smoke-neft', 'doc_id': 'smoke-bank',
        'namespace': 'bank', 'owner_key': 'bank', 'lifecycle': 'active',
        'text': 'NEFT transfers are available.', 'page': 1, 'version': '1',
        'effective_date': '2026-10-01', 'confidence': 1.0})
    llm.replies = iter([{'tool_calls': [{'name': 'search_bank_knowledge', 'arguments': {'query': 'NEFT charges'}}]},
                        'NEFT transfers are available. [1]'])
    assert send(client, sid, 'What are NEFT charges?').status_code == 200
    # Exercise real upload parsing/chunking/storage, with deterministic OCR and embeddings.
    image = Image.new('RGB', (800, 400), 'white')
    ImageDraw.Draw(image).text((20, 20), 'A fictional bank service note for customers.', fill='black')
    blob = io.BytesIO()
    image.save(blob, format='PNG')
    text = 'A fictional bank service note for customers. ' * 40
    def ocr(image):
        with log.span('OCR', 'fixture OCR'):
            return [[[[20, 20], [780, 20], [780, 380], [20, 380]], text, 0.99]]
    uploads.pipeline = Pipeline(store, images=ImageCascade(store, ocr=ocr),
                                embeddings=Embeddings(store, FakeLLM(), pace=False))
    card = upload(client, sid, 'summary.png', blob.getvalue()).json()
    document = store.one('documents', {'attachment.id': card['id']})
    with log.request('upload', document['request_id']):
        assert uploads.process(sid, card['id'])['status'] == 'Ready'
    assert all('r-' in line for line in summaries)
    assert ['path=greeting', 'path=flow-display', 'path=rag'] == [
        next(part for part in line.split() if part.startswith('path=')) for line in summaries[:3]]
    assert 'ocr_pages=1' in summaries[-1] and 'source=ocr' in summaries[-1]
    (ROOT / 'data' / 'request_smoke_summaries.txt').write_text('\n'.join([*summaries[:3], summaries[-1]]), encoding='utf-8')


def test_db_debug_aggregates_and_context_resets(services, monkeypatch):
    from app.log import log, request_context
    from app.ingest.store import Store
    from types import SimpleNamespace
    lines = []
    monkeypatch.setattr(log.console, 'print', lambda value: lines.append(str(value)))
    monkeypatch.setattr('app.ingest.store.collection', lambda name: SimpleNamespace(find_one=lambda query, **kwargs: None))
    with log.request('chat') as state:
        Store().one('sessions', {})
        assert state['spans'][0]['area'] == 'DB'
        request_id = state['id']
    assert request_context.get() is None
    assert not any('find one' in line for line in lines)
    assert any(request_id in line and '1 ops' in line for line in lines)
