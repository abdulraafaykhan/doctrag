"""Cached OCR triage and conservative crop-level dual extraction."""

import io
import json
import re
from datetime import timedelta
from functools import lru_cache
from threading import Lock

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from app.config import ROOT, get_settings
from app.ingest.models import digest, utcnow
from app.llm import LLMClient, QuotaExceeded
from app.log import log, mask_text
from app.vision import normalize_numeric_tokens, postprocess_vision

_ocr_lock = Lock()


@lru_cache(maxsize=1)
def ocr_engine():
    from rapidocr_onnxruntime import RapidOCR

    with log.span("OCR", "initialize RapidOCR"):
        return RapidOCR()


def run_ocr(image: Image.Image):
    with log.span("OCR", "RapidOCR", width=image.width, height=image.height):
        with _ocr_lock:
            blocks, _ = ocr_engine()(np.asarray(image.convert("RGB")))
    return blocks or []


def ocr_lines(blocks):
    groups = []
    for block in sorted(
        blocks, key=lambda item: sum(point[1] for point in item[0]) / 4
    ):
        box = block[0]
        center = sum(point[1] for point in box) / 4
        height = max(point[1] for point in box) - min(point[1] for point in box)
        if groups and abs(groups[-1][0] - center) <= max(height / 2, 1):
            groups[-1][1].append(block)
        else:
            groups.append((center, [block]))
    return [
        [
            item
            for item in sorted(
                group, key=lambda block: min(point[0] for point in block[0])
            )
        ]
        for _, group in groups
    ]


def _header(lines):
    keywords = get_settings().knowledge["images"]["table_keywords"]
    scores = [
        sum(word in " ".join(block[1] for block in row).lower() for word in keywords)
        for row in lines
    ]
    return max(range(len(scores)), key=scores.__getitem__) if scores else 0


def classify(blocks, width, height):
    cfg = get_settings().knowledge["images"]
    text = " ".join(block[1] for block in blocks)
    confidence = sum(float(block[2]) for block in blocks) / len(blocks) if blocks else 0
    lower = text.lower()
    if sum(word in lower for word in cfg["table_keywords"]) >= 2:
        kind = "table"
    elif any(word in lower for word in cfg["form_keywords"]):
        kind = "form"
    elif any(word in lower for word in cfg["chart_keywords"]):
        kind = "chart"
    elif confidence < get_settings().ocr["min_confidence"]:
        kind = "handwriting" if blocks else "mixed"
    else:
        kind = "paragraph"
    critical = (
        "%" in text
        or "₹" in text
        or any(word in lower for word in cfg["numeric_keywords"])
    )
    density = len(text.split()) / max(1, width * height)
    return kind, critical, confidence, density


def _numbers(text):
    return re.findall(r"\d+(?:[.,]\d+)*", normalize_numeric_tokens(str(text)))


def validate_table(structured, blocks):
    """Compare corresponding OCR cells rather than accepting a number found in another row."""
    flags = []
    if not isinstance(structured, dict) or not isinstance(structured.get("rows"), list):
        return ["missing_table_structure"]
    lines = ocr_lines(blocks)
    header_index = _header(lines)
    header = lines[header_index] if lines else []
    columns = structured.get("columns", [])
    centers = [sum(point[0] for point in block[0]) / 4 for block in header]
    body = lines[header_index + 1 :]
    cfg = get_settings().knowledge["images"]
    previous = None
    for index, row in enumerate(structured["rows"]):
        ocr_cells = [[] for _ in columns]
        if index < len(body) and len(centers) == len(columns):
            for box, text, _ in body[index]:
                x = sum(point[0] for point in box) / 4
                col = min(
                    range(len(centers)), key=lambda value: abs(centers[value] - x)
                )
                ocr_cells[col].append(text)
        for col, value in enumerate(row):
            if value is None:
                flags.append(f"unreadable_cell:{index}:{col}")
                continue
            numbers = _numbers(value)
            if numbers and (
                col >= len(ocr_cells) or numbers != _numbers(" ".join(ocr_cells[col]))
            ):
                flags.append(f"numeric_disagreement:{index}:{col}")
            if "%" in str(value):
                try:
                    rate = float(normalize_numeric_tokens(str(value)).strip("%* "))
                    if not cfg["rate_min_percent"] <= rate <= cfg["rate_max_percent"]:
                        flags.append(f"implausible_rate:{index}:{col}")
                except ValueError:
                    flags.append(f"invalid_rate:{index}:{col}")
        if columns and "tenure" in columns[0].lower() and row and _numbers(row[0]):
            tenure = float(_numbers(row[0])[0])
            if previous is not None and tenure < previous:
                flags.append("tenures_not_ascending")
            previous = tenure
        if row and str(row[0]).strip().lower() == "total":
            from decimal import Decimal, InvalidOperation

            for col in range(1, len(row)):
                try:
                    expected = sum(
                        Decimal(str(other[col]).replace(",", ""))
                        for other in structured["rows"][:index]
                    )
                    if expected != Decimal(str(row[col]).replace(",", "")):
                        flags.append(f"total_disagreement:{index}:{col}")
                except (InvalidOperation, IndexError):
                    flags.append(f"unverifiable_total:{index}:{col}")
    return list(dict.fromkeys(flags))


