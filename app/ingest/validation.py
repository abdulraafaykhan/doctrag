"""Validate signatures and containers before opening untrusted uploads."""

import io
import zipfile
from pathlib import Path

import filetype

from app.config import get_settings
from app.log import log


class InvalidFile(ValueError):
    pass


def validate_file(path: Path, *, customer: bool) -> str:
    cfg = get_settings()
    ext = path.suffix.lower().lstrip(".")
    rules = cfg.knowledge["parsing"]
    if (
        ext not in cfg.uploads["allowed_extensions"]
        or ext in rules["executable_extensions"]
    ):
        raise InvalidFile(rules["unsupported_error"])
    if customer and path.stat().st_size > cfg.uploads["max_mb"] * 1024 * 1024:
        raise InvalidFile(rules["too_large_error"])
    data = path.read_bytes()
    kind = filetype.guess(data)
    if not data or data.startswith((b"MZ", b"\x7fELF")):
        raise InvalidFile(rules["corrupt_error"])
    if ext in {"docx", "pptx", "xlsx"}:
        required = {
            "docx": "word/document.xml",
            "pptx": "ppt/presentation.xml",
            "xlsx": "xl/workbook.xml",
        }[ext]
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                names = archive.namelist()
                infos = archive.infolist()
                expanded = sum(item.file_size for item in infos)
                compressed = max(1, sum(item.compress_size for item in infos))
                if (
                    required not in names
                    or expanded > rules["zip_uncompressed_mb"] * 1024 * 1024
                    or expanded / compressed > rules["zip_ratio_limit"]
                ):
                    raise InvalidFile(rules["corrupt_error"])
        except zipfile.BadZipFile:
            raise InvalidFile(rules["corrupt_error"]) from None
    elif ext == "pdf":
        if not data.startswith(b"%PDF-"):
            raise InvalidFile(rules["corrupt_error"])
    elif ext in {"png", "jpg", "jpeg", "webp"}:
        expected = "jpg" if ext == "jpeg" else ext
        if kind is None or kind.extension != expected:
            raise InvalidFile(rules["corrupt_error"])
    else:
        if kind is not None:
            raise InvalidFile(rules["corrupt_error"])
        try:
            text = data.decode("utf-8-sig")
            if "\x00" in text:
                raise UnicodeError
        except UnicodeError:
            raise InvalidFile(rules["corrupt_error"]) from None
    log.step("UPLOAD", "validated", extension=ext, bytes=len(data))
    return ext
