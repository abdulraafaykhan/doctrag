"""Conservative JSON and OCR checks for a single vision crop."""

import copy
import json
import re
from typing import Any

from app.log import log, mask_text

_NUMBER = re.compile(r"(?<!\w)[+-]?[0-9Ol]+(?:[.,][0-9Ol]+)*%?(?!\w)")
_CUT = re.compile(r"cut.?off|truncat|clipp", re.IGNORECASE)


def normalize_numeric_tokens(text: str) -> str:
    def replace(match: re.Match) -> str:
        token = match.group()
        # Confusable letters in prose are never treated as digits.
        if any(char.isdigit() for char in token) or "." in token or "," in token:
            return token.translate(str.maketrans({"O": "0", "l": "1"}))
        return token

    return _NUMBER.sub(replace, text)


def parse_vision_json(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    value = json.loads(cleaned)
    if not isinstance(value, dict):
        raise ValueError("Vision output must be a JSON object")
    return value


def render_vision(value):
    """Readable retrieval text, retaining explicit nulls rather than JSON syntax."""
    if isinstance(value, dict) and isinstance(value.get('rows'), list):
        columns = value.get('columns', [])
        lines = [' | '.join(str(column) for column in columns), ' | '.join('---' for _ in columns)]
        lines.extend(' | '.join('[unreadable]' if cell is None else str(cell) for cell in row) for row in value['rows'])
        return '\n'.join(lines)
    lines = []
    def visit(item, label=''):
        if isinstance(item, dict):
            if 'value' in item:
                visit(item['value'], label)
            else:
                for key, child in item.items():
                    if key not in {'confidence', 'overall_confidence', 'uncertain'}:
                        visit(child, f'{label} / {key}' if label else key)
        elif isinstance(item, list):
            for index, child in enumerate(item):
                visit(child, f'{label} {index + 1}')
        else:
            lines.append(f"{label}: {'[unreadable]' if item is None else item}")
    visit(value)
    return '\n'.join(lines)


def _words(text: str) -> set[str]:
    return set(re.findall(r"\w+", normalize_numeric_tokens(text).casefold()))


def postprocess_vision(
    value: dict[str, Any],
    local_ocr: str,
    *,
    cut_off_rows: tuple[int, ...] = (),
) -> dict[str, Any]:
    """Crop geometry uses zero-based row indices; OCR must belong to this crop."""
    result = copy.deepcopy(value)
    ocr_words = _words(local_ocr)
    ocr_numbers = set(_NUMBER.findall(normalize_numeric_tokens(local_ocr)))

    def supported_note(note: Any) -> bool:
        words = _words(note) if isinstance(note, str) else set()
        return bool(words) and words <= ocr_words

    def number(value: Any) -> Any:
        if not isinstance(value, str):
            return value
        normalized = normalize_numeric_tokens(value)
        # Only wholly numeric fields are compared; labels and prose remain verbatim.
        stripped = normalized.strip().strip("₹$€£").strip().rstrip("*")
        if not _NUMBER.fullmatch(stripped) or not (
            any(char.isdigit() for char in stripped)
            or "." in stripped
            or "," in stripped
        ):
            return value
        if local_ocr and stripped not in ocr_numbers:
            log.step("VERIFY", "vision numeric field absent from crop OCR", why='the number is not present in this crop; leave it null')
            return None
        return normalized

    def visit(item: Any) -> Any:
        if isinstance(item, list):
            return [visit(child) for child in item]
        if not isinstance(item, dict):
            return number(item)
        for key in list(item):
            if key in {"notes", "footnotes", "footnote"}:
                notes = item[key] if isinstance(item[key], list) else [item[key]]
                kept = [note for note in notes if supported_note(note)]
                if len(kept) != len(notes):
                    log.step(
                        "VERIFY",
                        "dropped vision notes absent from crop OCR",
                        count=len(notes) - len(kept),
                        why='note contains words absent from this crop OCR',
                    )
                item[key] = (
                    kept if isinstance(item[key], list) else (kept[0] if kept else None)
                )
            elif key not in {"overall_confidence", "confidence", "uncertain"}:
                item[key] = visit(item[key])
        rows = item.get("rows")
        if isinstance(rows, list):
            cut = set(cut_off_rows)
            for uncertain in item.get("uncertain", []):
                if isinstance(uncertain, dict) and _CUT.search(
                    str(uncertain.get("reason", ""))
                ):
                    index = uncertain.get("row")
                    if type(index) is int and index >= 0:
                        cut.add(index)
            if item.get("truncated") is True and rows and not cut:
                cut.add(len(rows) - 1)
            for index in sorted(cut):
                columns = len(item.get("columns", [])) or (len(rows[0]) if rows else 0)
                # Geometry may identify the omitted final row; never reconstruct its text.
                if index == len(rows):
                    rows.append([None] * columns)
                if 0 <= index < len(rows) and isinstance(rows[index], list):
                    rows[index] = [None] * len(rows[index])
                    item["truncated"] = True
                    log.step("VERIFY", "nulled cut-off vision row", row=index, why='crop geometry or model indicates truncation')
        return item

    def mask(item: Any) -> Any:
        if isinstance(item, dict):
            return {key: mask(child) for key, child in item.items()}
        if isinstance(item, list):
            return [mask(child) for child in item]
        if isinstance(item, (str, int, float)) and not isinstance(item, bool):
            masked = mask_text(str(item))
            return masked if masked != str(item) else item
        return item

    return mask(visit(result))
