"""Load the fictional shared bank library. Requires live Atlas and Gemini credentials."""

import asyncio
import mimetypes
from pathlib import Path

from app.db import (
    chunks_col,
    close_mongo,
    connect_to_mongo,
    documents_col,
    ensure_indexes,
)
from app.services.document_store import ingest_scoped, register_document
from app.services.loaders import SUPPORTED_EXTENSIONS

SEED_DIR = Path(__file__).resolve().parents[1] / "seed_docs"


async def seed_kb(directory: Path = SEED_DIR) -> None:
    for path in sorted(directory.iterdir()):
        if not path.is_file() or path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        # Only the local shared-library seed can replace shared documents by name.
        query = {"scope": "shared", "session_id": None, "filename": path.name}
        old = await documents_col().find(query).to_list()
        for row in old:
            await chunks_col().delete_many(
                {"doc_id": str(row["_id"]), "scope": "shared", "session_id": None}
            )
        await documents_col().delete_many(query)
        data = path.read_bytes()
        row, duplicate = await register_document(
            path.name, mimetypes.guess_type(path.name)[0] or "", data, scope="shared"
        )
        if not duplicate:
            await ingest_scoped(str(row["_id"]), path.name, data, scope="shared")
        result = await documents_col().find_one({"_id": row["_id"], "scope": "shared"})
        if result is None or result["status"] != "ready":
            raise RuntimeError(
                "Shared document ingestion failed. Inspect document status before retrying."
            )
    print("Fictional shared bank library is ready.")


async def main() -> None:
    try:
        await connect_to_mongo()
        await ensure_indexes()
        await seed_kb()
    finally:
        await close_mongo()


if __name__ == "__main__":
    asyncio.run(main())
