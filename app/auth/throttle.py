"""Username-based, process-local login attempt throttling."""
import asyncio
import time
from collections import deque
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator


class LoginThrottle:
    def __init__(self) -> None:
        self.failures: dict[str, deque[float]] = {}
        self.blocked_until: dict[str, float] = {}
        self.lock = asyncio.Lock()

    def prune(self, now: float) -> None:
        for username in list(self.failures):
            attempts = self.failures[username]
            while attempts and attempts[0] <= now - 900:
                attempts.popleft()
            if not attempts:
                del self.failures[username]
        for username in list(self.blocked_until):
            if self.blocked_until[username] <= now:
                del self.blocked_until[username]

    @asynccontextmanager
    async def attempt(self, username: str) -> AsyncIterator[bool]:
        # Serialize the check and verification so concurrent attempts cannot bypass the cap.
        async with self.lock:
            self.prune(time.monotonic())
            yield username not in self.blocked_until

    def fail(self, username: str) -> None:
        now = time.monotonic()
        attempts = self.failures.setdefault(username, deque())
        attempts.append(now)
        if len(attempts) >= 5:
            self.blocked_until[username] = now + 900

    def succeed(self, username: str) -> None:
        self.failures.pop(username, None)
        self.blocked_until.pop(username, None)


login_throttle = LoginThrottle()
