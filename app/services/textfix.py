"""Clean up characters that break Markdown and make answers look messy.

gpt-oss style models like to emit typographic look-alikes: non-breaking hyphens (U+2011) in table separator rows
and horizontal rules ("|‑‑‑|"), narrow no-break spaces (U+202F) inside numbers, and zero-width characters.
Markdown only understands the plain ASCII versions, so tables and rules show up as raw text. Every character
handled here is a single code point, so it is safe to apply to each streamed token.
"""

_MAP = {}
for _c in "‐‑‒⁃−":      # hyphen look-alikes (and the minus sign) -> '-'
    _MAP[ord(_c)] = "-"
for _c in "        ":  # odd spaces -> ' '
    _MAP[ord(_c)] = " "
for _c in "​‌‍⁠﻿":       # zero-width characters -> removed
    _MAP[ord(_c)] = None


def normalize(text: str) -> str:
    return text.translate(_MAP)