"""Plain currency-copy check over authoritative tool results."""
import re

_NUMBER = r"[+-]?\d+(?:,\d+)*(?:\.\d+)?(?:[eE][+-]?\d+)?(?:[kKmMbB]|[ \t]+(?i:thousand|million|billion|crore|lakh))?"
_CODE = r"(?:[A-Z]{3}|(?i:INR|USD|EUR|GBP|JPY|CAD|AUD|CHF|CNY|SGD|AED|Rs\.?))"
_AMOUNT = re.compile(
    rf"(?<!\w)(?:(?:[+-]\s*)?{_CODE}\s*{_NUMBER}|{_NUMBER}\s*{_CODE})(?!\w)"
    rf"|(?:[+-]\s*)?[₹$€£¥]\s*{_NUMBER}(?!\w)"
    r"|(?<![\w.,])[+-]?\d+(?:,\d+)*\.\d+(?!\w)"
)
_DECIMAL = re.compile(r"(?<![\w.,])[+-]?\d+(?:,\d+)*\.\d+(?!\w)")


def valid_account_amounts(answer: str, data: dict, sources: list[dict] | None = None) -> bool:
    texts = [row[key] for rows in data.values() for row in rows
             for key in ("balance", "amount") if isinstance(row.get(key), str)]
    texts.extend(source["text"] for source in sources or [])
    allowed = {match.group() for text in texts for match in _AMOUNT.finditer(text)}
    allowed.update(match.group() for text in texts for match in _DECIMAL.finditer(text))
    return all(match.group() in allowed for match in _AMOUNT.finditer(answer))
