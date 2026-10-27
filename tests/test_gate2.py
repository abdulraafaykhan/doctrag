"""Gate 2 code paths use FakeLLM and an isolated synthetic customer store."""

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import pytest
from app.chat import Chat
from app.config import ROOT, get_settings
from app.flows import Flows
from app.ingest.models import utcnow
from app.llm import FakeLLM
from app.prepass import extract
from app.session import Sessions, verify_otp
from app.tools.registry import Registry
from app.verify import check
from tests.memory_store import MemoryStore


@pytest.fixture
def store():
    result = MemoryStore()
    for name in ['users', 'accounts', 'cards', 'transactions']:
        for row in json.loads((ROOT / 'seed' / f'{name}.json').read_text()):
            result.data[name].append(row)
    return result


@pytest.fixture
def verified(store):
    session = Sessions(store).load()
    session['auth'].update(state='VERIFIED', user_id='usr_001', account_id='acc_001')
    Sessions(store).save(session)
    return session


def call(name, **arguments):
    return {'tool_calls': [{'name': name, 'arguments': arguments}]}


def test_A1_A3_A9_replay_and_masking(store):
    llm = FakeLLM([call('get_balance'), '', call('get_statement', period='last_5'), '', call('get_card_info'), ''])
    chat = Chat(store, llm=llm)
    first = chat.turn('show my balance')
    assert 'mobile' in first['reply'] and 'account' in first['reply']
    sid = first['session_id']
    mobile = chat.turn('+91 90000 00001', session_id=sid)
    assert 'account' in mobile['reply'] and 'mobile' not in mobile['reply']
    otp = chat.turn('0000-0000-0001', session_id=sid)
    assert otp['state'] == 'AWAITING_OTP'
    # Identity-only messages must not displace the actual held requests.
    session = store.one('sessions', {'session_id': sid})
    session['auth']['held'] = [{'text': text, 'attachments': []} for text in ['my balance', 'last five transactions', 'card details']]
    Sessions(store).save(session)
    reply = chat.turn('123456', session_id=sid)
    assert reply['state'] == 'VERIFIED'
    assert reply['tools_called'] == ['get_balance', 'get_statement', 'get_card_info']
    assert '₹57,600.00' in reply['reply']
    saved = json.dumps(store.one('sessions', {'session_id': sid}), default=str)
    assert '123456' not in saved
    assert '123456' not in json.dumps(llm.calls)


def test_A4_A6_A12_lock_cancel(store):
    chat = Chat(store, llm=FakeLLM())
    result = chat.turn('balance 9000000002 000000000001')
    sid = result['session_id']
    for _ in range(2):
        result = chat.turn('9000000002 000000000001', session_id=sid)
    assert 'Too many' in result['reply']
    other = chat.turn('balance 9000000001 000000000001')
    sid = other['session_id']
    result = chat.turn('12ab', session_id=sid)
    assert '6-digit' in result['reply']
    assert store.one('sessions', {'session_id': sid})['auth']['failures'] == 1
    chat.turn('cancel', session_id=sid)
    assert not store.one('sessions', {'session_id': sid})['auth']['held']


def test_A5_A7_A8_A10_A11(store, verified):
    assert extract('123456').code == '123456'
    assert not verify_otp('123456', {'otp_expires_at': utcnow() - timedelta(seconds=1)})
    llm = FakeLLM(['Please ask a bank question.', 'NEFT information is unavailable.'])
    chat = Chat(store, llm=llm)
    result = chat.turn('123456')
    assert result['state'] == 'ANONYMOUS'
    pending = chat.turn('balance 9000000001 000000000001')
    sid = pending['session_id']
    result = chat.turn('what are NEFT charges?', session_id=sid)
    assert result['state'] == 'AWAITING_OTP'
    session = store.one('sessions', {'session_id': sid})
    session['auth']['otp_expires_at'] = utcnow() - timedelta(seconds=1)
    Sessions(store).save(session)
    assert 'expired' in chat.turn('123456', session_id=sid)['reply']
    assert store.one('sessions', {'session_id': sid})['auth']['held']
    different = chat.turn('balance 000000000002', session_id=verified['session_id'])
    assert different['state'] != 'VERIFIED'
    assert not different['tools_called']
    verified['last_active_at'] = utcnow() - timedelta(minutes=16)
    Sessions(store).save(verified)
    assert Sessions(store).load(verified['session_id'])['auth']['state'] == 'ANONYMOUS'


