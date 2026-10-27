"""Resumable, input-hashed Groq behavior evaluation with independent repetitions."""

import argparse
import json
import re
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent import Runner
from app.chat import Chat
from app.config import ROOT, get_settings
from app.db import close_db
from app.flows import Flows
from app.ingest.embeddings import Embeddings
from app.ingest.models import utcnow
from app.ingest.models import digest
from app.llm import FakeLLM, QuotaGuard, pacific_day
from app.session import Sessions
from app.tools.registry import Registry
from tests.memory_store import MemoryStore
import groq


def fixture(case):
    store = MemoryStore()
    for name in ['users', 'accounts', 'cards', 'transactions']:
        store.data[name] = json.loads((ROOT / 'seed' / f'{name}.json').read_text())
    text = (ROOT / 'data/bank_docs/digital_faq.md').read_text(encoding='utf-8')
    from openpyxl import load_workbook
    from app.money import format_money
    workbook = load_workbook(ROOT / 'data/bank_docs/fees.xlsx', read_only=True, data_only=True)
    text += '\n' + '\n'.join(f'{service}, {segment}: {format_money(amount)}' for service, segment, amount in list(workbook.active.values)[2:] if isinstance(amount, int))
    workbook.close()
    store.put('documents', {'doc_id': 'faq'}, {'doc_id': 'faq', 'title': 'Digital banking FAQ'})
    store.put('chunks', {'chunk_id': 'neft'}, {'chunk_id': 'neft', 'section_id': 'neft', 'doc_id': 'faq', 'namespace': 'bank', 'owner_key': 'bank', 'lifecycle': 'active', 'doc_type': 'faq', 'product': 'fees_and_charges', 'text': text, 'page': 1, 'version': '1', 'effective_date': '2026-10-01', 'confidence': 1.0, 'embedding': [1.0] + [0.0] * (get_settings().models.embed_dims - 1)})
    session = Sessions(store).load()
    setup = case.get('setup', {})
    if setup.get('auth', 'VERIFIED') == 'VERIFIED':
        session['auth'].update(state='VERIFIED', user_id='usr_001', account_id='acc_001')
    if setup.get('auth') == 'AWAITING_OTP':
        session['auth']['candidates'] = {'mobile': '9000000001', 'account': '000000000001'}
        Sessions(store).start(session)
    if 'flow' in setup:
        Flows(store).set_slots(session, 'chequebook', **setup['flow'])
    if setup.get('expired_flow'):
        session['active_flow']['updated_at'] = utcnow() - timedelta(minutes=get_settings().flow['idle_minutes'] + 1)
    Sessions(store).save(session)
    return store, session


