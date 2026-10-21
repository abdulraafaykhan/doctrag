"""Central configuration. Values come from environment variables / the .env file."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- MongoDB Atlas ---
    mongodb_uri: str
    mongodb_db_name: str = "rag_db"

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
    history_turns: int = 6  # how many past messages are sent back to the LLM
    max_upload_mb: int = 20

    # --- Tables / spreadsheets / slides ---
    table_chunk_chars: int = 1800   # a table chunk holds whole rows up to about this size
    max_table_rows: int = 5000      # rows per table that are made searchable (stats cover all rows)
    max_chunks_per_doc: int = 1500  # safety cap so one file cannot exhaust the free embedding quota

    # --- Chat behaviour ---
    answer_temperature: float = 0.4   # higher = more natural/creative; lower = more literal
    min_doc_score: float = 0.60       # Atlas cosine score (0-1) below which a passage is not shown to the model

    # --- Web access ---
    web_enabled: bool = True
    web_max_pages: int = 3            # pages read per question
    web_search_results: int = 5
    web_fetch_timeout: float = 12.0   # seconds per page
    web_max_bytes: int = 2_000_000    # per page download cap
    web_context_chars: int = 7000     # how much web text goes into one prompt (keeps Groq token limits happy)
    tavily_api_key: str = ""          # optional: free key from tavily.com, more reliable than DuckDuckGo

    # --- Atlas vector index ---
    vector_index_name: str = "chunks_vector_index"


@lru_cache
def get_settings() -> Settings:
    return Settings()