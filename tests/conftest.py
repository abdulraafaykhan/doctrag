"""Never load local secrets, including during test collection."""
import os
from app.config import Settings, get_settings
Settings.model_config["env_file"] = None
os.environ.update(MONGODB_URI="mongodb://localhost:27017", BANK_MONGODB_URI="", GROQ_API_KEY="test", GEMINI_API_KEY="test", JWT_SECRET="test-only-secret-with-at-least-32-characters")
get_settings.cache_clear()


import pytest

@pytest.fixture(autouse=True)
def fresh_chat_rate_state(monkeypatch):
    from app.routers import chat
    monkeypatch.setattr(chat, "chat_windows", {})
    # Legacy tests replace app.db at collection time. Supply lifecycle stubs so
    # individual phase suites can import main without relying on test order.
    import sys
    db = sys.modules["app.db"]
    async def noop(): pass
    async def ready(): return "READY"
    if not getattr(db, "__file__", None):
        for name in ("connect_to_mongo", "close_mongo", "ensure_indexes"):
            if not hasattr(db, name):
                monkeypatch.setattr(db, name, noop, raising=False)
        if not hasattr(db, "vector_index_status"):
            monkeypatch.setattr(db, "vector_index_status", ready, raising=False)
