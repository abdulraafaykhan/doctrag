"""Code-owned authentication, hashed OTPs and leased conversation sessions."""

import hashlib
import hmac
import secrets
import re
from contextlib import contextmanager
from datetime import timedelta
from uuid import uuid4

from app.config import get_settings
from app.ingest.models import utcnow
from app.ingest.store import Store
from app.log import mask_text
from app.rag import envelope
from app.log import log


def verify_otp(code, auth, *, now=None):
    now = now or utcnow()
    if now >= auth['otp_expires_at'] or len(code) != get_settings().auth['otp_length'] or not code.isdigit():
        return False
    return get_settings().features['otp_demo_mode'] or hmac.compare_digest(hashlib.sha256(code.encode()).hexdigest(), auth['otp_hash'])


class Sessions:
    def __init__(self, store=None):
        self.store = store or Store()

    def save(self, session):
        self.store.put('sessions', {'session_id': session['session_id']}, session)

    def audit(self, session, event):
        stamp, key = utcnow(), uuid4().hex
        self.store.put('audit_log', {'event_id': key}, {'event_id': key, 'session_id': session['session_id'], 'event_type': event, 'created_at': stamp, 'expires_at': stamp + timedelta(days=get_settings().retention['audit_log_days'])})
        log.step('AUTH', event, state=session['auth']['state'])

    def load(self, session_id=None):
        now, cfg = utcnow(), get_settings()
        row = self.store.one('sessions', {'session_id': session_id}) if session_id else None
        existing = row is not None and row.get('expires_at', now) > now
        if row is None or row.get('expires_at', now) <= now:
            row = {'session_id': uuid4().hex, 'auth': {'state': 'ANONYMOUS', 'failures': 0, 'held': [], 'candidates': {}}, 'history': [], 'active_flow': None, 'attachments': [], 'counters': {}, 'created_at': now}
        if row['auth']['state'] == 'VERIFIED' and now - row.get('last_active_at', now) >= timedelta(minutes=cfg.auth['session_idle_minutes']):
            previous = row.get('last_active_at')
            self.clear(row)
            row['expired_auth'] = True
            # Conditional field updates cannot erase a lease acquired by another worker.
            self.store.patch('sessions', {'session_id': row['session_id'], 'last_active_at': previous, '$or': [{'lease_until': {'$exists': False}}, {'lease_until': {'$lte': now}}]}, {'$set': {'auth': row['auth'], 'active_flow': None, 'history': [], 'last_submission': None}})
        if not existing:
            row.update(last_active_at=now, expires_at=now + timedelta(hours=cfg.session['expires_hours']))
            self.save(row)
        return row

    def clear(self, session):
        session['auth'] = {'state': 'ANONYMOUS', 'failures': 0, 'held': [], 'candidates': {}}
        session['active_flow'] = None
        session['history'] = []
        session.pop('last_submission', None)
        session.pop('postal_pin', None)

    def hold(self, session, message, attachments=()):
        if not re.search(r'\w', re.sub(r'\[(?:mobile|account|secret)\]', '', message)) and not attachments:
            return
        held = session['auth'].setdefault('held', [])
        value = {'text': mask_text(message), 'attachments': list(attachments)}
        for previous in held:
            if previous['text'] == value['text']:
                previous['attachments'] = list(dict.fromkeys(previous['attachments'] + value['attachments']))
                return
        if value not in held and len(held) < get_settings().auth['held_messages_max']:
            held.append(value)

    def fail(self, session):
        auth, cfg = session['auth'], get_settings()
        auth['failures'] = auth.get('failures', 0) + 1
        self.audit(session, 'otp_failed' if auth['state'] == 'AWAITING_OTP' else 'pair_failed')
        if auth['failures'] >= cfg.auth['otp_max_attempts']:
            auth['locked_until'] = utcnow() + timedelta(minutes=cfg.auth['lock_minutes'])
            return envelope('LOCKED', display=cfg.messages['auth']['locked'].format(minutes=cfg.auth['lock_minutes']))
        return None

    def start(self, session, *, resend=False):
        cfg, auth = get_settings(), session['auth']
        if auth.get('locked_until', utcnow()) > utcnow():
            return envelope('LOCKED', display=cfg.messages['auth']['locked'].format(minutes=cfg.auth['lock_minutes']))
        if auth.get('locked_until'):
            auth['failures'] = 0
            auth.pop('locked_until')
        if auth['state'] == 'AWAITING_OTP' and not resend:
            return envelope('OTP_SENT', display=cfg.messages['auth']['otp_sent'])
        candidates = auth.setdefault('candidates', {})
        missing = 'both' if not candidates else 'mobile' if not candidates.get('mobile') else 'account' if not candidates.get('account') else None
        if missing:
            return envelope('NEED_' + missing.upper(), display=cfg.messages['auth']['need_' + missing])
        account = self.store.one('accounts', {'account_number': candidates['account']})
        user = self.store.one('users', {'mobile': candidates['mobile']})
        if not user or not account or user['user_id'] != account['user_id'] or user.get('status') != 'active':
            return self.fail(session) or envelope('NOT_VERIFIED', display=cfg.messages['auth']['generic_fail'])
        auth.update(state='AWAITING_OTP', candidate_user_id=user['user_id'], candidate_account_id=account['account_id'], otp_hash=hashlib.sha256(str(secrets.randbelow(10 ** cfg.auth['otp_length'])).zfill(cfg.auth['otp_length']).encode()).hexdigest(), otp_expires_at=utcnow() + timedelta(minutes=cfg.auth['otp_expiry_minutes']))
        self.audit(session, 'otp_sent')
        return envelope('OTP_SENT', display=cfg.messages['auth']['otp_sent'])

    def otp(self, session, code):
        cfg, auth = get_settings(), session['auth']
        if auth.get('locked_until', utcnow()) > utcnow():
            return envelope('LOCKED', display=cfg.messages['auth']['locked'].format(minutes=cfg.auth['lock_minutes']))
        if auth.get('locked_until'):
            auth.pop('locked_until')
            auth['failures'] = 0
        if utcnow() >= auth['otp_expires_at']:
            return self.fail(session) or envelope('EXPIRED', display=cfg.messages['auth']['otp_expired'])
        if not verify_otp(code, auth):
            return self.fail(session) or envelope('INVALID_ARGUMENT', display=cfg.messages['auth']['otp_bad_format'])
        auth.update(state='VERIFIED', user_id=auth.pop('candidate_user_id'), account_id=auth.pop('candidate_account_id'), verified_at=utcnow(), failures=0)
        auth.pop('otp_hash', None)
        auth.pop('otp_expires_at', None)
        auth['candidates'] = {}
        self.audit(session, 'otp_verified')
        return envelope('OK', display=cfg.messages['auth']['verified'])

    @contextmanager
    def lock(self, session_id):
        token = uuid4().hex
        cfg, now = get_settings(), utcnow()
        result = self.store.patch('sessions', {'session_id': session_id, '$or': [{'lease_until': {'$exists': False}}, {'lease_until': {'$lte': now}}]}, {'$set': {'lease_token': token, 'lease_until': now + timedelta(seconds=cfg.session['lease_seconds'])}})
        if not result.modified_count:
            raise RuntimeError('SESSION_BUSY')
        try:
            yield
        finally:
            self.store.patch('sessions', {'session_id': session_id, 'lease_token': token}, {'$unset': {'lease_token': '', 'lease_until': ''}})

