"""One masked logging boundary for terminal and rotating JSON output."""

import functools
import inspect
import json
import logging
from contextvars import ContextVar
from uuid import uuid4
import re
from contextlib import contextmanager
from datetime import datetime
from logging.handlers import RotatingFileHandler
from threading import Lock
from time import perf_counter
from typing import Any

from rich.console import Console
from rich.text import Text

from app.config import ROOT, get_settings

AREAS = dict(
    zip(
        "UI API SESSION AUTH PREPASS AGENT TOOL FLOW LLM EMBED VLM OCR PARSE INGEST RAG VERIFY DB UPLOAD CLEANUP".split(),
        (
            "cyan",
            "green",
            "blue",
            "yellow",
            "magenta",
            "cyan",
            "green",
            "blue",
            "magenta",
            "cyan",
            "yellow",
            "blue",
            "green",
            "cyan",
            "magenta",
            "yellow",
            "blue",
            "green",
            "yellow",
        ),
    )
)
_NUMBER = re.compile(r"(?<!\w)(?:\+91[ -]*)?(?:\d[ -]?){5,18}\d(?!\w)")
_SENSITIVE = re.compile(
    r"^(?:otp|pin|cvv|password|secret|.*api.?key|mongodb.?uri|token|pending_token|code_hash|authorization|card.?number)$",
    re.I,
)


def mask_mobile(value: str) -> str:
    digits = re.sub(r"\D", "", value)[-10:]
    return digits[:2] + "****" + digits[-2:] if len(digits) == 10 else "[MOBILE]"


def mask_account(value: str) -> str:
    digits = re.sub(r"\D", "", value)
    return "****" + digits[-4:] if len(digits) == 12 else "[ACCOUNT]"


def mask_text(value: str) -> str:
    def replace(match: re.Match) -> str:
        raw = match.group()
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
            try:
                datetime.strptime(raw, "%Y-%m-%d")
                return raw
            except ValueError:
                pass
        digits = re.sub(r"\D", "", raw)
        reference_digits = get_settings().conversation['agent']['reference_digits']
        if re.fullmatch(r'\d{4}-\d{' + str(reference_digits) + '}', raw) and any(value[:match.start()].endswith(flow['reference_prefix'] + '-') for flow in get_settings().flows.values()):
            # Generated request references are trace IDs, not customer credentials.
            return raw
        if len(digits) == 12 and digits.startswith("91") and digits[2] in "6789":
            return mask_mobile(digits[2:])
        if len(digits) == 10 and digits[0] in "6789":
            return mask_mobile(digits)
        if len(digits) == 12:
            return mask_account(digits)
        return "[REDACTED]"

    value = re.sub(r"mongodb(?:\+srv)?://[^\s]+", "[DATABASE_URI]", value)
    value = re.sub(r"\b(?:gsk_|AIza)[\w-]+", "[API_KEY]", value)
    value = re.sub(
        r"(?i)\b(api[_ -]?key|password|secret|authorization)\s*[:=]\s*\S+",
        r"\1=[REDACTED]",
        value,
    )
    value = re.sub(
        r"(?i)\b(otp|pin|cvv|card(?: number)?)\s*[:=]?\s*(?=[\d *-]*[\d*])[\d *-]+",
        r"\1 [REDACTED]",
        value,
    )
    return _NUMBER.sub(replace, value)