def test_B1_B4_B8_B9_flow_confirm_once(store, verified):
    flows = Flows(store)
    assert flows.set_slots(verified, 'chequebook')['ok']
    assert flows.fast(verified, '50')['code'] == 'INVALID_ARGUMENT'
    assert 'pages' not in verified['active_flow']['slots']
    assert flows.fast(verified, 'the 40 one')['ok']
    flows.fast(verified, 'urgent')
    summary = flows.fast(verified, 'yes')
    assert all(value in summary['display'] for value in ['40', 'Emergency', 'Address'])
    assert verified['active_flow']['status'] == 'AWAITING_CONFIRM'
    assert flows.fast(verified, 'ok maybe')['code'] == 'NEEDS_MORE_INFO'
    Sessions(store).save(verified)
    chat = Chat(store, llm=FakeLLM())
    first = chat.turn('confirm', session_id=verified['session_id'])
    second = chat.turn('confirm', session_id=verified['session_id'])
    assert first['reply'] == second['reply']
    assert store.count('requests', {'type': 'chequebook'}) == 1


def test_B2_B5_B7_B10_B11_B12_B13(store, verified):
    flow = Flows(store)
    assert flow.submit(verified)['code'] == 'INVALID_ARGUMENT'
    assert 'address' in flow.set_slots(verified, 'chequebook', pages=40, delivery='urgent')['display'].lower()
    assert flow.set_slots(verified, 'debit_card')['code'] == 'FLOW_ACTIVE'
    assert flow.set_slots(verified, 'chequebook', address={'mode': 'new', 'line1': '123 Main Street', 'city': 'Mumbai', 'state': 'MH', 'pin': '4110'})['code'] == 'INVALID_ARGUMENT'
    flow.set_slots(verified, 'chequebook', address={'mode': 'on_file'})
    old = verified['active_flow']['pending_token']
    assert '60' in flow.fast(verified, 'change pages to 60')['display']
    assert old != verified['active_flow']['pending_token']
    verified['active_flow']['updated_at'] = utcnow() - timedelta(minutes=16)
    assert flow.fast(verified, 'confirm')['code'] == 'EXPIRED'
    flow.set_slots(verified, 'chequebook')
    flow.fast(verified, 'cancel')
    assert not verified['active_flow'] and not store.count('requests', {})


def test_B6_B14_agent_flow_paths(store, verified):
    flow = Flows(store)
    flow.set_slots(verified, 'chequebook', pages=40)
    Sessions(store).save(verified)
    llm = FakeLLM(['I could not find the charges.'])
    result = Chat(store, llm=llm).turn('what are NEFT charges?', session_id=verified['session_id'])
    assert 'delivery' in result['reply'].lower()
    llm = FakeLLM([call('chequebook_set_slots'), ''])
    chat = Chat(store, llm=llm)
    pending = chat.turn('I need a chequebook 9000000001 000000000001')
    result = chat.turn('123456', session_id=pending['session_id'])
    assert result['flow']['flow'] == 'chequebook'


def test_C1_C2_C3_C4_C6_C7(store, verified):
    registry = Registry(verified, store)
    assert '₹57,600.00' in registry.tools['get_balance'].invoke()['display']
    statement = registry.tools['get_statement'].invoke({'period': 'last_5'})
    assert len(statement['data']['transactions']) == 5
    dates = [row['date'] for row in statement['data']['transactions']]
    assert dates == sorted(dates, reverse=True)
    for start, end in [('2026-01-01', '2026-05-01'), ('2026-05-01', '2026-01-01')]:
        result = registry.tools['get_statement'].invoke({'period': 'custom', 'from_date': start, 'to_date': end})
        assert result['code'] == 'INVALID_ARGUMENT' and '90' in result['display']
    card = registry.tools['get_card_info'].invoke()
    assert '****0001' in card['display'] and 'cvv' not in card['data']
    verified['auth'].update(user_id='usr_010', account_id='acc_010')
    assert 'frozen' in Registry(verified, store).tools['get_balance'].invoke()['display']


