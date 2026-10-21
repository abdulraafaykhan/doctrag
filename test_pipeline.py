"""Test ingestion + retrieval end to end, without FastAPI:

    uv run python test_pipeline.py path/to/file.pdf "your question about the file"
"""
import asyncio
import sys
from pathlib import Path

from app.db import close_mongo, connect_to_mongo, ensure_indexes
from app.services.ingestion import ingest_document, register_document
from app.services.retrieval import search
from app.db import documents_col
from bson import ObjectId


async def main(path: str, question: str) -> None:
    await connect_to_mongo()
    await ensure_indexes()

    p = Path(path)
    data = p.read_bytes()
    doc_id, duplicate = await register_document(p.name, "", data)

    if duplicate:
        print(f"Already ingested (doc_id={doc_id}), skipping embedding.")
    else:
        print(f"Ingesting {p.name} (doc_id={doc_id})...")
        await ingest_document(doc_id, p.name, data)
        doc = await documents_col().find_one({"_id": ObjectId(doc_id)})
        print(f"  status={doc['status']} chunks={doc['num_chunks']} error={doc['error']}")
        if doc["status"] != "ready":
            await close_mongo()
            return
        print("  waiting a few seconds for Atlas to index the new vectors...")
        await asyncio.sleep(8)

    print(f"\nQuestion: {question}\n")
    hits = await search(question, doc_ids=[doc_id])
    if not hits:
        print("No results yet. Wait ~10 seconds and run again (index may still be catching up).")
    for i, h in enumerate(hits, start=1):
        page = f" p.{h['page']}" if h.get("page") else ""
        print(f"[{i}] score={h['score']:.3f} {h['filename']}{page}")
        print("    " + h["text"][:300].replace("\n", " ") + "...\n")

    await close_mongo()


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit('Usage: uv run python test_pipeline.py <file> "<question>"')
    asyncio.run(main(sys.argv[1], sys.argv[2]))