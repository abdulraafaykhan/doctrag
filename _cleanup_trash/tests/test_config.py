import shutil

import pytest
import yaml
from pydantic import ValidationError

from app.config import ROOT, Secrets, get_settings, load_settings


@pytest.fixture
def config_root(tmp_path):
    for name in ("config", "prompts"):
        shutil.copytree(ROOT / name, tmp_path / name)
    return tmp_path


def test_load_and_cache():
    first = get_settings()
    assert first is get_settings()
    assert first.prompts["agent_system"]
    assert first.models.embed_dims > 0
    assert "unit-test-placeholder" not in repr(first)
    assert "secrets" not in first.model_dump()


def test_missing_key_clear_without_secret(config_root):
    path = config_root / "config/settings.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    del data["models"]["embed"]
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(ValueError, match="models.embed") as caught:
        load_settings(config_root)
    assert "unit-test-placeholder" not in str(caught.value)


def test_missing_secret(config_root, monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY")
    with pytest.raises(ValueError, match="groq_api_key"):
        load_settings(config_root)


def test_empty_secret(config_root):
    with pytest.raises(ValidationError):
        Secrets(
            groq_api_key="", gemini_api_key="test", mongodb_uri="test", _env_file=None
        )


def test_missing_prompt(config_root):
    (config_root / "prompts/repair.txt").unlink()
    with pytest.raises(ValueError, match="Missing prompt: repair"):
        load_settings(config_root)
