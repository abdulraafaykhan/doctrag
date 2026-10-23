"""Plain per-key sliding-window limiter; no framework or router dependencies."""

from collections import deque
from math import ceil
from threading import Lock

_lock = Lock()


def check_rate_limit(
    windows: dict[str, deque[float]], key: str, limit: int, now: float
) -> int | None:
    """Reserve one request, or return seconds until a slot is available."""
    with _lock:
        for expired_key in list(windows):
            window = windows[expired_key]
            while window and window[0] <= now - 60:
                window.popleft()
            if not window:
                del windows[expired_key]
        window = windows.setdefault(key, deque())
        if len(window) >= limit:
            return max(1, ceil(window[0] + 60 - now))
        window.append(now)
        return None


def rate_message(seconds: int) -> str:
    return f"You're sending messages too quickly. Please try again in about {seconds} seconds."
