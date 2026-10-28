"""One shared MongoDB client and the project's index definitions."""

from threading import Lock
from time import monotonic, sleep

from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.operations import SearchIndexModel

from app.config import get_settings
from app.log import log

_client: MongoClient | None = None
_client_lock = Lock()


def get_client() -> MongoClient:
    global _client
    # A lock prevents simultaneous first requests from opening multiple pools.
    with _client_lock:
        if _client is None:
            settings = get_settings()
            _client = MongoClient(
                settings.secrets.mongodb_uri.get_secret_value(),
                serverSelectionTimeoutMS=settings.database.timeout_ms,
                connectTimeoutMS=settings.database.timeout_ms,
                socketTimeoutMS=settings.database.timeout_ms,
                maxPoolSize=settings.database.max_pool_size,
                tz_aware=True,
            )
        return _client


def get_db():
    return get_client()[get_settings().database.name]


def collection(name: str):
    return get_db()[name]


def close_db() -> None:
    global _client
    with _client_lock:
        if _client is not None:
            _client.close()
            _client = None


def ping() -> None:
    with log.span("DB", "ping"):
        get_db().command("ping")


def normal_indexes() -> dict[str, list[tuple[list[tuple[str, int]], dict]]]:
    def unique(field):
        return ([(field, ASCENDING)], {"unique": True})

    def plain(*fields):
        return ([(field, ASCENDING) for field in fields], {})

    ttl = ([("expires_at", ASCENDING)], {"expireAfterSeconds": 0})
    return {
        "users": [unique("mobile"), unique("user_id")],
        "accounts": [unique("account_number"), unique("account_id")],
        "cards": [unique("card_id")],
        "transactions": [
            ([("account_id", ASCENDING), ("posted_at", DESCENDING)], {}),
            unique("txn_id"),
        ],
        "sessions": [unique("session_id"), ttl],
        "pending_actions": [unique("token"), ttl],
        "requests": [
            unique("idempotency_key"),
            ([("user_id", ASCENDING), ("submitted_at", DESCENDING)], {}),
        ],
        "audit_log": [plain("user_id", "ts"), ttl],
        "documents": [
            plain("namespace", "owner_key", "lifecycle"),
            ([("file_hash", ASCENDING), ("owner_key", ASCENDING)], {"unique": True}),
            ttl,
        ],
        "sections": [plain("doc_id"), ttl],
        "chunks": [
            plain("doc_id"),
            plain('content_hash', 'embedding_model'),
            ttl,
            (
                [("text", "text")],
                {"name": "chunks_text", "default_language": "english"},
            ),
        ],
        "rate_rows": [plain("product", "lifecycle"), plain("table_id"), ttl],
        "rate_tables": [plain("doc_id"), ttl],
        "extractions": [
            ([("extraction_id", ASCENDING)], {'unique': True, 'sparse': True}),
            plain('doc_id'),
            plain('image_hash', 'owner_key'),
            ttl,
        ],
        "quota_usage": [unique("day")],
    }


def search_models() -> list[SearchIndexModel]:
    cfg = get_settings()
    filters = ["namespace", "owner_key", "lifecycle", "doc_type", "product"]
    return [
        SearchIndexModel(
            name=cfg.database.vector_index,
            type="vectorSearch",
            definition={
                "fields": [
                    {
                        "type": "vector",
                        "path": "embedding",
                        "numDimensions": cfg.models.embed_dims,
                        "similarity": "cosine",
                    },
                    *[{"type": "filter", "path": field} for field in filters],
                ]
            },
        ),
    ]


def index_status() -> dict:
    missing = []
    for name, definitions in normal_indexes().items():
        with log.span("DB", "list indexes", collection=name):
            actual = list(collection(name).list_indexes())
        for keys, options in definitions:
            if not any(
                (
                    list(item["key"].items()) == keys
                    or keys == [("text", "text")]
                    and item.get("weights", {}).get("text") == 1
                )
                and all(item.get(k) == v for k, v in options.items())
                for item in actual
            ):
                missing.append(f"{name}:" + ",".join(field for field, _ in keys))
    with log.span("DB", "list search indexes"):
        search = {
            item["name"]: item for item in collection("chunks").list_search_indexes()
        }
    cfg = get_settings().database
    statuses = {
        name: search.get(name, {}).get("status", "MISSING")
        for name in (cfg.vector_index,)
    }
    ready = not missing and all(
        search.get(name, {}).get("queryable", False) and status == "READY"
        for name, status in statuses.items()
    )
    return {"ready": ready, "missing_normal": missing, "search": statuses}


def create_indexes() -> None:
    for name, definitions in normal_indexes().items():
        for keys, options in definitions:
            with log.span("DB", "create index", collection=name, fields=keys):
                collection(name).create_index(keys, **options)
    chunks = collection("chunks")
    with log.span("DB", "list search indexes"):
        existing = {item["name"] for item in chunks.list_search_indexes()}
    for model in search_models():
        if model.document["name"] not in existing:
            with log.span("DB", "create search index", name=model.document["name"]):
                chunks.create_search_index(model)
    deadline = monotonic() + get_settings().database.index_wait_seconds
    while monotonic() < deadline:
        state = index_status()
        log.step("DB", "index status", **state)
        if state["ready"]:
            return
        if "FAILED" in state["search"].values():
            raise RuntimeError(
                "Atlas search index build failed; inspect the Atlas index error"
            )
        sleep(get_settings().database.index_poll_seconds)
    raise TimeoutError(
        "Atlas indexes did not become READY within the configured timeout"
    )
