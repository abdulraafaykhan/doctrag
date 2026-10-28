"""Structure-aware parent/child chunks and deterministic exact-rate records."""

import math
import re
from decimal import Decimal, InvalidOperation

from app.config import get_settings
from app.ingest.models import digest, utcnow
from app.log import mask_text


def tokens(text: str) -> int:
    return math.ceil(len(text) / get_settings().chunking["chars_per_token"])


def _split(text: str, maximum: int, overlap: int = 0):
    size = max(1, maximum * get_settings().chunking["chars_per_token"])
    start = 0
    while start < len(text):
        end = min(len(text), start + size)
        if end < len(text):
            boundary = text.rfind(" ", start + size // 2, end)
            if boundary > start:
                end = boundary
        yield text[start:end].strip()
        if end == len(text):
            break
        start = max(
            start + 1, end - overlap * get_settings().chunking["chars_per_token"]
        )


def _table_parts(lines: list[str], header: str, maximum: int):
    batch = []
    for line in lines:
        if batch and tokens(header + "\n" + "\n".join([*batch, line])) > maximum:
            yield header + "\n" + "\n".join(batch)
            batch = []
        batch.append(line)
    if batch:
        yield header + "\n" + "\n".join(batch)


def chunk_elements(elements, document: dict):
    cfg = get_settings().chunking
    sections, chunks = [], []
    base = {
        key: document[key]
        for key in (
            "doc_id",
            "namespace",
            "owner_key",
            "lifecycle",
            "doc_type",
            "product",
            "version",
            "effective_date",
        )
    }
    if document.get("expires_at"):
        base["expires_at"] = document["expires_at"]

    def child(text, element, section_id, kind):
        text = mask_text(text)
        chunks.append(
            {
                **base,
                "chunk_id": "chk_"
                + digest(f"{document['doc_id']}|{len(chunks)}|{text}")[:24],
                "section_id": section_id,
                "kind": kind,
                "filename": document.get('filename', document['title']),
                "doc_title": document['title'],
                "created_at": utcnow(),
                "expires_at": document.get('expires_at'),
                "embedding_model": get_settings().models.embed,
                "embedding_dims": get_settings().models.embed_dims,
                "extraction_id": element.extraction_id,
                "text": text,
                "token_count": tokens(text),
                "content_hash": digest(text),
                "section_path": element.section,
                "page": element.page,
                "bbox": element.bbox,
                "source_method": element.source_method if element.source_method in {'ocr', 'vlm'} else 'text_layer',
                "confidence": element.confidence,
                "needs_review": element.needs_review,
                "review_flags": element.flags,
            }
        )

    for element in elements:
        prefix = f"{document['title']} > {element.section or document['title']} > Page {element.page}\n"
        header = ""
        if element.type == "table":
            header = " | ".join(element.content["columns"])
            lines = [
                " | ".join(
                    "[unreadable]" if value is None else str(value) for value in row
                )
                for row in element.content["rows"]
            ]
            body = header + "\n" + "\n".join(lines)
        else:
            body = str(element.content)
        if not body.strip():
            continue
        parent_budget = max(1, cfg["parent_max_tokens"] - tokens(prefix))
        parent_parts = (
            _table_parts(lines, header, parent_budget)
            if header
            else _split(body, parent_budget)
        )
        for parent_body in parent_parts:
            section_id = (
                "sec_"
                + digest(f"{document['doc_id']}|{len(sections)}|{parent_body}")[:24]
            )
            sections.append(
                {
                    **base,
                    "section_id": section_id,
                    "section_path": element.section,
                    "page_start": element.page,
                    "page_end": element.page,
                    "text": prefix + parent_body,
                    "token_count": tokens(prefix + parent_body),
                }
            )
            budget = (
                cfg["table_max_tokens"]
                if element.type == "table"
                else cfg["prose_tokens"][1]
            )
            extra = header + "\n" if header else ""
            child_parts = (
                _table_parts(
                    parent_body.splitlines()[1:],
                    header,
                    max(1, budget - tokens(prefix)),
                )
                if header
                else _split(
                    parent_body,
                    max(1, budget - tokens(prefix + extra)),
                    cfg["prose_tokens"][0] * cfg["overlap_percent"] // 100
                    if not header
                    else 0,
                )
            )
            for part in child_parts:
                child(
                    prefix + part if header else prefix + extra + part,
                    element,
                    section_id,
                    "table"
                    if header
                    else "image_text"
                    if element.source_method in {"ocr", "vlm"}
                    else "prose",
                )
        if element.type == "table":
            for row in element.content["rows"]:
                row_line = " | ".join(
                    "[unreadable]" if value is None else str(value) for value in row
                )
                section_id = next(
                    (
                        section["section_id"]
                        for section in sections
                        if section["page_start"] == element.page
                        and row_line in section["text"]
                    ),
                    sections[-1]["section_id"],
                )
                sentence = " | ".join(
                    f"{column}: {value}"
                    for column, value in zip(
                        element.content["columns"], row, strict=False
                    )
                )
                for part in _split(
                    sentence, max(1, cfg["row_max_tokens"] - tokens(prefix))
                ):
                    child(prefix + part, element, section_id, "table_row")
    return sections, chunks


def rate_records(element, document):
    if element.type != "table":
        return [], []
    columns = element.content["columns"]
    keys = [re.sub(r"\W+", "_", column.lower()).strip("_") for column in columns]
    row_reviews = [element.needs_review] * len(element.content["rows"])
    if element.needs_review and element.flags and all(flag == "truncated" or flag.startswith("unreadable_cell:") for flag in element.flags):
        # Independently OCR-validated complete rows stay usable when only cut-off rows are null.
        row_reviews = [any(value is None for value in row) for row in element.content["rows"]]
    if "general_rate" in keys and "senior_rate" in keys:
        row_reviews = [review for review in row_reviews for _ in range(2)]
        expanded = []
        for values in element.content["rows"]:
            raw = dict(zip(keys, values, strict=False))
            for segment, key in (
                ("general", "general_rate"),
                ("senior_citizen", "senior_rate"),
            ):
                expanded.append(
                    [
                        document["product"],
                        raw.get("tenure_months"),
                        segment,
                        raw.get(key),
                    ]
                )
        element = element.model_copy(
            update={
                "content": {
                    "columns": ["product", "tenure_months", "segment", "rate_percent"],
                    "rows": expanded,
                }
            }
        )
        columns = element.content["columns"]
        keys = columns
    if not any(
        "rate" in key or "fee" in key or "charge" in key or key == "amount_paise"
        for key in keys
    ):
        return [], []
    table_id = (
        "tbl_" + digest(f"{document['doc_id']}|{element.page}|{element.content}")[:24]
    )
    base = {
        key: document[key]
        for key in (
            "doc_id",
            "namespace",
            "owner_key",
            "lifecycle",
            "effective_date",
            "product",
        )
    }
    if document.get("expires_at"):
        base["expires_at"] = document["expires_at"]
    records = []
    for index, values in enumerate(
        element.content["rows"][: get_settings().chunking["rate_rows_cap"]]
    ):
        raw = dict(zip(keys, values, strict=False))
        dims, numeric = {}, {}
        product = str(raw.get("product") or document["product"])
        for key, value in raw.items():
            if value is None:
                continue
            if key in {"tenure_months", "amount_min_rupees", "amount_max_rupees"}:
                try:
                    dims[key] = int(Decimal(str(value)))
                except InvalidOperation:
                    continue
            elif key == "segment":
                dims[key] = str(value).lower().replace(" ", "_")
            elif key in get_settings().knowledge["retrieval"]["rate_keys"]:
                try:
                    number = Decimal(str(value).strip("%* "))
                    numeric[key] = int(number) if key == "amount_paise" else str(number)
                except InvalidOperation:
                    continue
        records.append(
            {
                **base,
                "row_id": f"{table_id}_{index}",
                "table_id": table_id,
                "product": product,
                "dims": dims,
                "values": numeric,
                "raw": raw,
                "row_text": " | ".join(f"{k}: {v}" for k, v in raw.items()),
                "source": {"doc_id": document["doc_id"], "page": element.page},
                "confidence": element.confidence,
                "needs_review": row_reviews[index]
                or any(value is None for value in values),
            }
        )
    tables = [
        {
            **base,
            "table_id": table_id,
            "name": element.section,
            "columns": [
                {"key": key, "label": label, "type": "text", "unit": ""}
                for key, label in zip(keys, columns, strict=False)
            ],
            "row_count": len(records),
            "source": {"page": element.page},
            "source_method": element.source_method,
            "confidence": element.confidence,
        }
    ]
    return tables, records
