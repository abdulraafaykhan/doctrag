"""Deterministic verification consumes raw input before any storage or model call."""

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from datetime import timedelta
from weakref import WeakValueDictionary

from fastapi import HTTPException

from app.bank.service import match_customer
from app.config import get_settings
from app.db import messages_col, sessions_col
from app.services.audit import audit_event
from app.services.rate_limits import check_rate_limit, rate_message
from app.sessions.store import aware, delete_private_files, now_utc
from app.verification import messages as text
from app.verification.lockout import (
    account_hash,
    failed_attempt,
    is_locked,
    reset_failures,
)
from app.verification.masking import mask_sensitive
from app.verification.otp import accepts_otp, send_otp

log = logging.getLogger(__name__)
verify_windows = {}


@dataclass(frozen=True)
class FlowReply:
    answer: str | None = None
    replay: str | None = None
    clear_history: bool = False


async def _event(name: str, session_id: str, *, state: str = "") -> None:
    log.info("%s session=%s state=%s", name, session_id[:6], state)
    if name in {
        "verify_started",
        "verify_failed",
        "verify_locked",
        "otp_sent",
        "verify_ok",
    }:
        await audit_event(name, session_id=session_id)


async def _save(session: dict, flow: dict) -> None:
    session["flow"] = flow
    await sessions_col().update_one({"_id": session["_id"]}, {"$set": {"flow": flow}})
    await _event("verify_step", session["_id"], state=flow["state"])


async def start(session: dict, question: str) -> str:
    if session.get("flow", {}).get("state", "idle") != "idle":
        return text.CONTINUE
    await _save(
        session,
        {
            "state": "awaiting_account",
            "pending_question": mask_sensitive(question),
            "tries": 0,
        },
    )
    await _event("verify_started", session["_id"])
    return text.ACCOUNT


def _account(value: str) -> str | None:
    value = re.sub(r"[ -]", "", value.strip())
    return value if re.fullmatch(r"[0-9]{12}", value) else None


def _mobile(value: str) -> str | None:
    value = re.sub(r"[ -]", "", value.strip())
    if value.startswith("+91"):
        value = value[3:]
    elif len(value) == 12 and value.startswith("91"):
        value = value[2:]
    return value if re.fullmatch(r"[6-9][0-9]{9}", value) else None


def _numeric(value: str) -> bool:
    return bool(re.fullmatch(r"[+0-9 -]+", value.strip()))


async def _reserve_attempt(ip: str, session_id: str) -> None:
    retry = check_rate_limit(
        verify_windows,
        ip,
        get_settings().verify_rate_per_ip_per_minute,
        time.monotonic(),
    )
    if retry:
        await audit_event(
            "rate_limited", session_id=session_id, outcome="verification_ip"
        )
        raise HTTPException(
            429, rate_message(retry), headers={"Retry-After": str(retry)}
        )


async def _account_step(session: dict, flow: dict, raw: str, ip: str) -> FlowReply:
    account = _account(raw)
    if account is None:
        return FlowReply(text.ACCOUNT if _numeric(raw) else None)
    # Raw account/mobile are transient server flow state only, cleared on finish/reset.
    flow.update(state="awaiting_mobile", account=account)
    await _save(session, flow)
    return FlowReply(text.MOBILE)


