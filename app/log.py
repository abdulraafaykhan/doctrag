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
_NUMBER = re.compile(r"(?<!\w)(?:\+91[ -]*)?(?:\d[ -]?){9,18}\d(?!\w)")
_SENSITIVE = re.compile(
    r"^(?:pin|cvv|password|secret|.*api.?key|mongodb.?uri|token|pending_token|code_hash|otp_hash|authorization|card.?number)$",
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
        if len(digits) == 12 and digits.startswith("91") and digits[2] in "6789":
            return mask_mobile(digits[2:])
        if len(digits) == 10 and digits[0] in "6789":
            return mask_mobile(digits)
        if len(digits) == 12:
            return mask_account(digits)
        return "[CARD]" if 13 <= len(digits) <= 19 else raw

    value = re.sub(r"mongodb(?:\+srv)?://[^\s]+", "[DATABASE_URI]", value)
    value = re.sub(r"\b(?:gsk_|AIza)[\w-]+", "[API_KEY]", value)
    value = re.sub(
        r"(?i)\b(api[_ -]?key|password|secret|authorization)\s*[:=]\s*\S+",
        r"\1=[REDACTED]",
        value,
    )
    value = re.sub(
        r"(?i)\b(pin|cvv)\s*[:=]?\s*(?=[\d *-]*[\d*])[\d *-]+",
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
            logger.setLevel(logging.DEBUG)
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
        self.step('API', 'path selected', path=value, why=reason)

    @contextmanager
    def request(self, kind, request_id=None):
        state = {'id': request_id or 'r-' + uuid4().hex[:4], 'path': 'upload' if kind.endswith('upload') else 'agent', 'spans': [], 'upload': {}, 'tokens': {'in': 0, 'out': 0, 'total': 0, 'calls': 0}}
        token = request_context.set(state)
        started = perf_counter()
        try:
            yield state
        finally:
            spans = state['spans']
            def total(area):
                return sum(span['duration_ms'] for span in spans if span['area'] == area and not any(other['span_id'] == span['parent_span_id'] and other['area'] == area for other in spans)) / 1000
            llms = [entry for entry in spans if entry['area'] in {'LLM', 'VLM'}]
            models = ','.join(dict.fromkeys(str(entry['attributes'].get('model', '?')) for entry in llms)) or 'none'
            tools = ','.join(f"{span['name']}({span['duration_ms'] / 1000:.2f}s)" for span in spans if span['area'] == 'TOOL') or 'none'
            line = (f"{kind}  total={perf_counter() - started:.2f}s  path={state['path']}  "
                    f"llm={total('LLM'):.2f}s({models}, {len(llms)} call)  tools={tools}  "
                    f"db={total('DB'):.2f}s({sum(span['area'] == 'DB' for span in spans)} ops)  "
                    f"prepass={total('PREPASS'):.2f}s  verify={total('VERIFY'):.2f}s")
            if kind.endswith('upload'):
                line += '  ' + '  '.join(f'{key}={value}' for key, value in state['upload'].items())
                line += '  stages=' + ','.join(f'{area.lower()}:{total(area):.2f}s' for area in ('PARSE', 'OCR', 'VLM', 'CHUNK', 'EMBED'))
            duration = perf_counter() - started
            db_ops = sum(span['area'] == 'DB' for span in spans)
            self.console.print(Text(f"TRACE TREE {state['id']}  {kind} total={duration:.2f}s db={total('DB'):.3f}s ({db_ops} ops)"))
            visible = [span for span in spans if span['area'] != 'DB']
            def tree(parent=None, prefix=''):
                children = [span for span in visible if span['parent_span_id'] == parent]
                for index, span in enumerate(children):
                    last = index == len(children) - 1
                    attrs = span['attributes']
                    details = ' '.join(f'{key}={attrs[key]}' for key in ('model', 'in', 'out', 'total', 'chars') if key in attrs)
                    label = f"{span['area']} {span['module']}.{span['function']} {span['status']} {span['duration_ms'] / 1000:.3f}s name={span['name']} {details}"
                    self.console.print(Text(prefix + ('└─ ' if last else '├─ ') + mask_text(label)))
                    tree(span['span_id'], prefix + ('   ' if last else '│  '))
            tree()
            self.step('API', line, **state['tokens'])
            request_context.reset(token)

    def upload_details(self, **fields):
        state = request_context.get()
        if state is not None:
            state['upload'].update(fields)

    def step(self, area: str, action: str, **kv: Any) -> None:
        if area in {'LLM', 'VLM'}:
            from app.usage import meter, session_context
            for field in ('in', 'out', 'total'):
                kv.setdefault(field, 0)
            kv.setdefault('session_total', meter.session(session_context.get())['total'])
        logger = self._configure()
        if 'reason' in kv:
            kv.setdefault('why', kv['reason'])
        if any(word in action.lower() for word in ('cache', 'fallback', 'repair', 'selected', 'visibility', 'number check', 'number and citation')):
            kv.setdefault('why', 'matching cached result reused' if 'hit' in action else 'decision follows current validation and state')
        if 'error' in kv:
            kv.setdefault('reason', 'operation failed; see exception type')
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
        if getattr(logging, level) >= getattr(logging, cfg.level.upper()):
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
                    "trace_id": request_id,
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
        frame = inspect.currentframe().f_back
        while frame and frame.f_globals.get('__name__') in {__name__, 'contextlib'}:
            frame = frame.f_back
        module = kv.pop('module', frame.f_globals.get('__name__', '') if frame else '')
        function = kv.pop('function', frame.f_code.co_qualname if frame else '')
        stack = span_stack.get()
        state = request_context.get()
        entry = {'trace_id': state['id'] if state else None, 'span_id': uuid4().hex[:12], 'parent_span_id': stack[-1]['span_id'] if stack else None, 'name': action, 'area': area, 'module': module, 'function': function, 'start_time': datetime.now().astimezone().isoformat(), 'duration_ms': 0, 'status': 'ok', 'attributes': dict(kv)}
        if state is not None:
            state['spans'].append(entry)
        token = span_stack.set((*stack, entry))
        try:
            yield entry['attributes']
        except Exception as exc:
            entry['status'] = 'error'
            entry['attributes'].update(error=type(exc).__name__, reason='operation failed; see exception type')
            raise
        finally:
            entry['duration_ms'] = round((perf_counter() - started) * 1000, 3)
            entry['status'] = entry['attributes'].pop('status', entry['status'])
            self.step(area, module + '.' + function, name=action, status=entry['status'], ms=entry['duration_ms'], **entry['attributes'], level='DEBUG' if area == 'DB' else 'ERROR' if entry['status'] == 'error' else 'INFO')
            self._configure().debug(json.dumps({'type': 'span', **{key: value for key, value in entry.items() if key != 'attributes'}, 'attributes': mask_payload(entry['attributes'])}, ensure_ascii=False))
            span_stack.reset(token)


log = AppLog()


def traced(area: str, action: str):
    def decorate(function):
        if inspect.iscoroutinefunction(function):

            @functools.wraps(function)
            async def async_wrapper(*args, **kwargs):
                with log.span(area, action, module=function.__module__, function=function.__qualname__):
                    return await function(*args, **kwargs)

            return async_wrapper

        @functools.wraps(function)
        def wrapper(*args, **kwargs):
            with log.span(area, action, module=function.__module__, function=function.__qualname__):
                return function(*args, **kwargs)

        return wrapper

    return decorate