def test_C5_multitool(store, verified):
    store.data['rate_rows'].append({'namespace': 'bank', 'owner_key': 'bank', 'lifecycle': 'active', 'needs_review': False, 'product': 'fixed_deposit', 'effective_date': '2026-01-01', 'dims': {'tenure_months': 12, 'segment': 'general'}, 'values': {'rate_percent': '7.10'}, 'source': {'doc_id': 'rates', 'page': 1}, 'raw': {}})
    llm = FakeLLM([{'tool_calls': [{'name': 'get_balance', 'arguments': {}}, {'name': 'get_statement', 'arguments': {'period': 'last_10'}}, {'name': 'lookup_rate', 'arguments': {'product': 'fixed_deposit', 'tenure_months': 12, 'segment': 'general'}}]}, 'FD rate 7.10%.'])
    result = Chat(store, llm=llm).turn('balance last ten transactions FD rate', session_id=verified['session_id'])
    assert result['tools_called'] == ['get_balance', 'get_statement', 'lookup_rate']
    assert '7.10%' in result['reply'] and '₹57,600.00' in result['reply']


def test_G_visibility_identity_schema_and_no_submit(store, verified):
    anonymous = Sessions(store).load()
    registry = Registry(anonymous, store)
    assert 'get_balance' not in registry.visible()
    assert 'submit_request' not in registry.tools
    assert registry.tools['get_balance'].invoke()['code'] == 'NOT_VERIFIED'
    own = Registry(verified, store)
    assert own.tools['get_balance'].invoke({'account_id': 'acc_002'})['code'] == 'INVALID_ARGUMENT'
    assert not store.count('requests', {})
    Flows(store).set_slots(verified, 'chequebook')
    assert 'get_balance' not in Registry(verified, store).visible()


@pytest.mark.parametrize('value', ['+91 90000 00001 account 0000-0000-0001 OTP 654321', 'my PIN is 987654', 'CVV 123'])
def test_G7_G13_secrets(value):
    safe = extract(value).text
    assert not re_search_identity(safe)
    assert all(secret not in safe for secret in ['654321', '987654', 'CVV 123'])


def re_search_identity(text):
    import re
    return re.search(r'(?<!\d)\d{10}(?!\d)|(?<!\d)\d{12}(?!\d)', text)


def test_G10_address_is_literal(store, verified):
    flow = Flows(store)
    address = {'mode': 'new', 'line1': '{"$ne": null}', 'city': 'Mumbai', 'state': 'MH', 'pin': '411001'}
    flow.set_slots(verified, 'chequebook', pages=40, delivery='normal', address=address)
    assert flow.submit(verified)['ok']
    assert store.one('requests', {})['slots']['address']['line1'] == '{"$ne": null}'


def test_G11_G12_cap_and_rapid_attempts(store):
    chat = Chat(store, llm=FakeLLM())
    assert 'shorter' in chat.turn('x' * 20000)['reply']
    pending = chat.turn('balance 9000000001 000000000001')
    for _ in range(3):
        result = chat.turn('bad', session_id=pending['session_id'])
    assert 'Too many' in result['reply']


def test_I3_atomic_submit_failure(store, verified, monkeypatch):
    flow = Flows(store)
    flow.set_slots(verified, 'chequebook', pages=40, delivery='normal', address={'mode': 'on_file'})
    original = store.patch
    def broken(name, *args, **kwargs):
        if name == 'requests':
            raise TimeoutError()
        return original(name, *args, **kwargs)
    monkeypatch.setattr(store, 'patch', broken)
    assert flow.submit(verified)['code'] == 'UPSTREAM_ERROR'
    assert not store.count('requests', {}) and verified['active_flow']['status'] == 'AWAITING_CONFIRM'


def test_I5_tool_cap(store, verified):
    llm = FakeLLM([{'tool_calls': [{'name': 'get_balance', 'arguments': {}}] * 10}, ''])
    result = Chat(store, llm=llm).turn('balance', session_id=verified['session_id'])
    assert len(result['tools_called']) == get_settings().agent['max_tool_calls']


