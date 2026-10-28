"""The shared parser element format and document metadata."""

import hashlib
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field


def digest(value: bytes | str) -> str:
    return hashlib.sha256(
        value.encode() if isinstance(value, str) else value
    ).hexdigest()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Element(BaseModel):
    type: Literal["text", "table", "image"]
    content: Any
    page: int = Field(ge=1)
    sheet: str | None = None
    section: str = ""
    bbox: list[float] | None = None
    source_method: str
    confidence: float = Field(default=1.0, ge=0, le=1)
    needs_review: bool = False
    flags: list[str] = Field(default_factory=list)
    extraction_id: str | None = None


class DocumentMeta(BaseModel):
    title: str
    doc_type: Literal["faq", "policy", "rate_card", "offer", "other"] = "other"
    product: str = ""
    version: str = "1"
    effective_date: str = ""
    lifecycle: Literal["active", "superseded"] = "active"
