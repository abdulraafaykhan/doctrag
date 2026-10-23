"""Scoped document registration and ingestion; callers supply server-verified context."""

import hashlib
from datetime import datetime, timezone
from typing import Literal

from bson import ObjectId
from fastapi import HTTPException
from pymongo.errors import DuplicateKeyError

from app.config import get_settings
from app.db import chunks_col, documents_col, sessions_col
from app.services.ingestion import ingest_document
from app.sessions.store import deadline, require_session, require_verified
from app.verification.masking import mask_sensitive


def user_scope(session_id: str) -> dict:
    return {"scope": "user", "session_id": session_id}


def expiry_for(session: dict) -> datetime:
    return deadline(session["last_activity_at"])


async def register_document(
    filename: str,
    content_type: str,
    data: bytes,
    *,
    scope: Literal["shared", "user"],
    session_id: str | None = None,
) -> tuple[dict, bool]:
    if scope == "user":
        if not session_id:
            raise ValueError("User documents require server context")
        session = await require_session(session_id)
        require_verified(session)
        filename = mask_sensitive(filename)
        expires_at = expiry_for(session)
    else:
        if session_id is not None:
            raise ValueError("Shared documents cannot have an owner or session")
        expires_at = None
    fields = {"scope": scope, "session_id": session_id}
    key = {**fields, "file_hash": hashlib.sha256(data).hexdigest()}
    existing = await documents_col().find_one(key)
    if existing:
        if existing["status"] == "failed":
            result = await documents_col().update_one(
                {"_id": existing["_id"], **key, "status": "failed"},
                {
                    "$set": {
                        "status": "processing",
                        "error": None,
                        "warnings": [],
                        "created_at": datetime.now(timezone.utc),
                    }
                },
            )
            if result.matched_count:
                existing.update(status="processing", error=None, warnings=[])
                return existing, False
        return existing, True
    reserved = False
    if scope == "user":
        # Reserve a slot atomically so parallel uploads cannot exceed the per-chat cap.
        result = await sessions_col().update_one(
            {
                "_id": session_id,
                "$or": [
                    {"attachment_count": {"$lt": 5}},
                    {"attachment_count": {"$exists": False}},
                ],
            },
            {"$inc": {"attachment_count": 1}},
        )
        if not result.matched_count:
            await require_session(session_id)
            raise HTTPException(400, "At most 5 files are allowed per chat.")
        reserved = True
    row = {
        "_id": ObjectId(),
        **key,
        "expires_at": expires_at,
        "filename": filename,
        "content_type": content_type,
        "size_bytes": len(data),
        "status": "processing",
        "attachment_customer_id": session["verified"]["customer_id"]
        if scope == "user"
        else None,
        "num_chunks": 0,
        "error": None,
        "warnings": [],
        "created_at": datetime.now(timezone.utc),
    }
    try:
        await documents_col().insert_one(row)
    except Exception as exc:
        if reserved:
            await sessions_col().update_one(
                {"_id": session_id}, {"$inc": {"attachment_count": -1}}
            )
        if isinstance(exc, DuplicateKeyError):
            duplicate = await documents_col().find_one(key)
            if duplicate:
                return duplicate, True
        raise
    if scope == "user" and await sessions_col().find_one({"_id": session_id}) is None:
        await documents_col().delete_one({"_id": row["_id"], **fields})
        raise HTTPException(404, "Session not found")
    return row, False


async def ingest_scoped(
    doc_id: str,
    filename: str,
    data: bytes,
    *,
    scope: Literal["shared", "user"],
    session_id: str | None = None,
) -> None:
    fields = {"scope": scope, "session_id": session_id}
    query = {"_id": ObjectId(doc_id), **fields}
    document = await documents_col().find_one(query)
    if document is None:
        return
    if scope == "user":
        session = await sessions_col().find_one({"_id": session_id})
        if session is None or session.get("attachment_customer_id") != document.get(
            "attachment_customer_id"
        ):
            await documents_col().delete_one(query)
            return
        expires_at = expiry_for(session)
    else:
        expires_at = None
    # A retry after an interrupted ingestion replaces any partial chunks for this document.
    await chunks_col().delete_many({"doc_id": doc_id, **fields})
    settings = get_settings()
    await ingest_document(
        doc_id,
        filename,
        data,
        metadata={**fields, "expires_at": expires_at},
        max_chunks=settings.user_max_chunks_per_doc
        if scope == "user"
        else settings.max_chunks_per_doc,
        max_table_rows=settings.user_max_table_rows
        if scope == "user"
        else settings.max_table_rows,
    )
    if scope == "user":
        # Ingestion may outlive a delete or a message that extended the session's expiry.
        session = await sessions_col().find_one({"_id": session_id})
        if (
            session is None
            or session.get("attachment_customer_id")
            != document.get("attachment_customer_id")
            or await documents_col().find_one(query) is None
        ):
            await chunks_col().delete_many({"doc_id": doc_id, **fields})
            await documents_col().delete_one(query)
            return
        expiry = expiry_for(session)
        await documents_col().update_one(query, {"$max": {"expires_at": expiry}})
        await chunks_col().update_many(
            {"doc_id": doc_id, **fields}, {"$max": {"expires_at": expiry}}
        )