@pytest.mark.parametrize('rate_limited', [False, True])
def test_I1_I2_fallback_state(store, verified, monkeypatch, rate_limited):
    from app.agent import Runner
    runner = Runner()
    calls = []
    import groq
    import httpx
    failure = groq.RateLimitError('test 429', response=httpx.Response(429, request=httpx.Request('POST', 'https://api.groq.com/openai/v1/chat/completions')), body={}) if rate_limited else TimeoutError()
    fake = FakeLLM([failure, 'Hello'])
    def native(model, messages, registry):
        calls.append(model)
        return fake.chat(messages)
    monkeypatch.setattr(runner, '_native', native)
    result = Chat(store, runner=runner).turn('hi', session_id=verified['session_id'])
    assert result['reply'] == 'Hello' and len(calls) == 2
    def fail(*args):
        raise TimeoutError()
    monkeypatch.setattr(runner, '_native', fail)
    result = Chat(store, runner=runner).turn('hi', session_id=verified['session_id'])
    assert result['reply'] == get_settings().messages['answers']['retry_later']
    assert result['state'] == 'VERIFIED'


def test_I4_keyword_only_quota(store):
    from app.rag import search_knowledge
    from app.llm import QuotaExceeded
    store.put('chunks', {'chunk_id': 'a'}, {'chunk_id': 'a', 'section_id': 'a', 'doc_id': 'a', 'text': 'NEFT charges are zero.', 'namespace': 'bank', 'owner_key': 'bank', 'lifecycle': 'active', 'page': 1, 'version': '1', 'effective_date': '2026-01-01', 'confidence': 1})
    class Exhausted:
        def embed(self, *args, **kwargs):
            raise QuotaExceeded()
    result = search_knowledge('NEFT charges', store=store, embeddings=Exhausted())
    assert result['data']['items'] and not result['data']['no_good_match']


def test_retryable_image_never_marks_ingest_done(store, tmp_path):
    from PIL import Image
    from app.ingest.models import DocumentMeta
    from app.ingest.pipeline import Pipeline
    path = tmp_path / 'pending.png'
    Image.new('RGB', (100, 100), 'white').save(path)
    class Pending:
        def extract(self, *args, **kwargs):
            return {'needs_retry': True}
    result = Pipeline(store, images=Pending()).ingest(path, DocumentMeta(title='retry'), defer_embeddings=True)
    assert result['processing_state'] == 'needs_retry'
    assert store.one('ingest_jobs', {'doc_id': result['doc_id']})['state'] == 'needs_retry'


def test_cutoff_table_keeps_only_validated_rows(store):
    from app.ingest.chunking import rate_records
    from app.ingest.models import Element
    meta = {'doc_id': 'd', 'namespace': 'bank', 'owner_key': 'bank', 'lifecycle': 'active', 'effective_date': '2026-01-01', 'product': 'fixed_deposit'}
    element = Element(type='table', page=1, source_method='vlm', content={'columns': ['Tenure months', 'General rate', 'Senior rate'], 'rows': [['12', '6.85%', '7.35%'], [None, None, None]]}, needs_review=True, flags=['truncated', 'unreadable_cell:1:0'])
    _, rows = rate_records(element, meta)
    assert not rows[0]['needs_review'] and not rows[1]['needs_review']
    assert rows[2]['needs_review'] and rows[3]['needs_review']


def test_postal_pin_stays_local(store, verified):
    Flows(store).set_slots(verified, 'chequebook', pages=40, delivery='normal')
    Sessions(store).save(verified)
    llm = FakeLLM([call('chequebook_set_slots', address={'mode': 'new', 'line1': '123 Main Street', 'city': 'Mumbai', 'state': 'MH'}), ''])
    result = Chat(store, llm=llm).turn('New address 123 Main Street Mumbai MH PIN 411001', session_id=verified['session_id'])
    assert result['flow']['status'] == 'AWAITING_CONFIRM'
    assert result['flow']['slots']['address']['pin'] == '411001'
    assert '411001' not in json.dumps(llm.calls)
    assert '411001' not in json.dumps(store.data['audit_log'], default=str)
    assert '411001' not in json.dumps(store.one('sessions', {'session_id': verified['session_id']})['history'])


