"""Phase 2 integration checks with in-memory Mongo substitutes and no live providers."""
import asyncio
import copy
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import jwt
import pytest
from fastapi.testclient import TestClient

from app.auth import router as auth_router, security
from app.auth.throttle import LoginThrottle
from app.config import Settings, get_settings
from app.services import audit, maintenance, memory


class Cursor:
    def __init__(self, rows, projection=None):
        self.rows = copy.deepcopy(rows)
        self.projection = projection

    def sort(self, key, direction):
        self.rows.sort(key=lambda r: r[key], reverse=direction < 0)
        return self

    def limit(self, n):
        self.rows = self.rows[:n]
        return self

    async def to_list(self, length=None):
        rows = copy.deepcopy(self.rows if length is None else self.rows[:length])
        if self.projection:
            for row in rows:
                if any(v == 1 for v in self.projection.values()):
                    for key in list(row):
                        if not self.projection.get(key):
                            del row[key]
                else:
                    for key, include in self.projection.items():
                        if not include:
                            row.pop(key, None)
        return rows


class Collection:
    def __init__(self):
        self.rows = []
        self.queries = []

    def matches(self, row, query):
        for key, value in query.items():
            if key == "$or":
                if not any(self.matches(row, clause) for clause in value): return False
                continue
            if key == "$and":
                if not all(self.matches(row, clause) for clause in value): return False
                continue
            if isinstance(value, dict):
                for op, expected in value.items():
                    if op == "$exists":
                        if (key in row) != expected: return False
                    elif op == "$in":
                        if row.get(key) not in expected: return False
                    elif op == "$lt":
                        if key not in row or not row[key] < expected: return False
                    elif op == "$gt":
                        if key not in row or not row[key] > expected: return False
                    else: raise AssertionError(f"Unsupported fake query operator: {op}")
            elif row.get(key) != value:
                return False
        return True

    def apply_update(self, row, update):
        row.update(copy.deepcopy(update.get("$set", {})))
        for key, value in update.get("$inc", {}).items():
            row[key] = row.get(key, 0) + value
        for key, value in update.get("$max", {}).items():
            if row.get(key) is None or row[key] < value:
                row[key] = value

    async def find_one(self, query):
        self.queries.append(copy.deepcopy(query))
        return next((copy.deepcopy(row) for row in self.rows if self.matches(row, query)), None)

    def find(self, query, projection=None):
        self.queries.append(copy.deepcopy(query))
        rows = [copy.deepcopy(r) for r in self.rows if self.matches(r, query)]
        return Cursor(rows, projection)

    async def insert_one(self, row):
        self.rows.append(copy.deepcopy(row))

    async def delete_many(self, query):
        self.queries.append(copy.deepcopy(query))
        before = len(self.rows)
        self.rows = [row for row in self.rows if not self.matches(row, query)]
        return SimpleNamespace(deleted_count=before - len(self.rows))

    async def delete_one(self, query):
        return await self.delete_many(query)

    async def update_one(self, query, update, upsert=False):
        self.queries.append(copy.deepcopy(query))
        for row in self.rows:
            if self.matches(row, query):
                self.apply_update(row, update)
                return SimpleNamespace(modified_count=1, matched_count=1)
        if upsert:
            self.rows.append(copy.deepcopy(update["$setOnInsert"]))
        return SimpleNamespace(modified_count=0, matched_count=0)

    async def update_many(self, query, update):
        self.queries.append(copy.deepcopy(query))
        count = 0
        for row in self.rows:
            if self.matches(row, query):
                self.apply_update(row, update)
                count += 1
        return SimpleNamespace(modified_count=count)


