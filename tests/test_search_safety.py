"""Offline search/masking regressions; added without executing test files."""
import json
from types import SimpleNamespace
from app.config import get_settings
from app.local_llm import LocalClient
from app.log import mask_payload, mask_text
from app.rag import round_search_values
from app.tools.registry import Registry


def test_search_decimal_survives_and_identities_are_masked():
    score = 0.01639344262295082
    result = {'data': {'items': [{'score': score, 'confidence': .9876543210987654, 'text': 'Mobile 9876543210 account 123456789012 card 4111 1111 1111 1111'}]}}
    row = mask_payload(round_search_values(result))['data']['items'][0]
    assert row['score'] == .016 and isinstance(row['score'], float)
    assert row['confidence'] == .988
    assert row['text'] == 'Mobile 98****10 account ****9012 card [CARD]'
    assert mask_text(str(score)) == str(score)
    assert mask_text('0.9876543210 123456789012.5 0.4111111111111111') == '0.9876543210 123456789012.5 0.4111111111111111'
    assert mask_text('Screenshot 20261028164700123.png') == 'Screenshot 20261028164700123.png'
    registry = SimpleNamespace(list=lambda: [])
    # A corrupted legacy score is ignored for ranking, never passed to float().
    corrupted = {'data': {'items': [{'score': '0.[CARD]', 'text': 'Evidence'}]}}
    LocalClient(registry).prompt([{'role': 'tool', 'content': json.dumps(corrupted)}])


def test_upload_reference_hides_bank_search_for_this_turn(monkeypatch):
    monkeypatch.setattr(get_settings().models, 'agent', 'local/default')
    store = SimpleNamespace(one=lambda *args, **kwargs: None)
    session = {'session_id': 'owned', 'auth': {'state': 'ANONYMOUS'}, 'active_flow': None, 'attachments': [{'id': 'screenshot'}]}
    private = Registry(session, store, customer_message='Who is in the above image?')
    assert 'search_my_documents' in private.visible() and 'search_bank_knowledge' not in private.visible()
    normal = Registry(session, store, customer_message='What are bank fees?')
    assert 'search_bank_knowledge' in normal.visible()
    current = Registry({**session, 'attachments': []}, store, customer_message='Explain please', attachment_ids=['screenshot'])
    assert 'search_bank_knowledge' not in current.visible()


def test_unrelated_search_query_replaced_by_customer_words(monkeypatch):
    monkeypatch.setattr(get_settings().models, 'agent', 'local/default')
    message = 'who is the beneficiary in the above image?'
    session = {'session_id': 'owned', 'auth': {'state': 'ANONYMOUS'}, 'active_flow': None, 'attachments': [{'id': 'screenshot'}]}
    registry = Registry(session, SimpleNamespace(one=lambda *args, **kwargs: None), customer_message=message)
    client = LocalClient(registry)
    parsed = client.parse(json.dumps({'tool_calls': [{'name': 'search_my_documents', 'arguments': {'query': 'fixed deposit interest rates'}}]}))
    assert parsed['tool_calls'][0]['arguments']['query'] == message
    matched = client.parse(json.dumps({'tool_calls': [{'name': 'search_my_documents', 'arguments': {'query': 'beneficiary name'}}]}))
    assert matched['tool_calls'][0]['arguments']['query'] == 'beneficiary name'