def test_session_lease_and_concurrent_submit(store, verified):
    sessions = Sessions(store)
    with sessions.lock(verified['session_id']):
        sessions.load(verified['session_id'])
        with pytest.raises(RuntimeError, match='SESSION_BUSY'):
            with sessions.lock(verified['session_id']):
                pass
    flow = Flows(store)
    flow.set_slots(verified, 'chequebook', pages=40, delivery='normal', address={'mode': 'on_file'})
    sessions.save(verified)
    def confirm():
        try:
            return Chat(store, llm=FakeLLM()).turn('confirm', session_id=verified['session_id'])
        except RuntimeError:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: confirm(), range(2)))
    assert store.count('requests', {'type': 'chequebook'}) == 1


def test_native_optional_schema_and_cache(tmp_path, monkeypatch):
    import httpx
    import app.agent as module
    from app.tools.registry import Search
    class Quota:
        calls = 0
        def reserve(self, kind):
            self.calls += 1
    quota, usage, bodies = Quota(), [], []
    def respond(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={'choices': [{'message': {'content': 'ok'}}], 'usage': {'total_tokens': 10}})
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    transport = module.LoggedTransport(quota, usage, cache=True, pacing=False, schemas={'search_bank_knowledge': Search.model_json_schema()})
    transport.inner = httpx.MockTransport(respond)
    body = {'model': 'fake', 'messages': [{'role': 'user', 'content': 'NEFT charges'}], 'tools': [{'function': {'name': 'search_bank_knowledge', 'parameters': {'required': ['query', 'doc_type']}}}]}
    for _ in range(2):
        transport.handle_request(httpx.Request('POST', 'https://api.groq.com/openai/v1/chat/completions', json=body))
    assert quota.calls == 1 and len(bodies) == 1
    assert bodies[0]['tools'][0]['function']['parameters']['required'] == ['query']
    assert usage[-1]['cached']


def test_search_result_masking_preserves_json(store, verified):
    from app.log import mask_payload
    value = mask_payload({'score': 0.01639344262295082, 'pin': '411001', 'text': 'mobile 9000000001', 'ok': True})
    assert json.loads(json.dumps(value)) == value
    assert value['pin'] == '[REDACTED]' and '9000000001' not in value['text']
    from scripts.run_gate2 import fixture
    db, session = fixture({'setup': {'flow': {'pages': 40}}})
    result = Registry(session, db, embeddings=FakeLLM()).tools['search_bank_knowledge'].invoke({'query': 'NEFT charges', 'product': 'fees_and_charges'})
    assert result['ok'] and '₹0.00' in result['data']['items'][0]['text']


def test_actual_held_queue_and_resend_limit(store):
    llm = FakeLLM([call('get_balance'), '', call('get_statement', period='last_5'), '', call('get_card_info'), ''])
    chat = Chat(store, llm=llm)
    first = chat.turn('my balance')
    sid = first['session_id']
    chat.turn('my statement', session_id=sid)
    chat.turn('my card details', session_id=sid)
    chat.turn('9000000001 000000000001', session_id=sid)
    assert len(store.one('sessions', {'session_id': sid})['auth']['held']) == 3
    result = chat.turn('123456', session_id=sid)
    assert result['tools_called'] == ['get_balance', 'get_statement', 'get_card_info']
    pending = chat.turn('balance 9000000001 000000000001')
    for _ in range(get_settings().auth['otp_max_resends']):
        result = chat.turn('resend', session_id=pending['session_id'])
        assert 'sent' in result['reply']
    assert 'Too many' in chat.turn('resend', session_id=pending['session_id'])['reply']
    assert 'Too many' in chat.turn('123456', session_id=pending['session_id'])['reply']


def test_public_and_personal_while_awaiting(store):
    llm = FakeLLM(['NEFT information is unavailable.'])
    chat = Chat(store, llm=llm)
    result = chat.turn('what are NEFT charges and my balance?')
    assert 'NEFT' in result['reply'] and 'mobile' in result['reply']
    assert store.one('sessions', {'session_id': result['session_id']})['auth']['held']


