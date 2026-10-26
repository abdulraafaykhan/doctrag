import pytest

from app.config import get_settings


@pytest.fixture(autouse=True)
def isolated_credentials(monkeypatch):
    # Unit tests must never use live credentials or contact provider services.
    for name in ("GROQ_API_KEY", "GEMINI_API_KEY", "MONGODB_URI"):
        monkeypatch.setenv(name, "unit-test-placeholder")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