@pytest.fixture
def world(monkeypatch):
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    for key, value in {"JWT_SECRET": "phase-two-test-secret-at-least-64-characters-abcdefghijklmnopqrstuvwxyz", "MONGODB_URI": "x",
                       "GROQ_API_KEY": "x", "GEMINI_API_KEY": "x", "CORS_ORIGINS": "http://localhost:8000"}.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    users, sessions, messages, logs, docs, chunks = [Collection() for _ in range(6)]
    encoded = security.hash_password("fake-password")
    for uid in ("a", "b"):
        users.rows.append({"_id": uid, "username": uid, "password_hash": encoded,
                           "customer_id": f"customer-{uid}", "status": "active"})
    monkeypatch.setattr(security, "users_col", lambda: users)
    monkeypatch.setattr(auth_router, "users_col", lambda: users)
    monkeypatch.setattr(auth_router, "login_throttle", LoginThrottle())
    monkeypatch.setattr(memory, "sessions_col", lambda: sessions)
    monkeypatch.setattr(memory, "messages_col", lambda: messages)
    monkeypatch.setattr(memory, "documents_col", lambda: docs)
    monkeypatch.setattr(memory, "chunks_col", lambda: chunks)
    monkeypatch.setattr(audit, "audit_log_col", lambda: logs)
    monkeypatch.setattr(maintenance, "documents_col", lambda: docs)
    from app import main
    from app.routers import chat
    main.app.dependency_overrides.clear()
    async def plan(*args):
        from app.services.router import Plan
        return Plan(question="Hello", action="greeting")
    async def names(*args, **kwargs): return []
    monkeypatch.setattr(chat, "plan_turn", plan)
    monkeypatch.setattr(chat, "ready_document_names", names)
    async def status(): return "READY"
    monkeypatch.setattr(main, "vector_index_status", status)
    client = TestClient(main.app)
    result = SimpleNamespace(client=client, users=users, sessions=sessions, messages=messages, logs=logs, docs=docs, chunks=chunks,
                             headers=lambda uid: {"Authorization": "Bearer " + security.create_access_token(uid)})
    yield result
    client.close()
    main.app.dependency_overrides.clear()
    get_settings.cache_clear()


def test_login_and_swagger_authorize(world):
    response = world.client.post("/auth/login", data={"username": "A", "password": "fake-password"})
    assert response.status_code == 200
    token = response.json()["access_token"]
    claims = jwt.decode(token, get_settings().jwt_secret.get_secret_value(), algorithms=["HS256"])
    assert claims["sub"] == "a" and claims["exp"] > claims["iat"]
    assert world.client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).json() == {"id": "a", "username": "a"}
    schema = world.client.get("/openapi.json").json()
    schemes = schema["components"]["securitySchemes"]
    assert schemes["OAuth2PasswordBearer"]["flows"]["password"]["tokenUrl"] == "auth/login"
    assert world.logs.rows[-1]["event"] == "login_ok"
    assert world.users.rows[0]["password_hash"].startswith("$argon2")


def test_unknown_wrong_password_and_locked_have_same_error(world, monkeypatch):
    seen = []
    real_verify = auth_router.verify_password
    async def verify(password, encoded):
        seen.append(encoded)
        return await real_verify(password, encoded)
    monkeypatch.setattr(auth_router, "verify_password", verify)
    wrong = world.client.post("/auth/login", data={"username": "a", "password": "wrong"})
    unknown = world.client.post("/auth/login", data={"username": "missing", "password": "wrong"})
    world.users.rows[1]["status"] = "locked"
    locked = world.client.post("/auth/login", data={"username": "b", "password": "fake-password"})
    assert wrong.status_code == unknown.status_code == locked.status_code == 401
    assert wrong.json() == unknown.json() == locked.json() == {"detail": auth_router.LOGIN_ERROR}
    assert len(seen) == 3 and seen[1] == security.dummy_hash()
    assert all(row["event"] == "login_fail" for row in world.logs.rows)


@pytest.mark.parametrize("kind", ["expired", "tampered", "missing_claim", "wrong_algorithm", "future_iat", "unknown_user", "locked"])
def test_bad_tokens_rejected(world, kind):
    now = datetime.now(timezone.utc)
    claims = {"sub": "a", "iat": now, "exp": now + timedelta(minutes=30)}
    secret = get_settings().jwt_secret.get_secret_value()
    algorithm = "HS256"
    if kind == "expired": claims["exp"] = now - timedelta(seconds=1)
    if kind == "missing_claim": del claims["iat"]
    if kind == "future_iat": claims["iat"] = now + timedelta(hours=1)
    if kind == "unknown_user": claims["sub"] = "missing"
    if kind == "wrong_algorithm": algorithm = "HS384"
    if kind == "locked": world.users.rows[0]["status"] = "locked"
    token = jwt.encode(claims, secret if kind != "tampered" else "different-test-secret-that-is-also-long", algorithm=algorithm)
    response = world.client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_login_throttle_and_expiry(world, monkeypatch):
    from app.auth import throttle
    now = [1000.0]
    monkeypatch.setattr(throttle.time, "monotonic", lambda: now[0])
    for _ in range(5):
        assert world.client.post("/auth/login", data={"username": "a", "password": "wrong"}).status_code == 401
    assert world.client.post("/auth/login", data={"username": "A", "password": "fake-password"}).status_code == 429
    assert world.client.post("/auth/login", data={"username": "b", "password": "fake-password"}).status_code == 200
    now[0] += 901
    assert world.client.post("/auth/login", data={"username": "a", "password": "fake-password"}).status_code == 200


