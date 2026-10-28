"""Provider calls, fallback, PII boundary and atomic daily quota reservation."""

import base64
import json
import math
from datetime import datetime, timezone
from typing import Any, Literal
from zoneinfo import ZoneInfo

import groq
import httpx
from google import genai
from google.genai import errors, types
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError
from tenacity import Retrying, retry_if_exception, stop_after_attempt, wait_fixed

from app.config import get_settings
from app.db import collection
from app.log import log, mask_text, mask_payload
from app.vision import parse_vision_json, postprocess_vision
from app.usage import meter, session_context


def record_usage(result=None, model='scripted', kind='llm', *, cached=False):
    usage = getattr(result, 'usage', None) or getattr(result, 'usage_metadata', None)
    incoming = getattr(usage, 'prompt_tokens', None) or getattr(usage, 'prompt_token_count', 0)
    outgoing = getattr(usage, 'completion_tokens', None)
    if outgoing is None:
        outgoing = (getattr(usage, 'candidates_token_count', 0) or 0) + (getattr(usage, 'thoughts_token_count', 0) or 0)
    counted = meter.add(session_context.get(), model, kind, incoming, outgoing, cached=cached)
    log.step('VLM' if kind == 'vlm' else 'LLM', 'token usage', model=model, **counted)
    return counted


class QuotaExceeded(RuntimeError):
    pass


def pacific_day(now: datetime | None = None) -> str:
    return (
        (now or datetime.now(timezone.utc))
        .astimezone(ZoneInfo("America/Los_Angeles"))
        .date()
        .isoformat()
    )


class QuotaGuard:
    def __init__(self, store=None, settings=None) -> None:
        self.settings = settings or get_settings()
        self.store = store if store is not None else collection("quota_usage")

    def reserve(
        self,
        kind: str,
        *,
        upload: bool = False,
        now: datetime | None = None,
        count: int = 1,
    ) -> int:
        if kind not in {"embed", "vlm", "llm"}:
            raise ValueError("Unknown quota kind")
        if type(count) is not int or count <= 0:
            raise ValueError("Quota count must be a positive integer")
        cfg = self.settings.quotas
        field = f"{kind}_requests"
        limit = getattr(cfg, f"{kind}_requests_per_day")
        if kind == "embed" and upload:
            limit = min(
                limit * cfg.upload_guard_percent // 100,
                limit - cfg.embed_reserved_for_queries,
            )
        day = pacific_day(now)
        stamp = now or datetime.now(timezone.utc)
        # The unique day index plus conditional increment prevents overspending across workers.
        with log.span("DB", "initialize quota", day=day):
            try:
                self.store.update_one(
                    {"day": day},
                    {
                        "$setOnInsert": {
                            "day": day,
                            "embed_requests": 0,
                            "vlm_requests": 0,
                            "llm_requests": 0,
                        }
                    },
                    upsert=True,
                )
            except DuplicateKeyError:
                pass
        with log.span("DB", "reserve quota", kind=kind):
            result = self.store.find_one_and_update(
                {"day": day, field: {"$lt": limit - count + 1}},
                {"$inc": {field: count}, "$set": {"updated_at": stamp}},
                return_document=ReturnDocument.AFTER,
            )
        if result is None:
            log.step(
                "LLM" if kind == "llm" else kind.upper(), "quota exhausted", kind=kind
            )
            raise QuotaExceeded(f"{kind} request budget exhausted")
        log.step(
            "EMBED" if kind == "embed" else "LLM" if kind == "llm" else "VLM",
            "quota reserved",
            used=result[field],
            budget=limit,
        )
        return result[field]

    def remaining(self) -> dict[str, int]:
        with log.span("DB", "quota remaining"):
            usage = self.store.find_one({"day": pacific_day()}) or {}
        return {
            kind: max(
                0,
                getattr(self.settings.quotas, f"{kind}_requests_per_day")
                - usage.get(f"{kind}_requests", 0),
            )
            for kind in ("embed", "vlm", "llm")
        }