def evaluate(case, model, repetition):
    store, session = fixture(case)
    runner = Runner(model=model, cache=True, sample=f'gate2-run-{repetition}')
    # Fake embeddings exercise retrieval routing without spending embedding quota.
    embeddings = FakeLLM()
    chat = Chat(store, runner=runner, embeddings=embeddings)
    turns = []
    for text in case['turns']:
        before = len(runner.usage)
        result = chat.turn(text, session_id=session['session_id'])
        result['usage'] = runner.usage[before:]
        turns.append({key: value for key, value in result.items() if key not in {'session_id', 'attachments'}})
    last = turns[-1]
    expect, errors = case['expect'], []
    tools = [name for turn in turns for name in turn.get('tools_called', [])]
    for name in expect.get('tools_called', []):
        if name not in tools:
            errors.append('missing tool ' + name)
    for name in expect.get('tools_not_called', []):
        if name in tools:
            errors.append('forbidden tool ' + name)
    for value in expect.get('reply_contains', []):
        if value.lower() not in last['reply'].lower():
            errors.append('missing reply ' + value)
    for value in expect.get('reply_not_contains', []):
        if value.lower() in last['reply'].lower():
            errors.append('forbidden reply ' + value)
    if 'state_after' in expect and last['state'] != expect['state_after']:
        errors.append('auth state')
    if 'requests' in expect and store.count('requests', {'type': 'chequebook'}) != expect['requests']:
        errors.append('request count')
    if expect.get('pages') and (not last.get('flow') or last['flow']['slots'].get('pages') != expect['pages']):
        errors.append('pages')
    if expect.get('flow_status') and (not last.get('flow') or last['flow']['status'] != expect['flow_status']):
        errors.append('flow status')
    if expect.get('no_tools') and tools:
        errors.append('unexpected tools')
    if case['id'] != 'G9' and any(turn['reply'].startswith(get_settings().messages['answers']['retry_later']) for turn in turns):
        errors.append('provider or agent failure')
    if case['id'] in {'B6', 'H6'} and not last.get('citations'):
        errors.append('FAQ answer lacks evidence')
    if case['id'] == 'H9' and last.get('flow') and last['flow']['slots'].get('pages', 40) != 40:
        errors.append('typo guessed wrong page count')
    # Every live prompt passes a second, independent PII scan at the transport boundary.
    audits = [row for row in store.find('audit_log', {}) if 'tool' in row]
    proposals = [proposal for usage in runner.usage for proposal in usage.get('tool_proposals', [])]
    schema_failures = [proposal for proposal in proposals if not proposal['schema_valid'] and case['id'] != 'G9']
    if schema_failures:
        errors.append('invalid tool arguments')
    return {'id': case['id'], 'group': case['group'], 'model': model, 'repetition': repetition, 'passed': not errors, 'errors': errors, 'turns': turns, 'tool_calls': len(proposals), 'executed_tool_calls': len(audits), 'valid_tool_calls': sum(proposal['schema_valid'] for proposal in proposals), 'usage': runner.usage}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--smoke', action='store_true')
    parser.add_argument('--model')
    parser.add_argument('--release', action='store_true')
    parser.add_argument('--case')
    args = parser.parse_args()
    cfg = get_settings()
    models = cfg.conversation['agent']['bakeoff_models']
    with groq.Groq(api_key=cfg.secrets.groq_api_key.get_secret_value(), max_retries=0, timeout=cfg.llm.timeout_seconds) as sdk:
        from app.log import log
        with log.span('LLM', 'list Groq models; no generation quota'):
            available = {model.id for model in sdk.models.list().data}
    missing = [model for model in models if model.removeprefix('groq/') not in available]
    if missing:
        raise RuntimeError('Configured bake-off models unavailable: ' + str(missing))
    cases = [json.loads(line) for line in (ROOT / 'eval/cases.jsonl').read_text(encoding='utf-8').splitlines() if line.strip()]
    target = ROOT / 'eval/gate2_live.json'
    report = json.loads(target.read_text()) if target.exists() else {'results': [], 'limits_source': 'https://console.groq.com/docs/rate-limits', 'published_tpm': cfg.conversation['agent']['free_tpm']}
    queue = [(model, case, 1) for model in models for case in cases if case['group'] in {'B', 'H'}]
    if args.model:
        queue = [(model, case, repeat) for model, case, repeat in queue if model == args.model]
    if args.release:
        chosen = args.model or cfg.models.agent
        queue = [(chosen, case, repeat) for repeat in range(1, 4) for case in cases if case['group'] in {'B', 'G'}] + [(chosen, case, 1) for case in cases if case['group'] == 'H']
    if args.case:
        queue = [(model, case, repeat) for model, case, repeat in queue if case['id'] == args.case]
    if args.smoke:
        queue = [(models[0], next(case for case in cases if case['id'] == 'B2'), 1)]
    for model, case, repeat in queue:
        fingerprint = digest(''.join(path.read_text(encoding='utf-8') for path in sorted((ROOT / 'app').rglob('*.py'))))
        key = digest(json.dumps([case, model, repeat, cfg.model_dump(), fingerprint, utcnow().date().isoformat()], sort_keys=True, default=str))
        cached = ROOT / 'data/test_cache' / f'gate2_case_{key}.json'
        if cached.exists():
            result = json.loads(cached.read_text(encoding='utf-8'))
            result['case_cache_hit'] = True
        else:
            result = evaluate(case, model, repeat)
            if result['passed']:
                cached.write_text(json.dumps(result, default=str), encoding='utf-8')
        report['results'] = [row for row in report['results'] if (row['model'], row['id'], row['repetition']) != (model, case['id'], repeat)] + [result]
        target.write_text(json.dumps(report, indent=2, default=str), encoding='utf-8')
        print(json.dumps({key: result[key] for key in ['model', 'id', 'repetition', 'passed', 'errors', 'tool_calls']}) + f" tokens={sum(row.get('total_tokens', 0) for row in result['usage'])}", flush=True)
        if args.smoke:
            print(json.dumps(result, default=str, indent=2), flush=True)
    if not args.smoke:
        print('Bake-off results saved; evaluate release repetitions after choosing the passing model.', flush=True)


if __name__ == '__main__':
    try:
        main()
    finally:
        close_db()