@pytest.mark.parametrize("method,path,body", [
    ("get", "/auth/me", None), ("get", "/sessions", None), ("post", "/sessions", None),
    ("get", "/sessions/foreign/history", None), ("delete", "/sessions/foreign", None),
    ("post", "/chat", {"message": "hello", "session_id": "foreign"}),
    ("get", "/sessions/foreign/attachments", None), ("post", "/sessions/foreign/attachments", None),
])
def test_all_api_routes_require_login(world, method, path, body):
    assert world.client.request(method, path, json=body).status_code == 401


def test_public_documentation_and_health(world):
    for path in ("/docs", "/redoc", "/openapi.json", "/health"):
        assert world.client.get(path).status_code == 200


@pytest.mark.parametrize("stream", [False, True])
def test_session_ownership_chat_and_delete(world, stream):
    from uuid import UUID
    a = world.headers("a")
    b = world.headers("b")
    response = world.client.post("/sessions", headers=a)
    assert response.status_code == 201
    sid = response.json()["session_id"]
    UUID(sid)
    assert world.client.get("/sessions", headers=b).json() == []
    assert len(world.client.get("/sessions", headers=a).json()) == 1
    for foreign in (sid, "missing"):
        assert world.client.get(f"/sessions/{foreign}/history", headers=b).status_code == 404
        assert world.client.delete(f"/sessions/{foreign}", headers=b).status_code == 404
        assert world.client.post("/chat", headers=b, json={"message":"Hello", "session_id":foreign, "stream":stream}).status_code == 404
    result = world.client.post("/chat", headers=a, json={"message":"Hello", "session_id":sid, "stream":stream})
    assert result.status_code == 200
    history = world.client.get(f"/sessions/{sid}/history", headers=a).json()
    assert [m["role"] for m in history] == ["user", "assistant"]
    assert all(m["user_id"] == "a" for m in history)
    assert world.sessions.rows[0]["title"] == "Hello"
    assert world.logs.rows[-1]["event"] == "chat"
    assert world.logs.rows[-1]["user_id"] == "a"
    deleted = world.client.delete(f"/sessions/{sid}", headers=a)
    assert deleted.json()["messages_deleted"] == 2
    assert world.messages.rows == [] and world.sessions.rows == []


def test_missing_session_cannot_create_implicit_chat(world):
    for body in ({"message":"Hello"}, {"message":"Hello", "session_id":""}):
        assert world.client.post("/chat", headers=world.headers("a"), json=body).status_code == 422


def test_message_reads_filter_owner_even_for_mislabelled_rows(world):
    sid = asyncio.run(memory.create_session("a"))["session_id"]
    now = datetime.now(timezone.utc)
    world.messages.rows.extend([
        {"session_id": sid, "user_id":"a", "role":"user", "content":"own", "sources":[], "created_at":now},
        {"session_id": sid, "user_id":"b", "role":"user", "content":"other", "sources":[], "created_at":now},
    ])
    assert [r["content"] for r in asyncio.run(memory.get_recent(sid, 6, user_id="a"))] == ["own"]
    assert [r["content"] for r in asyncio.run(memory.get_full_history(sid, user_id="a"))] == ["own"]
    assert all(q.get("user_id") == "a" for q in world.messages.queries)


def test_sweep_only_old_processing_documents(world):
    now = datetime.now(timezone.utc)
    world.docs.rows = [
        {"status":"processing", "created_at":now - timedelta(minutes=16)},
        {"status":"processing", "created_at":now - timedelta(minutes=14)},
        {"status":"ready", "created_at":now - timedelta(days=1)},
    ]
    assert asyncio.run(maintenance.sweep_stuck_documents()) == 1
    assert [r["status"] for r in world.docs.rows] == ["failed", "processing", "ready"]