async def _mobile_step(session: dict, flow: dict, raw: str, ip: str) -> FlowReply:
    session_id = session["_id"]
    mobile = _mobile(raw)
    if mobile is None:
        return FlowReply(text.MOBILE if _numeric(raw) else None)
    await _reserve_attempt(ip, session_id)
    if await is_locked(flow["account"]):
        await _save(session, {"state": "idle"})
        await _event("verify_locked", session_id)
        return FlowReply(text.FAILED)
    customer_id = await match_customer(flow["account"], mobile)
    if customer_id is None:
        locked = await failed_attempt(flow["account"])
        await _event("verify_failed", session_id)
        tries = flow.get("tries", 0) + 1
        if locked or tries >= get_settings().verify_max_tries:
            await _save(session, {"state": "idle"})
            if locked:
                await _event("verify_locked", session_id)
            return FlowReply(text.FAILED)
        await _save(
            session,
            {
                "state": "awaiting_account",
                "pending_question": flow["pending_question"],
                "tries": tries,
            },
        )
        return FlowReply(text.RETRY)
    send_otp()
    flow.update(
        state="awaiting_otp",
        mobile=mobile,
        customer_id=customer_id,
        otp_sent_at=now_utc(),
    )
    await _save(session, flow)
    await _event("otp_sent", session_id)
    return FlowReply(text.OTP.format(last4=mobile[-4:]))


async def _otp_step(session: dict, flow: dict, raw: str, ip: str) -> FlowReply:
    session_id = session["_id"]
    if raw.strip().casefold() == "resend":
        await _reserve_attempt(ip, session_id)
        send_otp()
        flow["otp_sent_at"] = now_utc()
        await _save(session, flow)
        await _event("otp_sent", session_id)
        return FlowReply(text.OTP.format(last4=flow["mobile"][-4:]))
    if not _numeric(raw):
        return FlowReply()
    if (
        aware(flow["otp_sent_at"]) + timedelta(minutes=get_settings().otp_valid_minutes)
        <= now_utc()
    ):
        return FlowReply(text.OTP_EXPIRED)
    if not accepts_otp(raw.strip()):
        return FlowReply(text.OTP_FORMAT)
    await _reserve_attempt(ip, session_id)
    if await is_locked(flow["account"]):
        await _save(session, {"state": "idle"})
        return FlowReply(text.FAILED)
    await reset_failures(flow["account"])
    # A different customer must never inherit private files, including in-flight uploads.
    changed = session.get("attachment_customer_id") not in (
        None,
        flow["customer_id"],
    )
    if changed:
        # Keep the bearer session but erase the previous customer's entire context.
        await delete_private_files(session_id)
        await messages_col().delete_many({"session_id": session_id})
        await audit_event("customer_changed", session_id=session_id)
        log.info("customer_changed session=%s", session_id[:6])
    now = now_utc()
    verified = {
        "customer_id": flow["customer_id"],
        "verified_at": now,
        "last_verified_activity_at": now,
    }
    await sessions_col().update_one(
        {"_id": session_id},
        {
            "$set": {
                "verified": verified,
                "attachment_customer_id": flow["customer_id"],
                "flow": {"state": "idle"},
            }
        },
    )
    session.update(
        verified=verified,
        flow={"state": "idle"},
        attachment_customer_id=flow["customer_id"],
    )
    await _event("verify_ok", session_id)
    return (
        FlowReply(text.CUSTOMER_CHANGED, clear_history=True)
        if changed
        else FlowReply(replay=flow["pending_question"])
    )


async def _advance(session: dict, raw: str, ip: str) -> FlowReply:
    flow = dict(session.get("flow") or {"state": "idle"})
    state = flow["state"]
    if state == "idle":
        return FlowReply()
    session_id = session["_id"]
    if raw.strip().casefold() in {"cancel", "stop", "never mind"}:
        await _save(session, {"state": "idle"})
        await _event("verify_cancelled", session_id)
        return FlowReply(text.CANCELLED)
    handlers = {
        "awaiting_account": _account_step,
        "awaiting_mobile": _mobile_step,
        "awaiting_otp": _otp_step,
    }
    return await handlers[state](session, flow, raw, ip)


_account_locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()


async def advance(session: dict, raw: str, ip: str) -> FlowReply:
    account = session.get("flow", {}).get("account")
    if not account:
        return await _advance(session, raw, ip)
    # In the single-worker demo, a success cannot race another session's new lockout.
    key = account_hash(account)
    lock = _account_locks.setdefault(key, asyncio.Lock())
    async with lock:
        return await _advance(session, raw, ip)
