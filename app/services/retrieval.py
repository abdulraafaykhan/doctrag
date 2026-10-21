"""Vector search over the chunks collection (Atlas $vectorSearch)."""
from app.config import get_settings
from app.db import chunks_col
from app.services.embeddings import embed_query


async def search(query: str, top_k: int | None = None, doc_ids: list[str] | None = None) -> list[dict]:
    s = get_settings()
    top_k = top_k or s.top_k
    query_vector = await embed_query(query)

    stage: dict = {
        "index": s.vector_index_name,
        "path": "embedding",
        "queryVector": query_vector,
        "numCandidates": max(100, top_k * 20),  # wider candidate pool = better recall
        "limit": top_k,
    }
    if doc_ids:
        stage["filter"] = {"doc_id": {"$in": doc_ids}}

    pipeline = [
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
                "score": {"$meta": "vectorSearchScore"},
            }
        },
    ]
    cursor = await chunks_col().aggregate(pipeline)
    return await cursor.to_list()


async def ready_document_names(doc_ids: list[str] | None = None, limit: int = 30) -> list[str]:
    """File names of the documents the assistant may use (the planner needs them to judge relevance)."""
    from bson import ObjectId
    from bson.errors import InvalidId

    from app.db import documents_col

    q: dict = {"status": "ready"}
    if doc_ids:
        try:
            q["_id"] = {"$in": [ObjectId(d) for d in doc_ids]}
        except (InvalidId, TypeError):
            return []
    cursor = documents_col().find(q, {"filename": 1}).sort("created_at", -1)
    return [d["filename"] for d in await cursor.to_list(limit)]