def test_cors_restricts_origins(world):
    allowed = world.client.options("/sessions", headers={"Origin":"http://localhost:8000", "Access-Control-Request-Method":"GET"})
    denied = world.client.options("/sessions", headers={"Origin":"https://untrusted.invalid", "Access-Control-Request-Method":"GET"})
    assert allowed.headers["access-control-allow-origin"] == "http://localhost:8000"
    assert "access-control-allow-origin" not in denied.headers


def test_seed_is_idempotent_and_stores_hashes(world, monkeypatch):
    async def noop(): pass
    for name in ("close_mongo", "connect_to_mongo", "ensure_indexes"):
        monkeypatch.setattr(sys.modules["app.db"], name, noop, raising=False)
    from scripts import seed_users
    col = Collection()
    monkeypatch.setattr(seed_users, "users_col", lambda: col)
    asyncio.run(seed_users.seed_users())
    first = copy.deepcopy(col.rows)
    asyncio.run(seed_users.seed_users())
    assert col.rows == first and len(first) == 3
    assert {r["customer_id"] for r in first} == {"demo-customer-001", "demo-customer-002", "demo-customer-003"}
    for row in first:
        assert row["password_hash"].startswith("$argon2")
        assert "password" not in row


def test_audit_is_content_free(world):
    for event in ("login_ok", "login_fail", "chat"):
        asyncio.run(audit.audit_event(event, user_id="a", session_id="s"))
    assert all(set(row) == {"ts", "event", "user_id", "session_id", "tool", "outcome", "detail", "latency_ms"} for row in world.logs.rows)
    assert all(row["detail"] == "" for row in world.logs.rows)

@pytest.mark.parametrize("identity_field", ["user_id", "customer_id", "account_id"])
def test_request_cannot_supply_identity(world, identity_field):
    sid = world.client.post("/sessions", headers=world.headers("a")).json()["session_id"]
    response = world.client.post("/chat", headers=world.headers("a"),
        json={"message":"Hello", "session_id":sid, identity_field:"b"})
    assert response.status_code == 422
    assert world.messages.rows == []


def test_identity_loaded_from_user_record_not_extra_token_claims(world):
    token = jwt.encode({"sub":"a", "customer_id":"customer-b", "user_id":"b",
        "iat":datetime.now(timezone.utc), "exp":datetime.now(timezone.utc)+timedelta(minutes=10)},
        get_settings().jwt_secret.get_secret_value(), algorithm="HS256")
    user = asyncio.run(security.get_current_user(token))
    assert user.user_id == "a" and user.customer_id == "customer-a"


def test_startup_sweep_and_cleanup(world, monkeypatch):
    from app import main
    calls = []
    async def connect(): calls.append("connect")
    async def indexes(): calls.append("indexes")
    async def sweep(): calls.append("sweep")
    async def close(): calls.append("close")
    monkeypatch.setattr(main, "connect_to_mongo", connect)
    monkeypatch.setattr(main, "ensure_indexes", indexes)
    monkeypatch.setattr(main, "sweep_stuck_documents", sweep)
    monkeypatch.setattr(main, "close_mongo", close)
    with TestClient(main.app):
        assert calls == ["connect", "indexes", "sweep"]
    assert calls == ["connect", "indexes", "sweep", "close"]


def test_indexes_include_users_and_owned_sessions(world, monkeypatch):
    # Load the real index module separately from legacy tests' app.db stub.
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("phase2_real_db", Path(__file__).parents[1]/"app"/"db.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    indexes = {}
    class IndexCollection:
        def __init__(self, name): self.name = name
        async def create_index(self, fields, **kwargs):
            indexes.setdefault(self.name, []).append((fields, kwargs))
        async def list_search_indexes(self, name): return Cursor([{"status":"READY"}])
    class Database:
        def __init__(self): self.created = []
        async def list_collection_names(self): return []
        async def create_collection(self, name): self.created.append(name)
        def __getitem__(self, name): return IndexCollection(name)
    db = Database()
    monkeypatch.setattr(module, "get_db", lambda: db)
    asyncio.run(module.ensure_indexes())
    assert {"users", "sessions", "audit_log"} <= set(db.created)
    assert ([("username",1)], {"unique":True}) in indexes["users"]
    assert ([("user_id",1),("updated_at",-1)], {}) in indexes["sessions"]