def _retryable(exc: BaseException) -> bool:
    return (
        isinstance(exc, (groq.APITimeoutError, TimeoutError))
        or getattr(exc, "status_code", None) == 429
        or getattr(exc, "code", None) == 429
        or type(exc).__name__ in {"ReadTimeout", "ConnectTimeout"}
    )


def _vision_fallback_error(exc: Exception) -> bool:
    status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    return (
        status == 429
        or isinstance(status, int)
        and 500 <= status < 600
        or isinstance(exc, (groq.APITimeoutError, httpx.TimeoutException, TimeoutError))
    )


class LLMClient:
    def __init__(self, quota: QuotaGuard | None = None) -> None:
        self.settings = get_settings()
        self.quota = quota or QuotaGuard()
        self._groq = groq.Groq(
            api_key=self.settings.secrets.groq_api_key.get_secret_value(),
            timeout=self.settings.llm.timeout_seconds,
            max_retries=0,
        )
        self._gemini = genai.Client(
            api_key=self.settings.secrets.gemini_api_key.get_secret_value(),
            http_options=self._http_options(),
        )

    def _http_options(self, timeout_seconds: float | None = None) -> types.HttpOptions:
        # Per-call options keep all Gemini operations on the same configured timeout.
        return types.HttpOptions(
            timeout=int(
                (
                    timeout_seconds
                    if timeout_seconds is not None
                    else self.settings.llm.timeout_seconds
                )
                * 1000
            ),
            retry_options=types.HttpRetryOptions(attempts=1),
        )

    def _attempt(
        self,
        kind: str,
        model: str,
        purpose: str,
        function,
        *,
        upload: bool = False,
        count: int = 1,
        retry: bool = True,
    ):
        cfg = self.settings.llm

        def before_sleep(state):
            log.step(
                "LLM",
                "retry",
                model=model,
                error=type(state.outcome.exception()).__name__,
                attempt=state.attempt_number,
            )

        retry = Retrying(
            stop=stop_after_attempt(cfg.retry_attempts if retry else 1),
            wait=wait_fixed(cfg.retry_wait_seconds),
            retry=retry_if_exception(_retryable),
            reraise=True,
            before_sleep=before_sleep,
        )
        for attempt in retry:
            with attempt:
                self.quota.reserve(kind, upload=upload, count=count)
                with log.span(
                    {"llm": "LLM", "embed": "EMBED", "vlm": "VLM"}[kind],
                    "provider call",
                    provider="groq" if model.startswith("groq/") else "gemini",
                    model=model,
                    purpose=purpose,
                ):
                    return function()

    def chat(self, messages: list[dict[str, str]], purpose: str = "answer") -> str:
        masked = [
            {"role": item["role"], "content": mask_text(item["content"])}
            for item in messages
        ]
        chain = [self.settings.models.agent, *self.settings.models.agent_fallbacks]
        for position, model in enumerate(chain):
            try:
                return self._attempt(
                    "llm",
                    model,
                    purpose,
                    lambda selected=model: self._chat(selected, masked),
                )
            except (groq.APIError, errors.APIError, TimeoutError, QuotaExceeded) as exc:
                log.step(
                    "LLM",
                    "fallback",
                    model=model,
                    next_model=chain[position + 1]
                    if position + 1 < len(chain)
                    else "safe message",
                    error=type(exc).__name__,
                )
            except Exception as exc:
                if not _retryable(exc):
                    raise
                log.step("LLM", "fallback", model=model, error=type(exc).__name__)
        return self.settings.messages["answers"]["retry_later"]

    def _chat(self, model: str, messages: list[dict[str, str]]) -> str:
        provider, model_id = model.split("/", 1)
        if provider == "groq":
            result = self._groq.chat.completions.create(
                model=model_id,
                messages=messages,
                temperature=self.settings.models.temperature,
                timeout=self.settings.llm.timeout_seconds,
            )
            self._usage(result, model, 'llm')
            return result.choices[0].message.content or ""
        if provider != "gemini":
            raise ValueError("Unsupported configured chat provider")
        system = "\n".join(
            item["content"] for item in messages if item["role"] == "system"
        )
        contents = [
            types.Content(
                role="model" if item["role"] == "assistant" else "user",
                parts=[types.Part.from_text(text=item["content"])],
            )
            for item in messages
            if item["role"] != "system"
        ]
        result = self._gemini.models.generate_content(
            model=model_id,
            contents=contents,
            config=types.GenerateContentConfig(
                http_options=self._http_options(),
                system_instruction=system or None,
                temperature=self.settings.models.temperature,
            ),
        )
        self._usage(result, model, 'llm')
        return result.text or ""

    def _usage(self, result, model='scripted', kind='llm') -> None:
        record_usage(result, model, kind)

    def vision(
        self,
        image: bytes,
        mime_type: str,
        prompt: str,
        *,
        mode: Literal["ingest", "online"],
        pii_redacted: bool = False,
        local_ocr: str = "",
        cut_off_rows: tuple[int, ...] = (),
    ) -> dict[str, Any]:
        # Raw images cannot be regex-masked: the caller must redact PII before upload.
        if not pii_redacted:
            raise ValueError(
                "Vision images must be locally redacted before any model call"
            )
        if mode not in {"ingest", "online"}:
            raise ValueError("Vision mode must be ingest or online")
        if any(type(index) is not int or index < 0 for index in cut_off_rows):
            raise ValueError("Cut-off rows must be zero-based nonnegative indices")
        if not self.settings.features["vlm"]:
            return {
                "text": mask_text(local_ocr),
                "confidence": "low",
                "warning": self.settings.messages["upload"]["vision_unavailable"],
                "structured": None,
                "status": "unreadable",
            }
        chain = [
            getattr(self.settings.models, f"vlm_{mode}"),
            *getattr(self.settings.models, f"vlm_{mode}_fallbacks"),
        ]
        timeout = getattr(self.settings.llm, f"vlm_{mode}_timeout_seconds")
        for position, model in enumerate(chain):
            try:
                # Switch providers immediately instead of spending another timeout on the same model.
                for attempt in range(self.settings.llm.vision_json_retries + 1):
                    text = self._attempt(
                        "vlm",
                        model,
                        f"vision_{mode}",
                        lambda selected=model: self._vision(
                            selected, image, mime_type, mask_text(prompt), timeout
                        ),
                        retry=False,
                    )
                    try:
                        parsed = parse_vision_json(text)
                    except ValueError:
                        log.step(
                            "VLM",
                            "invalid JSON",
                            model=model,
                            mode=mode,
                            attempt=attempt + 1,
                        )
                        if attempt < self.settings.llm.vision_json_retries:
                            continue
                        return {
                            "text": mask_text(local_ocr),
                            "structured": None,
                            "status": "unreadable",
                            "confidence": "low",
                            "warning": self.settings.messages["upload"][
                                "unreadable_image"
                            ],
                        }
                    processed = postprocess_vision(
                        parsed, local_ocr, cut_off_rows=cut_off_rows
                    )
                    return {
                        "text": json.dumps(processed, ensure_ascii=False),
                        "raw_text": mask_text(text),
                        "raw_structured": mask_payload(parsed),
                        "model": model,
                        "structured": processed,
                        "status": "readable",
                        "confidence": "model",
                        "warning": None,
                    }
            except QuotaExceeded:
                break
            except Exception as exc:
                if not _vision_fallback_error(exc):
                    raise
                log.step(
                    "VLM",
                    "fallback",
                    model=model,
                    mode=mode,
                    next_model=chain[position + 1]
                    if position + 1 < len(chain)
                    else "local OCR",
                    error=type(exc).__name__,
                    status=getattr(exc, "status_code", None)
                    or getattr(exc, "code", None),
                )
        return {
            "text": mask_text(local_ocr),
            "confidence": "low",
            "warning": self.settings.messages["upload"]["vision_unavailable"],
            "structured": None,
            "status": "unreadable",
            "needs_retry": True,
        }

    def _vision(
        self, model: str, image: bytes, mime_type: str, prompt: str, timeout: float
    ) -> str:
        if model.startswith("groq/"):
            encoded = base64.b64encode(image).decode("ascii")
            result = self._groq.chat.completions.create(
                model=model.removeprefix("groq/"),
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:{mime_type};base64,{encoded}"
                                },
                            },
                        ],
                    }
                ],
                timeout=timeout,
                response_format={"type": "json_object"},
            )
            self._usage(result, model, 'vlm')
            return result.choices[0].message.content or ""
        result = self._gemini.models.generate_content(
            model=model.removeprefix("gemini/"),
            contents=[prompt, types.Part.from_bytes(data=image, mime_type=mime_type)],
            config=types.GenerateContentConfig(
                http_options=self._http_options(timeout),
                response_mime_type="application/json",
            ),
        )
        self._usage(result, model, 'vlm')
        return result.text or ""

    def embed(
        self, texts: list[str], *, upload: bool = False, query: bool = False
    ) -> list[list[float]]:
        if not texts:
            return []
        result = self._attempt(
            "embed",
            self.settings.models.embed,
            "embedding",
            lambda: self._gemini.models.embed_content(
                model=self.settings.models.embed,
                contents=[mask_text(text) for text in texts],
                config=types.EmbedContentConfig(
                    http_options=self._http_options(),
                    output_dimensionality=self.settings.models.embed_dims,
                    task_type="RETRIEVAL_QUERY" if query else "RETRIEVAL_DOCUMENT",
                ),
            ),
            upload=upload,
            count=len(texts),
            retry=False,
        )
        vectors = []
        for embedding in result.embeddings or []:
            vector = embedding.values or []
            norm = math.sqrt(sum(value * value for value in vector))
            if len(vector) != self.settings.models.embed_dims or not norm:
                raise ValueError(
                    "Embedding response has invalid dimensions or zero magnitude"
                )
            vectors.append([value / norm for value in vector])
        if len(vectors) != len(texts):
            raise ValueError("Embedding response count does not match input")
        return vectors

    def close(self) -> None:
        self._groq.close()
        self._gemini.close()