def mask_payload(value: Any, key: str = "") -> Any:
    """Mask leaves before serialization so numeric redaction cannot corrupt JSON."""
    if _SENSITIVE.search(key):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(k): mask_payload(v, str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [mask_payload(item, key) for item in value]
    if isinstance(value, (str, int, float)) and not isinstance(value, bool):
        masked = mask_text(str(value))
        return value if masked == str(value) else masked
    return value


def _clean(value: Any, key: str = "") -> Any:
    if _SENSITIVE.search(key):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(k): _clean(v, str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(item, key) for item in value]
    return mask_text(str(value))


request_context = ContextVar('request_log', default=None)
span_stack = ContextVar('log_span_stack', default=())


class AppLog:
    def __init__(self) -> None:
        self.console = Console()
        self._logger: logging.Logger | None = None
        self._lock = Lock()

    def _configure(self) -> logging.Logger:
        with self._lock:
            return self._configure_locked()

    def _configure_locked(self) -> logging.Logger:
        if self._logger is None:
            cfg = get_settings().logging
            path = ROOT / cfg.file
            path.parent.mkdir(parents=True, exist_ok=True)
            logger = logging.getLogger("harbourline")
            logger.setLevel(cfg.level.upper())
            logger.propagate = False
            if not logger.handlers:
                handler = RotatingFileHandler(
                    path,
                    maxBytes=cfg.max_bytes,
                    backupCount=cfg.backup_count,
                    encoding="utf-8",
                )
                handler.setFormatter(logging.Formatter("%(message)s"))
                logger.addHandler(handler)
            self._logger = logger
        return self._logger

    def path(self, value, *, reason):
        state = request_context.get()
        if state is not None:
            state['path'] = value
        self.step('API', 'path selected', path=value, reason=reason)

    @contextmanager
    def request(self, kind, request_id=None):
        state = {'id': request_id or 'r-' + uuid4().hex[:4], 'path': kind if kind == 'upload' else 'agent', 'spans': [], 'upload': {}}
        token = request_context.set(state)
        started = perf_counter()
        try:
            yield state
        finally:
            spans = state['spans']
            def total(area):
                return sum(ms for name, _, ms, _ in spans if name == area) / 1000
            llms = [entry for entry in spans if entry[0] == 'LLM']
            models = ','.join(dict.fromkeys(str(entry[3].get('model', '?')) for entry in llms)) or 'none'
            tools = ','.join(f'{action}({ms / 1000:.2f}s)' for area, action, ms, _ in spans if area == 'TOOL') or 'none'
            line = (f"{kind}  total={perf_counter() - started:.2f}s  path={state['path']}  "
                    f"llm={total('LLM'):.2f}s({models}, {len(llms)} call)  tools={tools}  "
                    f"db={total('DB'):.2f}s({sum(area == 'DB' for area, *_ in spans)} ops)  "
                    f"prepass={total('PREPASS'):.2f}s  verify={total('VERIFY'):.2f}s")
            if kind == 'upload':
                line += '  ' + '  '.join(f'{key}={value}' for key, value in state['upload'].items())
                line += '  stages=' + ','.join(f'{area.lower()}:{total(area):.2f}s' for area in ('PARSE', 'OCR', 'VLM', 'CHUNK', 'EMBED'))
            self.step('API', line)
            request_context.reset(token)

    def upload_details(self, **fields):
        state = request_context.get()
        if state is not None:
            state['upload'].update(fields)

    def step(self, area: str, action: str, **kv: Any) -> None:
        logger = self._configure()
        level = kv.pop("level", "DEBUG" if area == "DB" else "INFO").upper()
        state = request_context.get()
        request_id = state["id"] if state else "-"
        if not logger.isEnabledFor(getattr(logging, level)):
            return
        cfg = get_settings().logging
        fields = _clean(kv)
        for key in fields:
            if key in {"message", "text", "message_text"}:
                fields[key] = str(fields[key])[: cfg.message_chars]
        action = mask_text(action)
        stamp = datetime.now().astimezone()
        detail = " ".join(f"{key}={value}" for key, value in fields.items())
        self.console.print(
            Text(
                f"{stamp:%H:%M:%S}  {request_id}  {area:<8} {action} | {detail}",
                style=AREAS.get(area, "white"),
            )
        )
        logger.log(
            getattr(logging, level),
            json.dumps(
                {
                    "ts": stamp.isoformat(),
                    "request_id": request_id,
                    "level": level,
                    "area": area,
                    "action": action,
                    **fields,
                },
                ensure_ascii=False,
            ),
        )

    @contextmanager
    def span(self, area: str, action: str, **kv: Any):
        started = perf_counter()
        stack = span_stack.get()
        token = span_stack.set((*stack, area))
        try:
            yield
        except Exception as exc:
            # Exception messages may contain credentials or customer input.
            self.step(
                area,
                action,
                **kv,
                ms=round((perf_counter() - started) * 1000),
                error=type(exc).__name__,
                level="ERROR",
            )
            raise
        else:
            self.step(area, action, **kv, ms=round((perf_counter() - started) * 1000))
        finally:
            state = request_context.get()
            if state is not None and area not in stack:
                state['spans'].append((area, action, (perf_counter() - started) * 1000, kv))
            span_stack.reset(token)


log = AppLog()


def traced(area: str, action: str):
    def decorate(function):
        if inspect.iscoroutinefunction(function):

            @functools.wraps(function)
            async def async_wrapper(*args, **kwargs):
                with log.span(area, action):
                    return await function(*args, **kwargs)

            return async_wrapper

        @functools.wraps(function)
        def wrapper(*args, **kwargs):
            with log.span(area, action):
                return function(*args, **kwargs)

        return wrapper

    return decorate
