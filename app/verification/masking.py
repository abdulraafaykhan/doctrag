"""One privacy boundary for user text, private files, history and diagnostics."""

import re

# Longest identifiers win; the optional country prefix identifies a mobile first.
_IDENTIFIER = re.compile(
    r"(?<![\d])(?P<prefixed>\+?91[ -]?[6-9](?:[ -]?\d){9})(?![\d])"
    r"|(?<![\d])(?P<account>(?:\d[ -]?){11}\d)(?![\d])"
    r"|(?<![\d])(?P<mobile>[6-9](?:[ -]?\d){9})(?![\d])"
)
_OTP = re.compile(r"(?<!\d)\d{6}(?!\d)")


def last_four(value: str) -> str:
    return "****" + re.sub(r"\D", "", value)[-4:]


def mask_sensitive(text: str, *, awaiting_otp: bool = False) -> str:
    """Parse verification input first; call this before storing or prompting."""

    def replace(match: re.Match) -> str:
        label = "Account" if match.lastgroup == "account" else "Mobile"
        return label + " ending " + re.sub(r"\D", "", match[0])[-4:]

    text = _IDENTIFIER.sub(replace, text)
    # Six digits are also ordinary pincodes, references and amounts outside the OTP step.
    return _OTP.sub("OTP entered", text) if awaiting_otp else text
