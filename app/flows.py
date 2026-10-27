"""YAML-guided slots and code-only, idempotent request submission."""

import json
import re
import secrets
from datetime import timedelta
from uuid import uuid4
from app.config import get_settings
from app.ingest.models import digest, utcnow
from app.log import log, mask_text
from app.rag import envelope


class Flows:
    def __init__(self, store):
        self.store = store

    def definition(self, name):
        return get_settings().flows[name]

    def next_step(self, session):
        active = session.get('active_flow')
        if not active:
            return None
        return next((step for step in self.definition(active['flow'])['steps'] if step['slot'] not in active['slots']), None)

    def status(self, session):
        active = session.get('active_flow')
        if not active:
            return envelope('NOT_FOUND')
        if active.get('change_requested'):
            return envelope('NEEDS_MORE_INFO', {'options': self.definition(active['flow'])['change_buttons']}, display=get_settings().conversation['safe']['change'])
        step = self.next_step(session)
        return envelope('OK', {'flow': active['flow'], 'status': active['status'], 'slots': active['slots'], 'options': step.get('options', []) if step else self.definition(active['flow'])['confirm_buttons']}, display=step['question'] if step else active.get('summary', ''))

    def cancel(self, session):
        active = session.get('active_flow')
        if active and active.get('pending_token'):
            self.store.patch('pending_actions', {'token': active['pending_token']}, {'$set': {'status': 'cancelled'}})
        session['active_flow'] = None
        session.pop('postal_pin', None)
        return envelope('OK', display=get_settings().messages['flow']['cancelled'])

    def set_slots(self, session, name, **values):
        cfg, active = get_settings(), session.get('active_flow')
        if session['auth']['state'] != 'VERIFIED':
            return envelope('NOT_VERIFIED', display=cfg.conversation['safe']['not_verified'])
        if active and active['flow'] != name:
            return envelope('FLOW_ACTIVE', display=cfg.messages['flow']['busy'].format(flow=active['flow']))
        account = self.store.one('accounts', {'account_id': session['auth']['account_id']})
        if account['status'] == 'frozen':
            return envelope('INVALID_ARGUMENT', display=cfg.conversation['safe']['frozen'])
        active = active or {'flow': name, 'slots': {}, 'status': 'COLLECTING', 'updated_at': utcnow()}
        if active.get('change_requested') and not values:
            return self.status(session)
        definition = self.definition(name)
        slots = dict(active['slots'])
        for step in definition['steps']:
            value = values.get(step['slot'])
            if value is None:
                continue
            valid = True
            if step['type'] == 'choice':
                option = next((option for option in step['options'] if str(value).lower() in [str(option['value']).lower(), option['label'].lower(), *[str(v).lower() for v in option['synonyms']]]), None)
                valid = option is not None
                value = option['value'] if option else value
            elif step['type'] == 'address':
                valid = isinstance(value, dict) and value.get('mode') in {option['value'] for option in step['options']}
                if valid and value['mode'] == 'new':
                    for key, rules in step['new_address_fields'].items():
                        text = str(value.get(key) or '')
                        if rules.get('optional') and not text:
                            continue
                        valid &= bool(text) and len(text) >= rules.get('min', 0) and len(text) <= rules.get('max', len(text))
                        if 'digits' in rules:
                            valid &= bool(re.fullmatch(r'\d{' + str(rules['digits']) + '}', text))
            if not valid:
                if step['type'] == 'address' and isinstance(value, dict) and value.get('mode') == 'new':
                    active['address_draft'] = value
                session['active_flow'] = active
                return envelope('INVALID_ARGUMENT', {'field': step['slot'], 'allowed': step.get('options', [])}, display=step['on_invalid'], hint=step['question'])
            slots[step['slot']] = value
        if name == 'debit_card' and slots.get('card_type') and self.store.one('requests', {'user_id': session['auth']['user_id'], 'type': name, 'slots.card_type': slots['card_type'], 'status': 'pending'}):
            return envelope('INVALID_ARGUMENT', display=cfg.conversation['safe']['duplicate'])
        if active.get('pending_token'):
            self.store.patch('pending_actions', {'token': active['pending_token']}, {'$set': {'status': 'superseded'}})
        active.update(slots=slots, status='COLLECTING', updated_at=utcnow(), pending_token=None)
        active.pop('change_requested', None)
        if 'address' in slots:
            active.pop('address_draft', None)
        session['active_flow'] = active
        return self.status(session) if self.next_step(session) else self.summary(session)

    def summary(self, session):
        active, cfg = session.get('active_flow'), get_settings()
        if not active:
            return envelope('NOT_FOUND')
        if active.get('change_requested'):
            return self.status(session)
        if self.next_step(session):
            return self.status(session)
        if active['status'] == 'AWAITING_CONFIRM':
            return envelope('OK', display=active['summary'])
        definition, slots = self.definition(active['flow']), active['slots']
        values = dict(slots)
        for step in definition['steps']:
            if step['type'] == 'choice':
                values[step['slot'] + '_label'] = next(option['label'] for option in step['options'] if option['value'] == slots[step['slot']])
        address = slots['address']
        if address['mode'] == 'on_file':
            user = self.store.one('users', {'user_id': session['auth']['user_id']})
            values['address_text'] = cfg.conversation['safe']['address_on_file'].format(**user['address'])
        else:
            values['address_text'] = mask_text(', '.join(str(value) for key, value in address.items() if key != 'mode'))
        token = uuid4().hex
        summary = definition['summary_template'].format(**values)
        self.store.put('pending_actions', {'token': token}, {'token': token, 'user_id': session['auth']['user_id'], 'account_id': session['auth']['account_id'], 'session_id': session['session_id'], 'flow': active['flow'], 'slots': slots, 'status': 'pending', 'expires_at': utcnow() + timedelta(minutes=cfg.flow['pending_action_minutes'])})
        active.update(status='AWAITING_CONFIRM', pending_token=token, summary=summary, updated_at=utcnow())
        return envelope('OK', {'options': definition['confirm_buttons']}, display=summary)

    def submit(self, session):
        active, cfg = session.get('active_flow'), get_settings()
        if not active or active['status'] != 'AWAITING_CONFIRM' or session['auth']['state'] != 'VERIFIED':
            return envelope('INVALID_ARGUMENT', display=cfg.conversation['safe']['no_submit'])
        pending = self.store.one('pending_actions', {'token': active['pending_token'], 'session_id': session['session_id'], 'user_id': session['auth']['user_id'], 'account_id': session['auth']['account_id'], 'status': 'pending'})
        if not pending or pending['expires_at'] <= utcnow():
            session['active_flow'] = None
            return envelope('EXPIRED', display=cfg.messages['flow']['expired'])
        if pending['slots'] != active['slots']:
            return envelope('INVALID_ARGUMENT', display=cfg.messages['flow']['confirm_unclear'])
        account = self.store.one('accounts', {'account_id': session['auth']['account_id']})
        if account['status'] == 'frozen':
            return envelope('INVALID_ARGUMENT', display=cfg.conversation['safe']['frozen'])
        definition, now = self.definition(active['flow']), utcnow()
        key = digest(json.dumps([session['auth']['user_id'], active['flow'], active['slots'], active['pending_token']], sort_keys=True))
        selector = active['slots'].get('delivery', active['slots'].get('card_type'))
        ref = f"{definition['reference_prefix']}-{now:%y%m}-{secrets.randbelow(10 ** cfg.conversation['agent']['reference_digits']):0{cfg.conversation['agent']['reference_digits']}d}"
        request = {'idempotency_key': key, 'request_id': uuid4().hex, 'source_token': active['pending_token'], 'user_id': session['auth']['user_id'], 'account_id': session['auth']['account_id'], 'type': active['flow'], 'slots': active['slots'], 'details': active['slots'], 'ref_no': ref, 'status': 'pending', 'created_at': now, 'submitted_at': now, 'expected_by': (now + timedelta(days=definition['expected_days'][selector])).date().isoformat()}
        try:
            # One atomic set-on-insert plus the unique key makes retries safe after ambiguous timeouts.
            self.store.patch('requests', {'idempotency_key': key}, {'$setOnInsert': request}, upsert=True)
            request = self.store.one('requests', {'idempotency_key': key})
        except Exception as exc:
            log.step('FLOW', 'submit failed', error=type(exc).__name__)
            try:
                request = self.store.one('requests', {'idempotency_key': key})
            except Exception:
                return envelope('UPSTREAM_ERROR', display=cfg.conversation['safe']['submit_unknown'])
            if not request:
                return envelope('UPSTREAM_ERROR', display=cfg.messages['flow']['submitted_error'])
        session['last_submission'] = request
        session['active_flow'] = None
        try:
            self.store.patch('pending_actions', {'token': pending['token']}, {'$set': {'status': 'confirmed', 'idempotency_key': key}})
            audit_id = uuid4().hex
            self.store.put('audit_log', {'event_id': audit_id}, {'event_id': audit_id, 'event_type': 'submit', 'session_id': session['session_id'], 'user_id': session['auth']['user_id'], 'ref_no': request['ref_no'], 'created_at': now, 'expires_at': now + timedelta(days=cfg.retention['audit_log_days'])})
        except Exception as exc:
            # The request is already durably created; an audit outage must not falsely report failure.
            log.step('FLOW', 'post-submit audit delayed', error=type(exc).__name__)
        return envelope('OK', {'ref_no': request['ref_no'], 'expected_by': request['expected_by']}, display=definition['success_template'].format(**request))

    def fast(self, session, text):
        active, cfg = session.get('active_flow'), get_settings()
        if not active:
            return None
        lower = text.strip().lower()
        if utcnow() - active['updated_at'] >= timedelta(minutes=cfg.flow['idle_minutes']):
            session['active_flow'] = None
            return envelope('EXPIRED', display=cfg.messages['flow']['expired'])
        if lower in cfg.conversation['commands']['cancel']:
            return self.cancel(session)
        if active.get('change_requested'):
            for step in self.definition(active['flow'])['steps']:
                if lower.replace(' ', '_') == step['slot']:
                    active['slots'].pop(step['slot'], None)
                    active.pop('change_requested')
                    return self.status(session)
            if any(word.lower() in lower for word in cfg.conversation['public']):
                return None
            return self.status(session)
        if active['status'] == 'AWAITING_CONFIRM':
            if lower in cfg.conversation['commands']['confirm']:
                return self.submit(session)
            if any(lower.startswith(word) for word in cfg.conversation['commands']['change']):
                for step in self.definition(active['flow'])['steps']:
                    if step['slot'] in lower or step['slot'] == 'pages' and re.search(r'\d+', lower):
                        match = re.search(r'\d+', lower)
                        if match and step['slot'] == 'pages':
                            return self.set_slots(session, active['flow'], pages=int(match[0]))
                        active['slots'].pop(step['slot'], None)
                        if active.get('pending_token'):
                            self.store.patch('pending_actions', {'token': active['pending_token']}, {'$set': {'status': 'superseded'}})
                            active['pending_token'] = None
                        active['status'] = 'COLLECTING'
                        return self.status(session)
                if active.get('pending_token'):
                    self.store.patch('pending_actions', {'token': active['pending_token']}, {'$set': {'status': 'superseded'}})
                active.update(status='COLLECTING', pending_token=None, change_requested=True)
                return self.status(session)
            if not any(word.lower() in lower for word in cfg.conversation['public']):
                return envelope('NEEDS_MORE_INFO', display=cfg.messages['flow']['confirm_unclear'])
            return None
        step = self.next_step(session)
        if step and step['type'] in {'choice', 'address'}:
            for option in step.get('options', []):
                aliases = [str(option['value']).lower(), option['label'].lower(), *[str(value).lower() for value in option['synonyms']]]
                if lower in aliases or step['slot'] == 'pages' and any(re.fullmatch(r'the ' + re.escape(alias) + r' one', lower) for alias in aliases):
                    value = {'mode': option['value']} if step['type'] == 'address' else option['value']
                    return self.set_slots(session, active['flow'], **{step['slot']: value})
            if step['slot'] == 'pages' and lower.isdigit():
                return self.set_slots(session, active['flow'], pages=int(lower))
        return None
