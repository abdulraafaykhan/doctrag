"""Read-only database views; deletion requires the explicit drop-unknown --yes command."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rich.console import Console
from rich.table import Table
from app.db import get_db, close_db
from app.ingest.store import Store

PURPOSES = {
    'users': 'Customer identities', 'accounts': 'Account balances and status',
    'cards': 'Cards, status and last four digits', 'transactions': 'Account ledger',
    'requests': 'Submitted customer service requests', 'sessions': 'Auth, history, attachments and token totals',
    'pending_actions': 'Confirmation and idempotency tokens', 'audit_log': 'Masked events without message text',
    'documents': 'Source metadata and resumable job', 'sections': 'Parent retrieval text',
    'chunks': 'Search text, provenance and reusable embedding vectors',
    'rate_tables': 'Rate table metadata', 'rate_rows': 'Exact rate lookup rows',
    'extractions': 'OCR, VLM, comparison and selected source per crop', 'quota_usage': 'Daily provider request reservations',
}


def table(console, title, columns, rows):
    output = Table(title=title, show_lines=True)
    for column in columns:
        output.add_column(column, overflow='fold')
    for row in rows:
        output.add_row(*(str(value) for value in row))
    console.print(output)


def matching_docs(store, value):
    return [row for row in store.find('documents', {}) if value.casefold() in {str(row.get(key, '')).casefold() for key in ('doc_id', 'filename', 'title')}]


def run(argv=None, *, store=None, console=None, database=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['summary', 'session', 'docs', 'chunks', 'extraction', 'drop-unknown'])
    parser.add_argument('value', nargs='?')
    parser.add_argument('--yes', action='store_true')
    args = parser.parse_args(argv)
    store, console = store or Store(), console or Console(highlight=False)
    if args.command in {'summary', 'drop-unknown'}:
        database = database if database is not None else get_db()
        names = database.list_collection_names()
        if args.command == 'summary':
            table(console, 'Collections', ['Collection', 'Rows', 'Purpose'], [(name, store.count(name, {}), PURPOSES.get(name, 'UNKNOWN')) for name in sorted(set(names) | set(PURPOSES))])
        else:
            unknown = sorted(set(names) - set(PURPOSES))
            table(console, 'Unknown collections', ['Collection', 'Rows', 'Action'], [(name, store.count(name, {}), 'delete' if args.yes else 'keep (use --yes to delete)') for name in unknown])
            if args.yes:
                for name in unknown:
                    database[name].drop()
        return
    if args.command == 'session':
        rows = store.find('sessions', {'session_id': args.value} if args.value else {})
        for row in rows:
            safe = {key: value for key, value in row.items() if key != '_id'}
            safe['auth'] = {key: value for key, value in row.get('auth', {}).items() if key not in {'otp_hash', 'candidates'}}
            console.print(json.dumps(safe, ensure_ascii=False, default=str, indent=2), markup=False)
        return
    documents = matching_docs(store, args.value) if args.value else store.find('documents', {})
    if args.command == 'docs':
        table(console, 'Documents', ['Name / ID', 'State / stage', 'Pages', 'Chunks', 'Flags'], [(row.get('filename', row['doc_id']), f"{row.get('job', {}).get('state', row.get('processing_state'))} / {row.get('job', {}).get('stage', '')}", row.get('pages_processed', 0), store.count('chunks', {'doc_id': row['doc_id']}), row.get('review_flags', [])) for row in documents])
        return
    for document in documents:
        query = {'doc_id': document['doc_id']}
        if args.command == 'chunks':
            table(console, document.get('filename', document['doc_id']), ['Page', 'Kind', 'Source', 'Text (200 chars)', 'Review', 'Embedding', 'Dims'], [(row.get('page'), row.get('kind'), row.get('source_method'), row.get('text', '')[:200], row.get('needs_review'), bool(row.get('embedding')), row.get('embedding_dims', len(row.get('embedding') or []))) for row in store.find('chunks', query)])
        else:
            for row in store.find('extractions', query):
                console.print(f"{document.get('filename', document['doc_id'])} | page={row.get('page')} | {row.get('extraction_id')}", markup=False)
                console.print('OCR: ' + row.get('ocr', {}).get('text', ''), markup=False)
                console.print('VLM: ' + row.get('vlm', {}).get('text', ''), markup=False)
                table(console, 'Comparison', ['Field', 'OCR', 'VLM', 'Match', 'Selected'], [(field['path'], field.get('ocr_value'), field.get('vlm_value'), field.get('match'), field.get('selected')) for field in row.get('compare', {}).get('fields', [])])
                console.print(f"selected_source={row.get('selected_source')} | {row.get('selected_reason')}", markup=False)


if __name__ == '__main__':
    try:
        run()
    finally:
        close_db()