def test_ambiguous_insert_timeout_reconciles(store, verified, monkeypatch):
    flow = Flows(store)
    flow.set_slots(verified, 'chequebook', pages=40, delivery='normal', address={'mode': 'on_file'})
    original = store.patch
    def committed_then_timeout(name, *args, **kwargs):
        result = original(name, *args, **kwargs)
        if name == 'requests':
            raise TimeoutError()
        return result
    monkeypatch.setattr(store, 'patch', committed_then_timeout)
    result = flow.submit(verified)
    assert result['ok'] and store.count('requests', {'type': 'chequebook'}) == 1


def test_history_is_bounded_before_model(store, verified):
    verified['history'] = [{'role': 'assistant', 'content': 'bank information ' * 1000}]
    Sessions(store).save(verified)
    llm = FakeLLM(['Hello'])
    Chat(store, llm=llm).turn('hi', session_id=verified['session_id'])
    assert 'bank information' not in llm.calls[0]['messages'][1]['content']


def test_secret_only_message_skips_model_and_history(store, verified):
    llm = FakeLLM()
    result = Chat(store, llm=llm).turn('my OTP is 654321 and PIN is 987654', session_id=verified['session_id'])
    assert not llm.calls and not result['tools_called']
    saved = store.one('sessions', {'session_id': verified['session_id']})
    assert '654321' not in json.dumps(saved, default=str) and '987654' not in json.dumps(saved, default=str)


def test_change_button_then_slot_is_code_only(store, verified):
    Flows(store).set_slots(verified, 'chequebook', pages=40, delivery='normal', address={'mode': 'on_file'})
    Sessions(store).save(verified)
    llm = FakeLLM()
    chat = Chat(store, llm=llm)
    changed = chat.turn('Change', session_id=verified['session_id'])
    assert changed['options'] == ['Pages', 'Delivery', 'Address']
    chosen = chat.turn('Pages', session_id=verified['session_id'])
    assert 'pages' not in chosen['flow']['slots']
    summary = chat.turn('60', session_id=verified['session_id'])
    assert summary['flow']['status'] == 'AWAITING_CONFIRM' and summary['flow']['slots']['pages'] == 60
    assert not llm.calls and not store.count('requests', {})


def test_expired_otp_attempts_lock_without_losing_held(store):
    chat = Chat(store, llm=FakeLLM())
    result = chat.turn('balance 9000000001 000000000001')
    sid = result['session_id']
    session = store.one('sessions', {'session_id': sid})
    session['auth']['otp_expires_at'] = utcnow() - timedelta(seconds=1)
    Sessions(store).save(session)
    for _ in range(get_settings().auth['otp_max_attempts']):
        result = chat.turn('123456', session_id=sid)
    assert 'Too many' in result['reply']
    assert store.one('sessions', {'session_id': sid})['auth']['held']


def test_exact_flow_button_labels_skip_model(store, verified):
    Flows(store).set_slots(verified, 'chequebook')
    Sessions(store).save(verified)
    llm = FakeLLM()
    chat = Chat(store, llm=llm)
    for label in ['40 pages', 'Emergency (within 1 week)', 'Address on file']:
        result = chat.turn(label, session_id=verified['session_id'])
    assert result['flow']['status'] == 'AWAITING_CONFIRM'
    assert result['flow']['slots']['pages'] == 40 and not llm.calls


def test_J_number_units_citations_and_repair(store, verified):
    evidence = [('lookup_rate', {'data': {'items': [{'n': 1, 'text': 'Rate 8.50% and amount ₹1000'}]}})]
    assert check('Rate 8.5 [1]', evidence)[0]
    assert check('Rate 8.5【1】', evidence)[0]
    assert not check('Rate 8.5%', evidence)[0]
    assert not check('Amount 1000%', evidence)[0]
    assert not check('Rate 8.5 [2]', evidence)[0]
    assert not check('Rate 9.99%', evidence)[0]
    llm = FakeLLM(['Rate 99.9%', 'Rate 99.9%'])
    reply = Chat(store, llm=llm).turn('rate?', session_id=verified['session_id'])
    assert reply['reply'] == get_settings().messages['answers']['unverified_number']
