"""Orchestrates: register file -> load -> chunk -> embed -> store."""
import hashlib
from datetime import datetime, timezone

from bson import ObjectId

from app.config import get_settings
from app.db import chunks_col, documents_col
from app.services.chunking import chunk_sections
from app.services.embeddings import embed_documents
from app.services.loaders import load_file


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def register_document(filename: str, content_type: str, data: bytes) -> tuple[str, bool]:
    """Create the document record. Returns (doc_id, is_duplicate).

    If the exact same file was already ingested successfully, we reuse it instead of
    paying for embeddings again.
    """
    file_hash = hashlib.sha256(data).hexdigest()
    existing = await documents_col().find_one({"file_hash": file_hash, "status": "ready"})
    if existing:
        return str(existing["_id"]), True

    result = await documents_col().insert_one(
        {
            "filename": filename,
            "content_type": content_type,
            "file_hash": file_hash,
            "size_bytes": len(data),
            "status": "processing",
            "num_chunks": 0,
            "error": None,
            "warnings": [],
            "created_at": _now(),
        }
    )
    return str(result.inserted_id), False


async def ingest_document(doc_id: str, filename: str, data: bytes) -> None:
    """Run the full pipeline for one document. Never raises: failures are stored on the doc."""
    try:
        loaded = load_file(filename, data)
        warnings = list(loaded.warnings)
        chunks = chunk_sections(loaded.sections)

        cap = get_settings().max_chunks_per_doc
        if len(chunks) > cap:
            warnings.append(f"The file produced {len(chunks)} chunks; only the first {cap} were indexed.")
            chunks = chunks[:cap]

        # embed the header-enriched text, but store/display the clean text
        vectors = await embed_documents([c["embed_text"] for c in chunks])

        rows = [
            {
                "doc_id": doc_id,
                "filename": filename,
                "chunk_index": c["chunk_index"],
                "page": c["page"],
                "location": c["location"],
                "kind": c["kind"],
                "text": c["text"],
                "embedding": v,
            }
            for c, v in zip(chunks, vectors)
        ]
        await chunks_col().insert_many(rows)
        await documents_col().update_one(
            {"_id": ObjectId(doc_id)},
            {"$set": {"status": "ready", "num_chunks": len(rows), "error": None, "warnings": warnings}},
        )
    except Exception as e:  # noqa: BLE001 - we want to record any failure
        await chunks_col().delete_many({"doc_id": doc_id})  # no half-ingested documents
        await documents_col().update_one(
            {"_id": ObjectId(doc_id)},
            {"$set": {"status": "failed", "error": str(e)[:500]}},
        )