"""Typed, audited tools with code-enforced visibility and call budgets."""

import json
from datetime import date, timedelta
from typing import Any, Callable, Literal
from uuid import uuid4

from crewai.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, ValidationError

from app.config import get_settings
from app.flows import Flows
from app.ingest.models import utcnow
from app.log import log, mask_text, mask_payload
from app.money import calculate_emi, format_money, rupees_to_paise
from app.rag import envelope, lookup_rate, search_knowledge
from app.session import Sessions
from app.uploads import UploadService

Product = Literal[tuple(get_settings().products['products'])]
Segment = Literal[tuple(get_settings().products['segments'])]
_limits = get_settings().conversation['validation']


class Args(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Empty(Args):
    pass


class Search(Args):
    query: str = Field(min_length=_limits['query_min'], max_length=_limits['query_max'])
    doc_type: Literal['faq', 'policy', 'rate_card', 'offer'] | None = None
    product: Product | None = None
    top_k: int = Field(default=get_settings().knowledge['retrieval']['top_k'], ge=1, le=_limits['top_k_max'])


class MySearch(Args):
    query: str = Field(min_length=_limits['query_min'], max_length=_limits['query_max'])
    doc_id: str | None = None
    top_k: int = Field(default=get_settings().knowledge['retrieval']['top_k'], ge=1, le=_limits['top_k_max'])


class Delete(Args):
    doc_id: str | None = None
    all: bool = False


class Rate(Args):
    product: Product
    tenure_months: int | None = Field(default=None, ge=1, le=_limits['tenure_max'])
    amount_rupees: int | None = Field(default=None, ge=0)
    segment: Segment | None = None
    as_of: date | None = None


class EMI(Args):
    principal_rupees: int = Field(ge=_limits['principal_min'], le=_limits['principal_max'])
    annual_rate_percent: float = Field(ge=_limits['rate_min'], le=_limits['rate_max'])
    tenure_months: int = Field(ge=1, le=_limits['tenure_max'])


class Auth(Args):
    reason: Literal['balance', 'statement', 'cheque_book', 'debit_card', 'card_info', 'other']


class Reason(Args):
    reason: str = Field(default='', max_length=_limits['reason_max'])


class Statement(Args):
    period: Literal['last_5', 'last_10', 'last_30_days', 'custom']
    from_date: date | None = None
    to_date: date | None = None
    direction: Literal['all', 'credit', 'debit'] = 'all'
    page: int = Field(default=1, ge=1)


class Card(Args):
    card_id: str | None = None


class Address(Args):
    mode: Literal['on_file', 'new']
    line1: str | None = None
    line2: str | None = None
    city: str | None = None
    state: str | None = None
    pin: str | None = None


class Cheque(Args):
    pages: int | None = None
    delivery: str | None = None
    address: Address | None = None


class Debit(Args):
    card_type: str | None = None
    address: Address | None = None


class BoundTool(BaseTool):
    _callback: Callable = PrivateAttr()
    _registry: Any = PrivateAttr()
    _structured: Any = PrivateAttr(default=None)

    def __init__(self, name, schema, callback, registry):
        super().__init__(name=name, description=get_settings().conversation['tools'][name], args_schema=schema)
        self._callback, self._registry = callback, registry

    def invoke(self, arguments=None):
        registry = self._registry
        if self.name not in registry.visible():
            return envelope('NOT_VERIFIED')
        if registry.calls >= get_settings().agent['max_tool_calls']:
            return envelope('RATE_LIMITED', display=get_settings().conversation['safe']['tool_limit'])
        registry.calls += 1
        if registry.invalid.get(self.name, 0) > get_settings().agent['tool_retry']:
            return envelope('INVALID_ARGUMENT', display=get_settings().conversation['safe']['tool_limit'])
        try:
            parsed = self.args_schema.model_validate(arguments or {})
            with log.span('TOOL', self.name):
                result = self._callback(**parsed.model_dump(exclude_none=True))
        except ValidationError as exc:
            result = envelope('INVALID_ARGUMENT', {'fields': [str(error['loc']) for error in exc.errors()]})
        except Exception as exc:
            log.step('TOOL', 'failed', tool=self.name, error=type(exc).__name__)
            result = envelope('UPSTREAM_ERROR', display=get_settings().messages['answers']['retry_later'])
        registry.results.append((self.name, result))
        log.step('TOOL', 'result', tool=self.name, args=arguments or {}, code=result['code'])
        if result['code'] == 'INVALID_ARGUMENT':
            registry.invalid[self.name] = registry.invalid.get(self.name, 0) + 1
        if self.name.startswith('search_'):
            log.path('rag', reason='search tool selected')
            for item in result.get('data', {}).get('items', []):
                registry.source_count += 1
                item['n'] = registry.source_count
                item['text'] = get_settings().conversation['agent']['source_wrapper'].format(**item)
        event_id = uuid4().hex
        registry.store.put('audit_log', {'event_id': event_id}, {'event_id': event_id, 'session_id': registry.session['session_id'], 'tool': self.name, 'args': mask_text(json.dumps(arguments or {}, default=str)), 'code': result['code'], 'created_at': utcnow(), 'expires_at': utcnow() + timedelta(days=get_settings().retention['audit_log_days'])})
        # Address PINs are valid stored delivery data, but never leave the code boundary.
        return mask_payload(result)

    def to_structured_tool(self):
        self._structured = super().to_structured_tool()
        return self._structured

    def _run(self, **kwargs):
        result = self.invoke(kwargs)
        step = self._registry.display_step
        # CrewAI stops only on the last successful display in an all-display batch.
        outputs = self._registry.results[self._registry.display_start:] if step else []
        display = self._registry.direct_display(outputs) if len(outputs) == len(step) and [name for name, _ in outputs] == step else None
        self.result_as_answer = bool(display)
        if self._structured is not None:
            self._structured.result_as_answer = bool(display)
        return display or json.dumps(result, ensure_ascii=False, default=str)


class Registry:
    def __init__(self, session, store, *, embeddings=None):
        self.session, self.store, self.embeddings = session, store, embeddings
        self.flows, self.sessions = Flows(store), Sessions(store)
        self.calls, self.results = 0, []
        self.invalid, self.source_count = {}, 0
        self.display_step, self.display_start = [], 0
        self.tools = self._build()

    def direct_display(self, outputs):
        allowed = get_settings().conversation['display_tools']
        if outputs and all(name in allowed and result.get('display') for name, result in outputs):
            log.path('flow-display', reason='all tools returned exact displays')
            return '\n'.join(result['display'] for _, result in outputs)
        return None

    def visible(self):
        public = ['search_bank_knowledge', 'search_my_documents', 'list_my_documents', 'delete_my_documents', 'lookup_rate', 'emi_calculator', 'start_authentication', 'escalate_to_human']
        if self.session['auth']['state'] != 'VERIFIED':
            return public
        active = self.session.get('active_flow')
        if not active:
            return [name for name in public if name != 'start_authentication'] + ['get_balance', 'get_statement', 'get_card_info', 'chequebook_set_slots', 'debit_card_set_slots']
        names = ['search_bank_knowledge', 'search_my_documents', 'flow_status', 'flow_cancel', active['flow'] + '_set_slots', 'escalate_to_human']
        if active['status'] == 'COLLECTING':
            names += ['emi_calculator', 'lookup_rate', 'flow_summary']
        return names

    def _build(self):
        definitions = {
            'search_bank_knowledge': (Search, lambda **args: search_knowledge(**args, store=self.store, embeddings=self.embeddings)),
            'search_my_documents': (MySearch, lambda **args: search_knowledge(**args, namespace='user', owner_key=self.session['session_id'], store=self.store, embeddings=self.embeddings)),
            'list_my_documents': (Empty, self.files),
            'delete_my_documents': (Delete, self.delete),
            'lookup_rate': (Rate, lambda **args: lookup_rate(**args, store=self.store)),
            'emi_calculator': (EMI, self.emi),
            'start_authentication': (Auth, lambda reason: self.sessions.start(self.session)),
            'escalate_to_human': (Reason, self.handoff),
            'get_balance': (Empty, self.balance),
            'get_statement': (Statement, self.statement),
            'get_card_info': (Card, self.card),
            'chequebook_set_slots': (Cheque, lambda **args: self.set_slots('chequebook', **args)),
            'debit_card_set_slots': (Debit, lambda **args: self.set_slots('debit_card', **args)),
            'flow_status': (Empty, lambda: self.flows.status(self.session)),
            'flow_summary': (Empty, lambda: self.flows.summary(self.session)),
            'flow_cancel': (Reason, lambda reason='': self.flows.cancel(self.session)),
        }
        return {name: BoundTool(name, schema, callback, self) for name, (schema, callback) in definitions.items()}

    def set_slots(self, name, **args):
        address = args.get('address')
        if address and address.get('mode') == 'new':
            # Postal PIN is captured locally, never supplied or inferred by a model.
            address['pin'] = self.session.get('postal_pin')
        result = self.flows.set_slots(self.session, name, **args)
        if result['ok']:
            self.session.pop('postal_pin', None)
        return result

    def list(self):
        return [self.tools[name] for name in self.visible()]

    def balance(self):
        account = self.store.one('accounts', {'account_id': self.session['auth']['account_id'], 'user_id': self.session['auth']['user_id']})
        data = {'balance': format_money(account['balance_paise']), 'account': '****' + account['account_number'][-4:], 'status': account['status'], 'as_of': utcnow().date().isoformat()}
        return envelope('OK', data, get_settings().conversation['safe']['balance'].format(**data))

    def statement(self, period, from_date=None, to_date=None, direction='all', page=1):
        cfg, today = get_settings(), utcnow().date()
        if period == 'custom' and (not from_date or not to_date or from_date > to_date or to_date > today or (to_date - from_date).days > cfg.statement['max_range_days']):
            return envelope('INVALID_ARGUMENT', display=cfg.conversation['safe']['statement_limit'].format(days=cfg.statement['max_range_days']))
        rows = self.store.find('transactions', {'account_id': self.session['auth']['account_id']}, sort=[('posted_at', -1)])
        if direction != 'all':
            rows = [row for row in rows if row['direction'] == direction]
        if period in {'custom', 'last_30_days'}:
            start = from_date if period == 'custom' else today - timedelta(days=30)
            end = to_date if period == 'custom' else today
            rows = [row for row in rows if start.isoformat() <= str(row['posted_at'])[:10] <= end.isoformat()]
        elif period in {'last_5', 'last_10'}:
            rows = rows[:int(period.split('_')[1])]
        start = (page - 1) * cfg.statement['page_size']
        more, rows = len(rows) > start + cfg.statement['page_size'], rows[start:start + cfg.statement['page_size']]
        safe = [{'date': str(row['posted_at'])[:10], 'description': mask_text(row['description']), 'direction': row['direction'], 'amount': format_money(row['amount_paise'])} for row in rows]
        totals = {name: format_money(sum(row['amount_paise'] for row in rows if row['direction'] == name)) for name in ['credit', 'debit']}
        display = cfg.conversation['safe']['statement_header'].replace('\\n', '\n') + '\n' + '\n'.join('| ' + ' | '.join(row.values()) + ' |' for row in safe) + '\n' + cfg.conversation['safe']['statement_totals'].format(**totals)
        if more:
            display += '\n' + cfg.conversation['safe']['more']
        return envelope('OK', {'transactions': safe, 'totals': totals, 'more': more}, display)

    def card(self, card_id=None):
        query = {'account_id': self.session['auth']['account_id'], 'user_id': self.session['auth']['user_id']}
        if card_id:
            query['card_id'] = card_id
        row = self.store.one('cards', query)
        if not row:
            return envelope('NOT_FOUND')
        data = {'type': row['card_type'], 'network': row['network'], 'number': '****' + row['last4'], 'status': row['status'], 'expiry': f"{row['expiry_month']:02d}/{row['expiry_year']}", 'limit': format_money(row['daily_limit_paise'])}
        return envelope('OK', data, get_settings().conversation['safe']['card'].format(**data))

    def files(self):
        rows = self.store.find('documents', {'owner_key': self.session['session_id']}, limit=get_settings().uploads['max_files_per_session'])
        return envelope('OK', {'files': [{key: row.get(key) for key in ['doc_id', 'filename', 'processing_state', 'pages_processed', 'pages_total', 'warnings']} for row in rows]})

    def delete(self, doc_id=None, all=False):
        if bool(doc_id) == all:
            return envelope('INVALID_ARGUMENT')
        rows = self.files()['data']['files'] if all else [{'doc_id': doc_id}]
        count = 0
        service = UploadService(self.store)
        for row in rows:
            document = self.store.one('documents', {'doc_id': row['doc_id'], 'owner_key': self.session['session_id']})
            if document:
                service._delete_document(document)
                count += 1
        return envelope('OK', {'deleted': count}, get_settings().messages['upload']['deleted'].format(n=count))

    def emi(self, principal_rupees, annual_rate_percent, tenure_months):
        principal = rupees_to_paise(principal_rupees)
        emi = calculate_emi(principal, str(annual_rate_percent), tenure_months)
        data = {'emi': format_money(emi), 'interest': format_money(emi * tenure_months - principal), 'total': format_money(emi * tenure_months)}
        return envelope('OK', data, get_settings().conversation['safe']['emi'].format(**data))

    def handoff(self, reason=''):
        ref = uuid4().hex[:get_settings().conversation['agent']['reference_digits']].upper()
        self.store.put('requests', {'ref_no': ref}, {'ref_no': ref, 'idempotency_key': uuid4().hex, 'type': 'handoff', 'reason': mask_text(reason), 'session_id': self.session['session_id'], 'created_at': utcnow()})
        return envelope('OK', {'ref_no': ref}, get_settings().messages['answers']['handoff'].format(ref_no=ref))
