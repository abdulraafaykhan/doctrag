"""Server-built visibility filters for shared knowledge and the caller's chat files."""

from app.config import get_settings
from app.db import chunks_col, documents_col
from app.services.embeddings import embed_query


def visibility_filter(session_id: str, include_private: bool = True) -> dict:
    if not include_private:
        return {"scope": "shared"}
    return {
        "$or": [
            {"scope": "shared"},
            {"scope": "user", "session_id": session_id},
        ]
    }


async def search(
    query: str,
    *,
    session_id: str,
    include_private: bool = True,
    top_k: int | None = None,
) -> list[dict]:
    settings = get_settings()
    top_k = top_k or settings.top_k
    visible = visibility_filter(session_id, include_private)
    # Ready document ids also exclude stale search-index entries after removal or replacement.
    ready = (
        await documents_col().find({**visible, "status": "ready"}, {"_id": 1}).to_list()
    )
    ids = [str(row["_id"]) for row in ready]
    if not ids:
        return []
    stage = {
        "index": settings.vector_index_name,
        "path": "embedding",
        "queryVector": await embed_query(query),
        "numCandidates": max(100, top_k * 20),
        "limit": top_k,
        "filter": {"$and": [visible, {"doc_id": {"$in": ids}}]},
    }
    cursor = await chunks_col().aggregate(
        [
            {"$vectorSearch": stage},
            {
                "$project": {
                    "_id": 0,
                    "text": 1,
                    "page": 1,
                    "location": 1,
                    "kind": 1,
                    "doc_id": 1,
                    "filename": 1,
                    "chunk_index": 1,
                    "scope": 1,
                    "score": {"$meta": "vectorSearchScore"},
                }
            },
        ]
    )
    return await cursor.to_list()


async def ready_document_names(
    *, session_id: str, include_private: bool = True, limit: int = 30
) -> list[str]:
    query = {**visibility_filter(session_id, include_private), "status": "ready"}
    cursor = documents_col().find(query, {"filename": 1}).sort("created_at", -1)
    return [row["filename"] for row in await cursor.to_list(limit)]
