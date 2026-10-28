"""Process-wide token accounting with request context shared by worker threads."""
from contextlib import contextmanager
from contextvars import ContextVar
from threading import RLock

session_context = ContextVar('token_session', default=None)
usage_context = ContextVar('token_request', default=None)
store_context = ContextVar('token_store', default=None)


def empty():
    return {'in': 0, 'out': 0, 'total': 0, 'calls': 0}


class TokenMeter:
    def __init__(self):
        self._lock = RLock()
        self._sessions, self._process = {}, empty()
        self.events = []

    def restore(self, session_id, saved):
        with self._lock:
            if session_id not in self._sessions:
                self._sessions[session_id] = {key: int(saved.get(key, 0)) for key in empty()}

    def add(self, session_id, model, kind, input_tokens, output_tokens, *, cached=False):
        values = {'in': 0 if cached else int(input_tokens or 0), 'out': 0 if cached else int(output_tokens or 0), 'calls': 1}
        values['total'] = values['in'] + values['out']
        session_id = session_id or 'background'
        with self._lock:
            current = self._sessions.setdefault(session_id, empty())
            for key in values:
                current[key] += values[key]
                self._process[key] += values[key]
            request = usage_context.get()
            if request is not None:
                for key in values:
                    request[key] += values[key]
            self.events.append({'session_id': session_id, 'model': model, 'kind': kind, 'cached': cached, **values})
            from app.log import request_context, span_stack
            trace = request_context.get()
            if trace is not None:
                trace.setdefault('tokens', empty())
                for key in values:
                    trace['tokens'][key] += values[key]
                for span in span_stack.get():
                    if isinstance(span, dict):
                        for key in ('in', 'out', 'total'):
                            span['attributes'][key] = span['attributes'].get(key, 0) + values[key]
            store = store_context.get()
            if store is not None and session_id != 'background':
                store.patch('sessions', {'session_id': session_id}, {'$inc': {f'counters.tokens.{key}': value for key, value in values.items()}})
            return {**values, 'cached': cached, 'session_total': current['total']}

    def session(self, session_id):
        with self._lock:
            return dict(self._sessions.get(session_id, empty()))

    def process(self):
        with self._lock:
            return dict(self._process)

    def per_session_table(self):
        with self._lock:
            return {key: dict(value) for key, value in self._sessions.items()}


meter = TokenMeter()


@contextmanager
def token_scope(session_id, store=None):
    if store is not None:
        row = store.one('sessions', {'session_id': session_id}) or {}
        meter.restore(session_id, row.get('counters', {}).get('tokens', {}))
    state = empty()
    tokens = (session_context.set(session_id), usage_context.set(state), store_context.set(store))
    try:
        yield state
    finally:
        session_context.reset(tokens[0])
        usage_context.reset(tokens[1])
        store_context.reset(tokens[2])


def response_tokens(session_id):
    request = usage_context.get() or empty()
    return {'request': {key: request[key] for key in ('in', 'out', 'total')}, 'session': meter.session(session_id)}


def shutdown_tokens():
    from app.log import log
    for sid, values in meter.per_session_table().items():
        if values['total']:
            log.step('SESSION TOKENS', '', session=sid[:8], **values)
    log.step('PROCESS TOKENS', '', **meter.process())
