"""Validate numbers and citations against tool evidence before showing a reply."""

import json
import re
from decimal import Decimal, InvalidOperation
from app.config import get_settings

NUMBER = re.compile(r'(?P<rupee>(?:₹|INR\s*|Rs\.?\s*)\s*)?(?P<number>\d[\d,]*(?:\.\d+)?)(?P<percent>\s*(?:%|percent\b|per\s+cent\b))?', re.I)


def canonical_citations(text):
    return re.sub(r'【(\d+)】', r'[\1]', text)


def numbers(text):
    result = []
    for match in NUMBER.finditer(text):
        try:
            value = Decimal(match['number'].replace(',', ''))
        except InvalidOperation:
            continue
        unit = 'rupee' if match['rupee'] else 'percent' if match['percent'] else None
        result.append((value, unit))
    return result


def check(reply, results):
    reply = canonical_citations(reply)
    evidence = json.dumps([value for _, value in results], ensure_ascii=False, default=str)
    allowed = numbers(evidence)
    for _, result in results:
        for key, value in result.get('data', {}).get('rate', {}).items():
            if key.endswith('_percent'):
                allowed.extend((number, 'percent') for number, _ in numbers(str(value)))
    # A plain numeric spelling can match a percentage, but explicit units cannot contradict evidence.
    bad = [(str(value), unit) for value, unit in numbers(reply) if not any(value == known and (unit is None or unit == known_unit) for known, known_unit in allowed)]
    sources = {str(item['n']) for _, result in results for item in result.get('data', {}).get('items', []) if 'n' in item}
    citations = re.findall(r'\[(\d+)\]', reply)
    missing = bool(sources) and not citations and reply not in get_settings().messages['answers'].values()
    return not bad and not missing and all(citation in sources for citation in citations), {'numbers': bad, 'citations': [value for value in citations if value not in sources], 'missing_citation': missing}
