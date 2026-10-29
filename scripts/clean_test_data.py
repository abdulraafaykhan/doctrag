"""Preview conversation/upload rows; delete only with an explicit --yes."""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import close_db, get_db


# Only these collections and predicates are allowed; fixtures, quotas and bank content stay intact.
TARGETS = (
    ('sessions', {}),
    ('pending_actions', {}),
    ('audit_log', {}),
    ('requests', {}),
    *((name, {'namespace': 'user'}) for name in (
        'documents', 'sections', 'chunks', 'rate_tables', 'rate_rows', 'extractions'
    )),
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--yes', action='store_true', help='Delete the listed rows, including all conversation/service requests and audit events.')
    args = parser.parse_args()
    database = get_db()
    print('DELETE ENABLED' if args.yes else 'DRY RUN: counts only; no database or file changes')
    total = 0
    for name, query in TARGETS:
        count = database[name].count_documents(query)
        total += count
        suffix = f'; deleted={database[name].delete_many(query).deleted_count}' if args.yes else ''
        print(f'{name:16} {count:8}  {query or "all rows"}{suffix}')
    print(f'TOTAL: {total}')
    print('Preserved: users, accounts, cards, transactions, quota_usage, bank namespace, all files.')


if __name__ == '__main__':
    try:
        main()
    finally:
        close_db()