class FakeLLM:
    """Deterministic offline client; never constructs a provider or database client."""

    def __init__(self, replies: list[str] | None = None) -> None:
        self.replies = iter(replies or [])
        self.calls: list[dict] = []

    def chat(self, messages, purpose="answer") -> str | dict:
        record_usage(cached=True)
        self.calls.append(
            {
                "purpose": purpose,
                "messages": [
                    {**item, "content": mask_text(item["content"])} for item in messages
                ],
            }
        )
        result = next(self.replies, get_settings().messages["answers"]["retry_later"])
        if isinstance(result, Exception):
            raise result
        return result

    def vision(
        self,
        image,
        mime_type,
        prompt,
        *,
        mode: Literal["ingest", "online"],
        pii_redacted=False,
        local_ocr="",
        cut_off_rows=(),
    ) -> dict:
        record_usage(kind='vlm', cached=True)
        if not pii_redacted:
            raise ValueError(
                "Vision images must be locally redacted before any model call"
            )
        if mode not in {"ingest", "online"}:
            raise ValueError("Vision mode must be ingest or online")
        self.calls.append(
            {"purpose": "vision", "mode": mode, "prompt": mask_text(prompt)}
        )
        for _ in range(get_settings().llm.vision_json_retries + 1):
            try:
                parsed = parse_vision_json(next(self.replies, ""))
                structured = postprocess_vision(
                    parsed,
                    local_ocr,
                    cut_off_rows=cut_off_rows,
                )
            except ValueError:
                continue
            return {
                "text": json.dumps(structured, ensure_ascii=False),
                "raw_text": json.dumps(mask_payload(parsed), ensure_ascii=False),
                "raw_structured": mask_payload(parsed),
                "model": 'scripted',
                "structured": structured,
                "status": "readable",
                "confidence": "fake",
                "warning": None,
            }
        return {
            "text": mask_text(local_ocr),
            "structured": None,
            "status": "unreadable",
            "confidence": "low",
            "warning": get_settings().messages["upload"]["unreadable_image"],
        }

    def embed(self, texts, *, upload=False, query=False) -> list[list[float]]:
        self.calls.append(
            {"purpose": "embedding", "texts": [mask_text(text) for text in texts]}
        )
        dims = get_settings().models.embed_dims
        return [[1.0] + [0.0] * (dims - 1) for _ in texts]

    def close(self) -> None:
        pass
