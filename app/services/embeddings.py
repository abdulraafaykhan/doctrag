"""Gemini embeddings (async, batched, with retry on rate limits)."""
import asyncio
import math
from functools import lru_cache

from google import genai
from google.genai import errors, types

from app.config import get_settings

BATCH_SIZE = 50
MAX_RETRIES = 5


@lru_cache
def _client() -> genai.Client:
    return genai.Client(api_key=get_settings().gemini_api_key)


def _normalize(vec: list[float]) -> list[float]:
    # Gemini only pre-normalises the full 3072-dim output. For smaller sizes
    # (we use 768) we normalise ourselves so cosine similarity is accurate.
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


async def _embed_batch(texts: list[str], task_type: str) -> list[list[float]]:
    s = get_settings()
    config = types.EmbedContentConfig(task_type=task_type, output_dimensionality=s.embed_dim)
    for attempt in range(MAX_RETRIES):
        try:
            resp = await _client().aio.models.embed_content(
                model=s.embed_model, contents=texts, config=config
            )
            return [_normalize(list(e.values)) for e in resp.embeddings]
        except errors.APIError as e:
            retryable = getattr(e, "code", None) in (429, 500, 503)
            if not retryable or attempt == MAX_RETRIES - 1:
                raise
            await asyncio.sleep(2 ** attempt * 2)  # 2s, 4s, 8s, 16s
    raise RuntimeError("unreachable")


async def embed_documents(texts: list[str]) -> list[list[float]]:
    """Embed chunk texts (task type RETRIEVAL_DOCUMENT), in batches."""
    vectors: list[list[float]] = []
    for i in range(0, len(texts), BATCH_SIZE):
        vectors.extend(await _embed_batch(texts[i : i + BATCH_SIZE], "RETRIEVAL_DOCUMENT"))
    return vectors


async def embed_query(text: str) -> list[float]:
    """Embed a user question (task type RETRIEVAL_QUERY)."""
    return (await _embed_batch([text], "RETRIEVAL_QUERY"))[0]