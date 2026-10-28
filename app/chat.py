"""Single conversation turn: local state, masked agent, verification and persistence."""

import re
from app.agent import Runner
from app.config import get_settings
from app.flows import Flows
from app.ingest.models import utcnow
from app.ingest.store import Store
from app.log import log, mask_text
from app.prepass import extract
from app.rag import envelope
from app.session import Sessions
from app.tools.registry import Registry
from app.uploads import UploadService


class Chat:
    def __init__(self, store=None, *, llm=None, runner=None, embeddings=None):
        self.store = store or Store()
        self.sessions, self.flows = Sessions(self.store), Flows(self.store)
        self.runner, self.embeddings = runner or Runner(llm), embeddings

    def _agent(self, session, text, attachments=(), *, code_route=None, registry=None):
        log.path('rag-code-routed' if code_route else 'agent', reason='code selected ' + code_route if code_route else 'requires tool selection or grounded answer')
        registry = registry or Registry(session, self.store, embeddings=self.embeddings, customer_message=text, attachment_ids=attachments)
        outcome = self.runner.run(session, text, registry, **({'code_route': code_route} if code_route else {}))
        # start_authentication is the model's signal to hold the original masked request.
        if any(name == 'start_authentication' for name, _ in registry.results):
            self.sessions.hold(session, text)
        outcome['tools_called'] = [name for name, _ in registry.results]
        return outcome

    def turn(self, message, *, session_id=None, attachments=()):
        session = self.sessions.load(session_id)
        self.store.patch('sessions', {'session_id': session['session_id']}, {'$set': {'last_active_at': utcnow()}})
        with self.sessions.lock(session['session_id']):
            return self.turn_locked(message, session_id=session['session_id'], attachments=attachments)

    def turn_locked(self, message, *, session_id, attachments=()):
        """The HTTP boundary keeps the same lease until its transcript is persisted."""
        from datetime import timedelta
        session = self.store.one('sessions', {'session_id': session_id})
        before = (session['auth']['state'], (session.get('active_flow') or {}).get('status'))
        result = self._turn(session, message, attachments)
        if session.get('active_flow'):
            session['active_flow']['updated_at'] = utcnow()
        session['expires_at'] = utcnow() + timedelta(hours=get_settings().session['expires_hours'])
        # A slow reply must not move the last user-action time forward or overwrite a newer action.
        session.pop('last_active_at', None)
        self.sessions.save(session)
        after = (session['auth']['state'], (session.get('active_flow') or {}).get('status'))
        if before != after:
            log.step('SESSION', 'state transition', before=before, after=after)
        return {**result, 'session_id': session['session_id'], 'state': session['auth']['state'], 'flow': session.get('active_flow'), 'options': self.options(session), 'attachments': session.get('attachments', [])}

    def options(self, session):
        active = session.get('active_flow')
        if not active:
            return []
        if active.get('change_requested'):
            return get_settings().flows[active['flow']]['change_buttons']
        if active['status'] == 'AWAITING_CONFIRM':
            return get_settings().flows[active['flow']]['confirm_buttons']
        step = self.flows.next_step(session)
        return [option['label'] for option in step.get('options', [])] if step else []

    def _turn(self, session, message, attachments):
        cfg, auth = get_settings(), session['auth']
        if len(message) > cfg.agent['max_message_chars']:
            return {'reply': cfg.conversation['safe']['too_long'], 'citations': [], 'tools_called': []}
        parsed = extract(message)
        if not re.search(r'\w', message):
            return {'reply': cfg.conversation['safe']['empty'], 'citations': [], 'tools_called': []}
        log.step('PREPASS', 'input extracted', message=None if parsed.code and auth['state'] == 'AWAITING_OTP' else parsed.text, hits=[name for name in ('mobile', 'account', 'code', 'postal_pin') if getattr(parsed, name)])
        normalized = ' '.join(re.sub(r'[^\w\s]|_', '', message.lower()).split())
        for group, entry in cfg.conversation['smalltalk'].items():
            if normalized in entry['phrases']:
                log.path('greeting', reason='exact ' + group)
                result = {'reply': entry['reply'], 'citations': [], 'tools_called': []}
                self._history(session, parsed, result)
                return result
        if any(word in parsed.command for word in cfg.conversation['safe']['forbidden_patterns']):
            return {'reply': cfg.conversation['safe']['forbidden'], 'citations': [], 'tools_called': []}
        # Customer files are owned by this session and can be parsed before login.
        if len(attachments) > cfg.uploads['max_files_per_message']:
            return {'reply': cfg.knowledge['messages']['upload_limit'], 'citations': [], 'tools_called': []}
        for attachment in attachments:
            uploads = UploadService(self.store)
            document = uploads._owned(session['session_id'], attachment)
            if document.get('attachment', {}).get('status') not in {'Ready', 'Partial'}:
                uploads.process(session['session_id'], attachment, defer_embeddings=True)
        fixed = None
        replay = []
        personal = any(word in parsed.command for word in cfg.conversation['personal'])
        if any(re.search(pattern, parsed.command) for pattern in cfg.conversation['public_question_patterns']) and not any(word in parsed.command for word in cfg.conversation['personal_owners']):
            personal = False
        if any(word in parsed.command for word in cfg.conversation['public_exceptions']) and not any(word in parsed.command for word in cfg.conversation['personal_owners']):
            personal = False
        if cfg.models.agent.startswith('local/') and not (parsed.code and auth['state'] == 'AWAITING_OTP') and not parsed.mobile and not parsed.account:
            registry = Registry(session, self.store, embeddings=self.embeddings, customer_message=parsed.text, attachment_ids=attachments)
            words = set(re.findall(r'\w+', parsed.command))
            public = any(re.search(pattern, parsed.command) for pattern in cfg.conversation['public_question_patterns']) or any(word.casefold() in parsed.command for word in cfg.conversation['public'])
            route, why = None, None
            if registry.references_upload():
                route, why = 'search_my_documents', 'current attachment or reference to uploaded session files'
            elif public and not personal and not session.get('active_flow') and not words.intersection(cfg.local_llm['routing_passthrough_words']):
                route, why = 'search_bank_knowledge', 'public question without personal intent or an open flow'
            elif auth['state'] == 'VERIFIED' and words.intersection(cfg.local_llm['balance_words']):
                route, why = 'get_balance', 'verified customer requested the balance; return the tool display'
            if route and route in registry.visible():
                log.step('AGENT', 'code route selected', path='rag-code-routed', tool=route, why=why)
                result = self._agent(session, parsed.text, attachments, code_route=route, registry=registry)
                self._history(session, parsed, result)
                log.step('API', 'chat response', auth=auth['state'], tools=result.get('tools_called', []))
                return result
        if auth['state'] == 'VERIFIED' and parsed.account:
            own = self.store.one('accounts', {'account_id': auth['account_id']})
            if own['account_number'] != parsed.account:
                self.sessions.clear(session)
                auth = session['auth']
                fixed = envelope('NOT_VERIFIED', display=cfg.conversation['safe']['account_change'])
        if parsed.mobile:
            auth.setdefault('candidates', {})['mobile'] = parsed.mobile
        if parsed.account:
            auth.setdefault('candidates', {})['account'] = parsed.account
        if auth['state'] == 'AWAITING_OTP':
            if parsed.command in cfg.conversation['commands']['cancel']:
                self.sessions.clear(session)
                fixed = envelope('OK', display=cfg.messages['auth']['cancelled'])
            elif parsed.command in cfg.conversation['commands']['resend']:
                if auth.get('resends', 0) >= cfg.auth['otp_max_resends']:
                    from datetime import timedelta
                    auth['locked_until'] = utcnow() + timedelta(minutes=cfg.auth['lock_minutes'])
                    fixed = envelope('LOCKED', display=cfg.messages['auth']['locked'].format(minutes=cfg.auth['lock_minutes']))
                else:
                    auth['resends'] = auth.get('resends', 0) + 1
                    fixed = self.sessions.start(session, resend=True)
            elif parsed.code or not any(word.lower() in parsed.command for word in cfg.conversation['public']) and not personal:
                fixed = self.sessions.otp(session, parsed.code or '')
                if fixed['ok']:
                    replay = auth.pop('held', [])
            elif personal:
                self.sessions.hold(session, parsed.text, attachments)
                fixed = envelope('OTP_SENT', display=cfg.messages['auth']['otp_sent'])
        elif auth['state'] != 'VERIFIED' and (parsed.mobile or parsed.account or personal):
            self.sessions.hold(session, parsed.text, attachments)
            fixed = self.sessions.start(session)
        elif auth['state'] == 'VERIFIED':
            fixed = self.flows.fast(session, message)
            active = session.get('active_flow')
            if active:
                other = next((name for name, aliases in cfg.conversation['flow_words'].items() if name != active['flow'] and any(alias in parsed.command for alias in aliases)), None)
                if other:
                    fixed = envelope('FLOW_ACTIVE', display=cfg.messages['flow']['busy'].format(flow=active['flow']))
                if parsed.command in cfg.conversation['commands'].get('stop', []):
                    fixed = envelope('NEEDS_MORE_INFO', display=cfg.conversation['safe']['cancel_continue'])
                step = self.flows.next_step(session)
                if step and step['type'] == 'address':
                    pin = parsed.postal_pin or parsed.code
                    if pin:
                        session['postal_pin'] = pin
                    if re.fullmatch(r'(?:pin\s*)?\d+', parsed.command):
                        draft = active.get('address_draft')
                        fixed = self.flows.set_slots(session, active['flow'], address={**draft, 'pin': pin}) if draft else envelope('INVALID_ARGUMENT', display=step['on_invalid'])
            if not session.get('active_flow') and parsed.command in cfg.conversation['commands']['confirm'] and session.get('last_submission'):
                request = session['last_submission']
                fixed = envelope('OK', display=cfg.flows[request['type']]['success_template'].format(**request))
        if replay:
            outputs = [self._agent(session, held['text'], held.get('attachments', ())) for held in replay]
            result = {'reply': '\n'.join(output['reply'] for output in outputs), 'citations': [citation for output in outputs for citation in output.get('citations', [])], 'tools_called': [name for output in outputs for name in output.get('tools_called', [])]}
        elif fixed:
            log.path('fast-otp' if auth['state'] != 'VERIFIED' or parsed.code else 'fast-confirm', reason=fixed['code'])
            result = {'reply': fixed['display'], 'citations': [], 'tools_called': []}
            if personal and any(word in parsed.command for word in cfg.conversation['mixed_public_signals']) and auth['state'] != 'VERIFIED':
                public = self._agent(session, parsed.text, attachments)
                result = {**public, 'reply': public['reply'] + '\n' + fixed['display']}
        else:
            result = self._agent(session, parsed.text, attachments)
            if session.get('active_flow'):
                status = self.flows.status(session)
                suffix = cfg.messages['flow']['confirm_unclear'] if session['active_flow']['status'] == 'AWAITING_CONFIRM' else status['display']
                if suffix not in result['reply']:
                    result['reply'] += '\n' + suffix
        self._history(session, parsed, result)
        log.step('API', 'chat response', auth=session['auth']['state'], tools=result.get('tools_called', []))
        return result

    def _history(self, session, parsed, result):
        cfg = get_settings()
        # Verification codes stay out of model history; labelled credentials are already masked.
        if not parsed.code:
            session['history'].extend([{'role': 'user', 'content': parsed.text}, {'role': 'assistant', 'content': mask_text(result['reply'])}])
            session['history'] = session['history'][-cfg.session['history_turns'] * 2:]