def validate_fields(structured, blocks):
    """A null or conflicting numeric screenshot field must reach the confirmation path."""
    if not isinstance(structured, dict):
        return ["missing_screenshot_structure"]
    local = {number for block in blocks for number in _numbers(block[1])}
    flags = []

    def walk(value, path=""):
        if isinstance(value, dict):
            if "value" in value and value["value"] is None:
                flags.append(f"unreadable_field:{path}")
            for key, child in value.items():
                if key not in {
                    "confidence",
                    "overall_confidence",
                    "notes",
                    "uncertain",
                }:
                    walk(child, f"{path}.{key}")
        elif isinstance(value, list):
            for child in value:
                walk(child, path)
        elif _numbers(value) and not set(_numbers(value)) <= local:
            flags.append(f"numeric_disagreement:{path}")

    walk(structured)
    return flags


class ImageCascade:
    def __init__(self, store, llm=None, ocr=None):
        self.store, self.llm, self.ocr = store, llm, ocr or run_ocr

    def extract(self, blob: bytes, owner_key: str, *, mode: str, budget=None):
        cfg = get_settings()
        with Image.open(io.BytesIO(blob)) as original:
            image = original.convert("RGB")
        # Container extraction may encode identical PDF/PPTX pixels differently.
        key = digest(f"{image.size}|".encode() + image.tobytes())
        query = {"image_hash": key, "owner_key": owner_key}
        cached = self.store.one("image_cache", query)
        if (
            cached
            and not cached["result"].get("needs_retry")
            and cached["result"].get("cache_version")
            == cfg.knowledge["images"]["cache_version"]
            and (not cached.get("expires_at") or cached["expires_at"] > utcnow())
        ):
            log.step("OCR", "image cache hit", image=key[:8])
            return {**cached["result"], "cache_hit": True}
        settings = cfg.knowledge["images"]
        if image.width * image.height < settings["decorative_min_area"]:
            result = {
                "text": "",
                "structured": None,
                "method": "decorative",
                "classification": "decorative",
                "confidence": 1.0,
                "flags": [],
                "needs_review": False,
                "validated": True,
            }
        else:
            blocks = self.ocr(image)
            text = "\n".join(block[1] for block in blocks)
            kind, critical, confidence, density = classify(
                blocks, image.width, image.height
            )
            result = {
                "text": mask_text(text),
                "structured": None,
                "method": "ocr",
                "classification": kind,
                "confidence": confidence,
                "flags": [],
                "needs_review": False,
                "validated": True,
            }
            variance = float(
                np.var(np.asarray(image.convert("L").filter(ImageFilter.FIND_EDGES)))
            )
            if not blocks or variance < settings["blur_variance_min"]:
                result.update(
                    text=cfg.knowledge["messages"]["unreadable"],
                    flags=["unreadable_image"],
                    needs_review=True,
                    validated=False,
                )
            elif (
                kind != "paragraph"
                or critical
                or confidence < cfg.ocr["min_confidence"]
                or density < settings["paragraph_min_density"]
                or len(text.split()) < settings["paragraph_min_words"]
            ):
                if budget is not None and not budget():
                    result.update(
                        flags=["vlm_page_limit"], needs_review=True, validated=False
                    )
                else:
                    edges = np.asarray(image.convert("L"))[-settings["edge_margin"] :]
                    edge_cut = (
                        int((edges[-1] < 100).sum()) >= settings["edge_ink_pixels"]
                    )
                    lines = ocr_lines(blocks)
                    rows = lines[_header(lines) + 1 :]
                    last_reaches_edge = (
                        bool(rows)
                        and max(point[1] for block in rows[-1] for point in block[0])
                        >= image.height - settings["edge_margin"]
                    )
                    cut_rows = (
                        (max(0, len(rows) - int(last_reaches_edge)),)
                        if edge_cut and kind == "table"
                        else ()
                    )
                    draw = ImageDraw.Draw(image)
                    for box, raw, _ in blocks:
                        masked = mask_text(raw)
                        if masked != raw:
                            bounds = (
                                min(p[0] for p in box),
                                min(p[1] for p in box),
                                max(p[0] for p in box),
                                max(p[1] for p in box),
                            )
                            draw.rectangle(bounds, fill="white")
                            draw.text(bounds[:2], masked, fill="black")
                    if not edge_cut and blocks:
                        padding = settings["crop_padding"]
                        xs, ys = (
                            [point[0] for block in blocks for point in block[0]],
                            [point[1] for block in blocks for point in block[0]],
                        )
                        image = image.crop(
                            (
                                max(0, min(xs) - padding),
                                max(0, min(ys) - padding),
                                min(image.width, max(xs) + padding),
                                min(image.height, max(ys) + padding),
                            )
                        )
                    if image.width < settings["upscale_width"]:
                        image = image.resize(
                            (
                                settings["upscale_width"],
                                round(
                                    image.height
                                    * settings["upscale_width"]
                                    / image.width
                                ),
                            )
                        )
                    output = io.BytesIO()
                    image.save(output, format="PNG")
                    prompt = cfg.prompts[
                        "vlm_table" if kind == "table" else "vlm_screenshot"
                    ].replace("{{doc_hint}}", "untrusted bank document crop")
                    primary = getattr(cfg.models, f"vlm_{mode}")
                    disk_key = digest(f"{owner_key}|{key}|{mode}|{primary}|{prompt}")
                    disk = (
                        ROOT
                        / "data/test_cache"
                        / f"vision_{digest(owner_key)}_{disk_key}.json"
                    )
                    if disk.exists():
                        response = json.loads(disk.read_text(encoding="utf-8"))
                        log.step("VLM", "disk cache hit", image=key[:8])
                    else:
                        response = None
                    # Temporary provider failures must never become permanent cache hits.
                    if response is None or response.get("needs_retry") or (
                        response.get("status") == "unreadable"
                        and response.get("warning") == cfg.messages["upload"]["vision_unavailable"]
                    ):
                        llm = self.llm or LLMClient()
                        try:
                            if self.llm is None and llm.quota.remaining()["vlm"] <= 0:
                                raise QuotaExceeded("Vision daily budget exhausted")
                            response = llm.vision(
                                output.getvalue(),
                                "image/png",
                                prompt,
                                mode=mode,
                                pii_redacted=True,
                                local_ocr=mask_text(text),
                                cut_off_rows=cut_rows,
                            )
                        finally:
                            if self.llm is None:
                                llm.close()
                        disk.parent.mkdir(parents=True, exist_ok=True)
                        disk.write_text(
                            json.dumps(response, ensure_ascii=False), encoding="utf-8"
                        )
                    structured = response.get("structured")
                    if isinstance(structured, dict):
                        structured = postprocess_vision(
                            structured, mask_text(text), cut_off_rows=cut_rows
                        )
                        response["text"] = json.dumps(structured, ensure_ascii=False)
                    flags = (
                        validate_table(structured, blocks)
                        if kind == "table"
                        else validate_fields(structured, blocks)
                        if critical
                        else []
                    )
                    if response.get("status") == "unreadable":
                        flags.append("unreadable_image")
                    if not structured and critical:
                        flags.append("unverified_numeric_image")
                    if edge_cut:
                        flags.append("truncated")
                    result.update(
                        needs_retry=response.get("needs_retry", False),
                        processing_state="needs_retry" if response.get("needs_retry") else "done",
                        text=response["text"],
                        structured=structured,
                        truncated=edge_cut or bool((structured or {}).get("truncated")),
                        method="vlm",
                        flags=flags,
                        needs_review=bool(flags),
                        validated=not flags,
                        confidence=float(
                            (structured or {}).get("overall_confidence", confidence)
                        ),
                    )
        result["cache_version"] = settings["cache_version"]
        record = {**query, "result": result, "created_at": utcnow()}
        if owner_key != "bank":
            record["expires_at"] = utcnow() + timedelta(
                hours=cfg.retention["image_cache_hours"]
            )
        self.store.put("image_cache", query, record)
        log.step(
            "VLM" if result["method"] == "vlm" else "OCR",
            "image result",
            image=key[:8],
            classification=result["classification"],
            flags=len(result["flags"]),
        )
        return {**result, "cache_hit": False}
