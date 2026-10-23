"""Content-free request diagnostics, with a final credential-masking boundary."""

import logging
import re
import secrets
from contextvars import ContextVar

from starlette.types import ASGIApp, Receive, Scope, Send

from app.verification.masking import mask_sensitive

turn_id = ContextVar("turn_id", default="startup")
otp_input = ContextVar("otp_input", default=False)


class PrivacyFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        text = record.getMessage()
        # Uvicorn access logs contain URL paths; session ids are bearer secrets.
        text = re.sub(r"(/sessions/)([^/ ?]+)", lambda m: m[1] + m[2][:6] + "...", text)
        record.msg = "[turn=%s] %s" % (
            turn_id.get(),
            mask_sensitive(text, awaiting_otp=otp_input.get()),
        )
        record.args = ()
        # Tracebacks may embed third-party exception messages containing input.
        record.exc_info = None
        record.exc_text = None
        return True


def configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.setLevel(logging.INFO)
    handler.addFilter(PrivacyFilter())
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s %(message)s"))
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers = []
        logger.propagate = True
    for name in ("httpx", "httpx2", "httpcore", "pymongo", "groq", "google"):
        logging.getLogger(name).setLevel(logging.WARNING)


class RequestLoggingMiddleware:
    """ASGI scope keeps one turn id alive until the final SSE byte is sent."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        token = turn_id.set(secrets.token_hex(4))
        otp_token = otp_input.set(False)
        try:
            await self.app(scope, receive, send)
        finally:
            otp_input.reset(otp_token)
            turn_id.reset(token)
