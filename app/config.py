"""Validated, cached configuration; credentials never enter printable settings."""

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[1]


class Secrets(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")
    groq_api_key: SecretStr = Field(min_length=1)
    gemini_api_key: SecretStr = Field(min_length=1)
    mongodb_uri: SecretStr = Field(min_length=1)
    log_level: str | None = None


class Models(BaseModel):
    agent: str = Field(min_length=1)
    agent_fallbacks: list[str] = Field(min_length=1)
    vlm_ingest: str = Field(min_length=1)
    vlm_ingest_fallbacks: list[str]
    vlm_online: str = Field(min_length=1)
    vlm_online_fallbacks: list[str]
    embed: str = Field(min_length=1)
    embed_dims: int = Field(gt=0)
    temperature: float
    reranker: str


class Quotas(BaseModel):
    llm_requests_per_day: int = Field(gt=0)
    embed_requests_per_day: int = Field(gt=0)
    vlm_requests_per_day: int = Field(gt=0)
    embed_reserved_for_queries: int = Field(ge=0)
    embed_pace_per_minute: int = Field(gt=0)
    upload_guard_percent: int = Field(gt=0, le=100)


class Database(BaseModel):
    name: str = Field(min_length=1)
    timeout_ms: int = Field(gt=0)
    max_pool_size: int = Field(gt=0)
    index_poll_seconds: float = Field(gt=0)
    index_wait_seconds: float = Field(gt=0)
    vector_index: str = Field(min_length=1)


class Logging(BaseModel):
    level: str
    file: str
    mask_pii: bool
    max_bytes: int = Field(gt=0)
    backup_count: int = Field(gt=0)
    message_chars: int = Field(gt=0)
    slow_query_ms: int = Field(gt=0)


class LLMOptions(BaseModel):
    timeout_seconds: float = Field(gt=0)
    vlm_ingest_timeout_seconds: float = Field(gt=0)
    vlm_online_timeout_seconds: float = Field(gt=0)
    vision_json_retries: int = Field(ge=0)
    retry_attempts: int = Field(gt=0)
    retry_wait_seconds: float = Field(ge=0)


class Settings(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    secrets: Secrets = Field(exclude=True, repr=False)
    models: Models
    quotas: Quotas
    database: Database
    logging: Logging
    llm: LLMOptions
    bank: dict[str, Any]
    features: dict[str, Any]
    auth: dict[str, Any]
    session: dict[str, Any]
    agent: dict[str, Any]
    flow: dict[str, Any]
    uploads: dict[str, Any]
    chunking: dict[str, Any]
    ocr: dict[str, Any]
    retrieval: dict[str, Any]
    statement: dict[str, Any]
    formats: dict[str, Any]
    retention: dict[str, Any]
    money: dict[str, Any]
    seed: dict[str, Any]
    knowledge: dict[str, Any]
    conversation: dict[str, Any]
    flows: dict[str, Any]
    messages: dict[str, Any]
    products: dict[str, Any]
    prompts: dict[str, Any]


def _yaml(path: Path) -> dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a YAML mapping: {path.name}")
    return value


def load_settings(root: Path = ROOT, secrets: Secrets | None = None) -> Settings:
    """Load an alternate root for tests without changing the process environment."""
    try:
        credentials = (
            secrets if secrets is not None else Secrets(_env_file=root / ".env")
        )
        values = _yaml(root / "config/settings.yaml")
        prompts = {}
        for path in sorted((root / "prompts").iterdir()):
            if path.is_file():
                prompts[path.stem] = (
                    _yaml(path)
                    if path.suffix == ".yaml"
                    else path.read_text(encoding="utf-8")
                )
        for name in (
            "agent_system",
            "agent_blocks",
            "repair",
            "vlm_screenshot",
            "vlm_table",
        ):
            if name not in prompts or not prompts[name]:
                raise ValueError(f"Missing prompt: {name}")
        settings = Settings(
            **values,
            secrets=credentials,
            prompts=prompts,
            messages=_yaml(root / "config/messages.yaml"),
            products=_yaml(root / "config/products.yaml"),
            knowledge=_yaml(root / "config/knowledge.yaml"),
            conversation=_yaml(root / "config/conversation.yaml"),
            flows={path.stem: _yaml(path) for path in sorted((root / "config/flows").glob("*.yaml"))},
        )
        if credentials.log_level:
            settings.logging.level = credentials.log_level
        return settings
    except ValidationError as exc:
        # Never include validation input: a bad credential may be in that input.
        fields = ", ".join(".".join(map(str, error["loc"])) for error in exc.errors())
        raise ValueError(f"Missing or invalid configuration key(s): {fields}") from None
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"Cannot load configuration ({type(exc).__name__})") from None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return load_settings()
