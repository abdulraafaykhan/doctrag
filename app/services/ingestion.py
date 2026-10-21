"""Orchestrates: register file -> load -> chunk -> embed -> store."""
from datetime import datetime, timezone

from bson import ObjectId

from app.config import get_settings
from app.db import chunks_col, documents_col
from app.services.chunking import chunk_sections
from app.services.embeddings import embed_documents
from app.services.loaders import load_file


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def ingest_document(doc_id: str, filename: str, data: bytes, *, metadata: dict,
                          max_chunks: int | None = None, max_table_rows: int | None = None) -> None:
    """Run the full pipeline for one document. Never raises: failures are stored on the doc."""
    try:
        loaded = load_file(filename, data, max_table_rows=max_table_rows)
        warnings = list(loaded.warnings)
        chunks = chunk_sections(loaded.sections)

        cap = get_settings().max_chunks_per_doc if max_chunks is None else max_chunks
        if len(chunks) > cap:
            warnings.append(f"The file produced {len(chunks)} chunks; only the first {cap} were indexed.")
            chunks = chunks[:cap]

        # embed the header-enriched text, but store/display the clean text
        vectors = await embed_documents([c["embed_text"] for c in chunks])

        rows = [
            {
                **metadata,
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
            {"_id": ObjectId(doc_id), **{k: v for k, v in metadata.items() if k != "expires_at"}},
            {"$set": {"status": "ready", "num_chunks": len(rows), "error": None, "warnings": warnings}},
        )
    except Exception as e:  # noqa: BLE001 - we want to record any failure
        await chunks_col().delete_many({"doc_id": doc_id, **{k: v for k, v in metadata.items() if k != "expires_at"}})  # no half-ingested documents
        await documents_col().update_one(
            {"_id": ObjectId(doc_id), **{k: v for k, v in metadata.items() if k != "expires_at"}},
            {"$set": {"status": "failed", "error": str(e)[:500]}},
        )