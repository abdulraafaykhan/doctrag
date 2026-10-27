"""Two-level embedding cache and paced, normalized document/query embeddings."""

import json
import math
from datetime import timedelta
from threading import Lock
from time import monotonic, sleep

from tenacity import Retrying, retry_if_exception, stop_after_attempt, wait_exponential

from app.config import ROOT, get_settings
from app.ingest.models import digest, utcnow
from app.llm import FakeLLM, LLMClient, _retryable
from app.log import log, mask_text

_pace_lock = Lock()
_next_request = 0.0


class Embeddings:
    def __init__(self, store, llm=None, *, pace=True):
        self.store, self.llm, self.pace = store, llm, pace

    def embed(self, texts: list[str], *, query=False, upload=False):
        global _next_request
        cfg = get_settings()
        results, pending = [], {}
        for text in texts:
            safe = mask_text(text)
            # Query and document task types have separate cache keys.
            key = f"{cfg.models.embed}|{cfg.models.embed_dims}|{'query' if query else 'document'}|{digest(safe)}"
            if isinstance(self.llm, FakeLLM):
                key = "fake|" + key
            cached = self.store.one("embed_cache", {"key": key})
            disk = ROOT / "data/test_cache" / f"embedding_{digest(key)}.json"
            if cached and cached.get("expires_at", utcnow()) > utcnow():
                vector = cached["embedding"]
            elif disk.exists():
                vector = json.loads(disk.read_text())
            else:
                vector = None
                pending.setdefault(key, (safe, disk))
            results.append((key, vector))
        missing = list(pending)
        size = cfg.knowledge["embedding"]["batch_size"]
        new = {}
        llm = self.llm or (LLMClient() if missing else None)
        try:
            for start in range(0, len(missing), size):
                keys = missing[start : start + size]
                if self.pace and not query:
                    with _pace_lock:
                        delay = max(0, _next_request - monotonic())
                        if delay:
                            sleep(delay)
                        _next_request = (
                            monotonic()
                            + len(keys) * 60 / cfg.quotas.embed_pace_per_minute
                        )
                retry = cfg.knowledge["embedding"]
                for attempt in Retrying(
                    stop=stop_after_attempt(retry["retry_attempts"]),
                    wait=wait_exponential(multiplier=retry["retry_backoff_seconds"]),
                    retry=retry_if_exception(_retryable),
                    reraise=True,
                ):
                    with attempt:
                        vectors = llm.embed(
                            [pending[key][0] for key in keys],
                            query=query,
                            upload=upload,
                        )
                for key, vector in zip(keys, vectors, strict=True):
                    norm = math.sqrt(sum(value * value for value in vector))
                    if (
                        len(vector) != cfg.models.embed_dims
                        or not norm
                        or not math.isfinite(norm)
                    ):
                        raise ValueError("Invalid embedding response")
                    vector = [value / norm for value in vector]
                    new[key] = vector
                    pending[key][1].parent.mkdir(parents=True, exist_ok=True)
                    pending[key][1].write_text(json.dumps(vector), encoding="utf-8")
                log.step("EMBED", "batch cached", count=len(keys), query=query)
        finally:
            if self.llm is None and llm is not None:
                llm.close()
        for key, old in results:
            vector = old if old is not None else new[key]
            self.store.put(
                "embed_cache",
                {"key": key},
                {
                    "key": key,
                    "embedding": vector,
                    "created_at": utcnow(),
                    "expires_at": utcnow()
                    + timedelta(days=cfg.retention["embed_cache_days"]),
                },
            )
        cap = cfg.retention["embed_cache_max"]
        excess = self.store.count("embed_cache", {}) - cap
        if excess > 0:
            for value in self.store.find(
                "embed_cache", {}, sort=[("created_at", 1)], limit=excess
            ):
                self.store.delete("embed_cache", {"key": value["key"]})
        return [old if old is not None else new[key] for key, old in results]
