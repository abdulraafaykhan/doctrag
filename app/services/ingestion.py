"""Orchestrates: register file -> load -> chunk -> embed -> store."""

from dataclasses import replace

from bson import ObjectId

from app.config import get_settings
from app.db import chunks_col, documents_col
from app.services.chunking import chunk_sections
from app.services.embeddings import embed_documents
from app.services.loaders import load_file
from app.verification.masking import mask_sensitive


async def ingest_document(
    doc_id: str,
    filename: str,
    data: bytes,
    *,
    metadata: dict,
    max_chunks: int | None = None,
    max_table_rows: int | None = None,
) -> None:
    """Run the full pipeline for one document. Never raises: failures are stored on the doc."""
    try:
        loaded = load_file(filename, data, max_table_rows=max_table_rows)
        warnings = list(loaded.warnings)
        # Mask whole sections before splitting: a chunk boundary must not split a secret.
        if metadata["scope"] == "user":
            loaded.sections = [
                replace(
                    section,
                    text=mask_sensitive(section.text),
                    heading=mask_sensitive(section.heading),
                    location=mask_sensitive(section.location),
                )
                for section in loaded.sections
            ]
        chunks = chunk_sections(loaded.sections)

        cap = get_settings().max_chunks_per_doc if max_chunks is None else max_chunks
        if len(chunks) > cap:
            warnings.append(
                f"The file produced {len(chunks)} chunks; only the first {cap} were indexed."
            )
            chunks = chunks[:cap]

        # Mask private text before either persistence or Gemini embedding; shared KB stays exact.
        if metadata["scope"] == "user":
            filename = mask_sensitive(filename)
            warnings = [mask_sensitive(warning) for warning in warnings]
            chunks = [
                {
                    key: mask_sensitive(value) if isinstance(value, str) else value
                    for key, value in chunk.items()
                }
                for chunk in chunks
            ]
        # Embed enriched text, but display its clean passage.
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
            {
                "_id": ObjectId(doc_id),
                **{k: v for k, v in metadata.items() if k != "expires_at"},
            },
            {
                "$set": {
                    "status": "ready",
                    "num_chunks": len(rows),
                    "error": None,
                    "warnings": warnings,
                }
            },
        )
    except Exception:  # noqa: BLE001 - we want to record any failure
        await chunks_col().delete_many(
            {
                "doc_id": doc_id,
                **{k: v for k, v in metadata.items() if k != "expires_at"},
            }
        )  # no half-ingested documents
        await documents_col().update_one(
            {
                "_id": ObjectId(doc_id),
                **{k: v for k, v in metadata.items() if k != "expires_at"},
            },
            {
                "$set": {
                    "status": "failed",
                    "error": "The file could not be processed. Please try again.",
                }
            },
        )
