"""Plain per-user sliding-window limiter; no framework or router dependencies."""
from collections import deque
from math import ceil
from threading import Lock

_lock = Lock()


def check_rate_limit(windows: dict[str, deque[float]], user_id: str, limit: int, now: float) -> int | None:
    """Reserve one request, or return seconds until a slot is available."""
    with _lock:
        for key in list(windows):
            window = windows[key]
            while window and window[0] <= now - 60:
                window.popleft()
            if not window:
                del windows[key]
        window = windows.setdefault(user_id, deque())
        if len(window) >= limit:
            return max(1, ceil(window[0] + 60 - now))
        window.append(now)
        return None


def rate_message(seconds: int) -> str:
    return f"You're sending messages too quickly. Please try again in about {seconds} seconds."
