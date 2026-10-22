"""Central configuration. Values come from environment variables / the .env file."""
from functools import lru_cache

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore", hide_input_in_errors=True)

    jwt_secret: SecretStr
    access_token_minutes: int = Field(default=30, gt=0)
    cors_origins: str = "http://127.0.0.1:8000,http://localhost:8000"

    @field_validator("jwt_secret")
    @classmethod
    def validate_jwt_secret(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value()) < 32:
            raise ValueError("JWT_SECRET must contain at least 32 characters")
        return value

    @field_validator("cors_origins")
    @classmethod
    def validate_origins(cls, value: str) -> str:
        if "*" in value:
            raise ValueError("CORS_ORIGINS must contain explicit origins")
        return value

    @property
    def allowed_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    # --- MongoDB Atlas ---
    mongodb_uri: str
    mongodb_db_name: str = "rag_db"
    bank_db_name: str = "bank_db"
    bank_mongodb_uri: str = ""
    router_history_messages: int = Field(default=4, gt=0)
    router_message_chars: int = Field(default=300, gt=0)

    # --- Groq (chat LLM) ---
    groq_api_key: str
    groq_model: str = "llama-3.3-70b-versatile"
    groq_rewrite_model: str = "llama-3.1-8b-instant"  # small/fast model for rewriting follow-ups

    # --- Gemini (embeddings only) ---
    gemini_api_key: str
    embed_model: str = "gemini-embedding-001"
    embed_dim: int = 768  # must match the Atlas vector index numDimensions

    # --- RAG tuning ---
    chunk_size: int = 800          # characters, for prose (PDF/DOCX/TXT/MD text, long slides)
    chunk_overlap: int = 120
    top_k: int = 5
    history_max_messages: int = Field(default=12, ge=0)
    history_token_budget: int = Field(default=1500, ge=0)
    old_answer_trim_chars: int = Field(default=600, gt=0)
    max_message_chars: int = Field(default=2000, gt=0)
    max_passage_chars: int = Field(default=5000, ge=0)
    answer_max_tokens: int = Field(default=700, gt=0)
    prompt_token_ceiling: int = Field(default=5000, gt=0)
    llm_max_concurrency: int = Field(default=2, gt=0)
    chat_rate_per_minute: int = Field(default=6, gt=0)
    max_upload_mb: int = 20

    retention_days: int = Field(default=30, ge=0)
    attachment_wait_seconds: float = Field(default=60, ge=0)
    user_upload_max_mb: int = Field(default=5, gt=0)
    user_max_chunks_per_doc: int = Field(default=150, gt=0)
    user_max_table_rows: int = Field(default=500, gt=0)

    # --- Tables / spreadsheets / slides ---
    table_chunk_chars: int = 1800   # a table chunk holds whole rows up to about this size
    max_table_rows: int = 5000      # rows per table that are made searchable (stats cover all rows)
    max_chunks_per_doc: int = 1500  # safety cap so one file cannot exhaust the free embedding quota

    # --- Chat behaviour ---
    answer_temperature: float = 0.2   # higher = more natural/creative; lower = more literal
    min_doc_score: float = 0.60       # Atlas cosine score (0-1) below which a passage is not shown to the model

    # --- Atlas vector index ---
    vector_index_name: str = "chunks_vector_index"


@lru_cache
def get_settings() -> Settings:
    return Settings()
