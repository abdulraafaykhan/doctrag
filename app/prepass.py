"""Extract identity locally and remove credentials before persistence or model use."""

import re
from dataclasses import dataclass
from app.log import mask_text

MOBILE = re.compile(r"(?<!\d)(?:\+91[ -]*)?([6-9](?:[ -]*\d){9})(?!\d)")
ACCOUNT = re.compile(r"(?<!\d)(\d(?:[ -]*\d){11})(?!\d)")
CODE = re.compile(r"(?<!\d)\d{6}(?!\d)")
SECRET = re.compile(r"(?i)\b(?:otp|pin|cvv|password)\s*(?:is|:|=)?\s*\S+")


@dataclass
class Parsed:
    text: str
    mobile: str | None
    account: str | None
    code: str | None
    command: str
    postal_pin: str | None = None


def extract(text: str) -> Parsed:
    mobile_match = MOBILE.search(text)
    mobile = re.sub(r"\D", "", mobile_match[1]) if mobile_match else None
    without_mobile = MOBILE.sub("[mobile]", text)
    account_match = ACCOUNT.search(without_mobile)
    account = re.sub(r"\D", "", account_match[1]) if account_match else None
    remaining = ACCOUNT.sub("[account]", without_mobile)
    code = text.strip() if re.fullmatch(r"\d{6}", text.strip()) else None
    safe = SECRET.sub("[secret]", remaining)
    safe = CODE.sub("[secret]", safe)
    pin = re.search(r'(?i)\bpin\s*(?:is|:|=)?\s*(\d+)', text)
    return Parsed(mask_text(safe), mobile, account, code, text.strip().lower(), pin[1] if pin else None)
