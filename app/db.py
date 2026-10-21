"""MongoDB Atlas connection, collections, and index setup (async)."""
import certifi
from pymongo import ASCENDING, AsyncMongoClient
from pymongo.asynchronous.collection import AsyncCollection
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.operations import SearchIndexModel

from app.config import get_settings

_client: AsyncMongoClient | None = None


async def connect_to_mongo() -> None:
    """Create the client once (called at app startup) and verify it with a ping."""
    global _client
    settings = get_settings()
    _client = AsyncMongoClient(
        settings.mongodb_uri,
        tlsCAFile=certifi.where(),
        serverSelectionTimeoutMS=8000,
    )
    await _client.admin.command("ping")


async def close_mongo() -> None:
    """Close the client (called at app shutdown)."""
    global _client
    if _client is not None:
        await _client.close()
        _client = None


def get_db() -> AsyncDatabase:
    if _client is None:
        raise RuntimeError("MongoDB is not connected. Call connect_to_mongo() first.")
    return _client[get_settings().mongodb_db_name]


# --- Collection accessors ---
def documents_col() -> AsyncCollection:
    return get_db()["documents"]


def chunks_col() -> AsyncCollection:
    return get_db()["chunks"]


def messages_col() -> AsyncCollection:
    return get_db()["messages"]


async def ensure_indexes() -> None:
    """Create collections, normal indexes, and the Atlas vector search index if missing."""
    settings = get_settings()
    db = get_db()

    # Atlas needs the collection to exist before a search index can be created on it.
    existing = await db.list_collection_names()
    for name in ("documents", "chunks", "messages"):
        if name not in existing:
            await db.create_collection(name)

    # Regular indexes: fast lookups by document and by conversation.
    await chunks_col().create_index([("doc_id", ASCENDING), ("chunk_index", ASCENDING)])
    await messages_col().create_index([("session_id", ASCENDING), ("created_at", ASCENDING)])
    await documents_col().create_index([("file_hash", ASCENDING)])

    # Vector search index (built by Atlas in the background, usually under a minute).
    cursor = await chunks_col().list_search_indexes(settings.vector_index_name)
    found = await cursor.to_list()
    if not found:
        model = SearchIndexModel(
            name=settings.vector_index_name,
            type="vectorSearch",
            definition={
                "fields": [
                    {
                        "type": "vector",
                        "path": "embedding",
                        "numDimensions": settings.embed_dim,
                        "similarity": "cosine",
                    },
                    # lets us restrict a search to specific documents
                    {"type": "filter", "path": "doc_id"},
                ]
            },
        )
        await chunks_col().create_search_index(model=model)


async def vector_index_status() -> str:
    """Return the vector index state, e.g. 'READY' or 'BUILDING' (or 'MISSING')."""
    cursor = await chunks_col().list_search_indexes(get_settings().vector_index_name)
    found = await cursor.to_list()
    if not found:
        return "MISSING"
    return found[0].get("status", "UNKNOWN")