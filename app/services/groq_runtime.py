"""Shared Groq concurrency and bounded rate-limit retries, including streams."""
import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from math import ceil, isfinite
from typing import Any
from groq import RateLimitError
from app.config import get_settings


class AssistantBusy(Exception):
    def __init__(self, retry_after: float):
        self.retry_after = max(1, ceil(retry_after))
        super().__init__(f"The assistant is busy. Please try again in about {self.retry_after} seconds.")


def retry_seconds(error: RateLimitError) -> float:
    value = error.response.headers.get("retry-after", "60")
    try:
        seconds = float(value)
    except ValueError:
        try:
            when = parsedate_to_datetime(value)
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            seconds = (when - datetime.now(timezone.utc)).total_seconds()
        except (ValueError, TypeError, OverflowError):
            return 60
    return max(0, seconds) if isfinite(seconds) else 60


@asynccontextmanager
async def groq_slot() -> AsyncIterator[None]:
    loop = asyncio.get_running_loop()
    limit = get_settings().llm_max_concurrency
    state = getattr(loop, "_doctrag_groq_slot", None)
    if state is None or state[0] != limit:
        state = (limit, asyncio.Semaphore(limit))
        setattr(loop, "_doctrag_groq_slot", state)
    async with state[1]:
        yield


async def call_groq(create: Callable[..., Awaitable[Any]], **kwargs) -> Any:
    for attempt in range(2):
        try:
            async with groq_slot():
                return await create(**kwargs)
        except RateLimitError as error:
            seconds = retry_seconds(error)
            if attempt or seconds > 10:
                raise AssistantBusy(seconds) from None
            await asyncio.sleep(seconds)


async def stream_groq(create: Callable[..., Awaitable[Any]], **kwargs) -> AsyncIterator[Any]:
    emitted = False
    for attempt in range(2):
        try:
            async with groq_slot():
                response = await create(**kwargs)
                try:
                    async for chunk in response:
                        choices = getattr(chunk, "choices", None)
                        content = getattr(choices[0].delta, "content", None) if choices else chunk
                        emitted = emitted or bool(content)
                        yield chunk
                finally:
                    close = getattr(response, "close", None) or getattr(response, "aclose", None)
                    if close is not None:
                        await close()
                return
        except RateLimitError as error:
            seconds = retry_seconds(error)
            # Retrying after streamed output would duplicate text already sent.
            if attempt or emitted or seconds > 10:
                raise AssistantBusy(seconds) from None
            await asyncio.sleep(seconds)
