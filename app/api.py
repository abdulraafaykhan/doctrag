"""Session-bound HTTP boundary; database identity never comes from a model."""

from datetime import timedelta
from mimetypes import guess_type
from uuid import uuid4

from fastapi import APIRouter, File, Form, Request, Response, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app.config import get_settings
from app.ingest.models import utcnow
from app.ingest.validation import InvalidFile
from app.log import log, mask_payload
from app.prepass import extract
from app.usage import token_scope, response_tokens, meter


class SessionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_id: str = Field(min_length=1, max_length=get_settings().ui['identifier_chars'])
    session_id: str | None = None
    new_chat: bool = False


class ChatBody(SessionBody):
    message: str = ""
    attachment_ids: list[str] = Field(default_factory=list)
    request_id: str = Field(default_factory=lambda: uuid4().hex, max_length=get_settings().ui['identifier_chars'])


class EventBody(BaseModel):
    session_id: str
    event: str
    detail: dict | str = ""


class APIError(Exception):
    def __init__(self, code, message_key, status):
        self.code, self.message_key, self.status = code, message_key, status


def error_response(exc):
    cfg = get_settings()
    message = cfg.ui[exc.message_key].format(count=cfg.uploads['max_files_per_message'], size=cfg.uploads['max_mb'], minutes=cfg.session['idle_minutes'])
    return JSONResponse({**({'code': exc.code} if exc.code == 'SESSION_EXPIRED' else {}), "error": exc.code, "message": message}, status_code=exc.status)


class API:
    def __init__(self, chat, uploads):
        self.chat, self.uploads, self.store = chat, uploads, chat.store
        self.router = APIRouter(prefix="/api")
        for path, method, endpoint in [
            ("/session", "POST", self.session), ("/session", "GET", self.restore),
            ("/chat", "POST", self.turn), ("/upload", "POST", self.upload),
            ("/upload/{attachment_id}", "GET", self.status), ("/upload/{attachment_id}", "DELETE", self.delete),
            ("/files/{attachment_id}", "GET", self.file), ("/ui-event", "POST", self.event),
        ]:
            self.router.add_api_route(path, endpoint, methods=[method])

    def owned(self, request, session_id=None, client_id=None, *, file=False, touch=False):
        cfg = get_settings().ui
        cookie = request.cookies.get(cfg["cookie_name"])
        if not cookie or session_id and cookie != session_id:
            raise APIError("not_found", "not_found", 404)
        session = self.store.one("sessions", {"session_id": cookie})
        if session and (session['expires_at'] <= utcnow() or utcnow() - session.get('last_active_at', utcnow()) >= timedelta(minutes=get_settings().session['idle_minutes'])):
            self.uploads.expire_session(cookie)
            session = None
        if not session:
            raise APIError('SESSION_EXPIRED', 'session_expired', 410)
        if not file:
            client = client_id or request.headers.get(cfg["client_header"]) or request.query_params.get("client_id")
            if client != session.get("client_id"):
                raise APIError("second_tab", "second_tab", 409)
            self.store.patch("sessions", {"session_id": cookie}, {"$set": {"last_seen": utcnow(), **({'last_active_at': utcnow()} if touch else {})}})
        return session

    def attachment(self, session_id, attachment_id):
        try:
            return self.uploads._owned(session_id, attachment_id)["attachment"]
        except FileNotFoundError:
            raise APIError("not_found", "not_found", 404) from None

    def snapshot(self, session):
        meter.restore(session['session_id'], session.get('counters', {}).get('tokens', {}))
        documents = self.store.find("documents", {"owner_key": session["session_id"], "namespace": "user"})
        cards = {row["attachment"]["id"]: {**row["attachment"], "status": row["attachment"]["status"] if row["expires_at"] > utcnow() else get_settings().ui["expired"]} for row in documents}
        history = session.get("ui_history", [])
        for turn in history:
            turn["attachments"] = [cards.get(card["id"], {**card, "status": card['status'] if card.get('status') == get_settings().ui['deleted'] else get_settings().ui["expired"]}) for card in turn.get("attachments", [])]
        auth = session["auth"]
        user = self.store.one("users", {"user_id": auth.get("user_id")}) if auth["state"] == "VERIFIED" else None
        limits = get_settings().uploads
        return jsonable_encoder({"tokens": response_tokens(session["session_id"]), "session_id": session["session_id"], "history": history, "attachments": list(cards.values()), "auth_state": auth["state"], "auth_name": user["full_name"].split()[0] if user else "", "options": self.chat.options(session), "flow_state": self.flow(session), "upload_limits": {"max_files": limits['max_files_per_message'], "max_mb": limits['max_mb'], "allowed_extensions": limits['allowed_extensions']}})

    @staticmethod
    def flow(session):
        active = session.get("active_flow")
        # Pending tokens and postal addresses are private implementation state.
        return {"flow": active["flow"], "status": active["status"]} if active else None

    def session(self, body: SessionBody, request: Request, response: Response):
        cfg, now = get_settings().ui, utcnow()
        cookie = request.cookies.get(cfg["cookie_name"])
        if body.session_id and body.session_id != cookie:
            raise APIError("not_found", "not_found", 404)
        previous = self.store.one("sessions", {"session_id": cookie}) if cookie else None
        if previous and (previous['expires_at'] <= now or now - previous.get('last_active_at', now) >= timedelta(minutes=get_settings().session['idle_minutes'])):
            self.uploads.expire_session(cookie)
            previous = None
        if body.new_chat and previous and previous.get("client_id") != body.client_id:
            raise APIError("second_tab", "second_tab", 409)
        sid = None if body.new_chat else cookie
        row = self.store.one("sessions", {"session_id": sid}) if sid else None
        if row and row.get("client_id") and row.get("client_id") != body.client_id and row.get("last_seen", now) + timedelta(seconds=cfg["tab_idle_seconds"]) > now:
            raise APIError("second_tab", "second_tab", 409)
        session = self.chat.sessions.load(sid)
        # Claim atomically so simultaneous opening tabs cannot both become owners.
        claimed = self.store.patch("sessions", {"session_id": session["session_id"], "$or": [{"client_id": {"$exists": False}}, {"client_id": body.client_id}, {"last_seen": {"$lte": now - timedelta(seconds=cfg["tab_idle_seconds"])}}]}, {"$set": {"client_id": body.client_id, "last_seen": now}})
        if not claimed.modified_count and session.get("client_id") != body.client_id:
            raise APIError("second_tab", "second_tab", 409)
        response.set_cookie(cfg["cookie_name"], session["session_id"], httponly=True, samesite="strict", secure=request.url.scheme == "https", max_age=get_settings().session["expires_hours"] * 3600)
        log.step("SESSION", "restore" if row else "create", session=session["session_id"])
        return self.snapshot(session)

    def restore(self, request: Request):
        session = self.owned(request)
        return self.snapshot(self.chat.sessions.load(session["session_id"]))

    def turn(self, body: ChatBody, request: Request):
        with token_scope(body.session_id or request.cookies.get(get_settings().ui['cookie_name']), self.store):
            return self._metered_turn(body, request)

    def _metered_turn(self, body, request):
        session = self.owned(request, body.session_id, body.client_id, touch=True)
        self.chat.sessions.load(session['session_id'])
        try:
            with self.chat.sessions.lock(session['session_id']):
                session = self.store.one('sessions', {'session_id': session['session_id']})
                return self.turn_locked(body, session)
        except RuntimeError as exc:
            if str(exc) == 'SESSION_BUSY':
                raise APIError('busy', 'busy', 409) from None
            raise

    def turn_locked(self, body, session):
        if len(body.attachment_ids) > get_settings().uploads["max_files_per_message"]:
            raise APIError("too_many_files", "too_many", 422)
        cards = [self.attachment(session["session_id"], key) for key in body.attachment_ids]
        if any(card["status"] not in {"Ready", "Partial"} for card in cards):
            raise APIError("processing", "processing", 409)
        cached = next((row for row in session.get("ui_history", []) if row.get("request_id") == body.request_id), None)
        if cached:
            return {**cached["response"], 'tokens': response_tokens(session['session_id'])}
        message = body.message or (get_settings().ui["files_only"] if cards else "")
        result = self.chat.turn_locked(message, session_id=session["session_id"], attachments=body.attachment_ids)
        current = self.store.one("sessions", {"session_id": session["session_id"]})
        if current is None:
            raise APIError('SESSION_EXPIRED', 'session_expired', 410)
        state = self.snapshot(current)
        response = {"tokens": response_tokens(session["session_id"]), "reply_markdown": result["reply"], "citations": result.get("citations", []), "options": result.get("options", []), "auth_state": state["auth_state"], "auth_name": state["auth_name"], "flow_state": state["flow_state"], "attachments": state["attachments"], "notices": [], "tools_called": result.get("tools_called", [])}
        record = {"id": uuid4().hex, "request_id": body.request_id, "message": extract(message).text, "attachments": cards, "response": mask_payload(response), "created_at": utcnow()}
        history = [*current.get("ui_history", []), record][-get_settings().ui["ui_history_turns"]:]
        self.store.patch("sessions", {"session_id": session["session_id"]}, {"$set": {"ui_history": history}})
        log.step("API", "response", chars=len(result["reply"]), tools=response["tools_called"], auth=response["auth_state"])
        with log.span('RESPONSE', 'response', module=__name__, function='API.turn_locked', chars=len(result['reply'])):
            pass
        return jsonable_encoder(response)

    async def upload(self, request: Request, file: UploadFile = File(...), session_id: str = Form(...), client_id: str = Form(...)):
        with token_scope(session_id, self.store):
            return await self._metered_upload(request, file, session_id, client_id)

    async def _metered_upload(self, request, file, session_id, client_id):
        self.owned(request, session_id, client_id, touch=True)
        cfg = get_settings()
        content = await file.read(cfg.uploads["max_mb"] * 1024 * 1024 + cfg.ui["upload_read_extra_bytes"])
        log.step("UPLOAD", "received", name=file.filename, bytes=len(content))
        from starlette.concurrency import run_in_threadpool
        try:
            card = await run_in_threadpool(self.uploads.save, session_id, file.filename or "", content)
        except (InvalidFile, ValueError) as exc:
            log.step("UPLOAD", "rejected", reason=str(exc))
            return JSONResponse({"error": "invalid_file", "message": str(exc)}, status_code=422)
        return {**card, "attachment_id": card["id"]}

    def status(self, attachment_id: str, request: Request):
        session = self.owned(request)
        return self.attachment(session["session_id"], attachment_id)

    def delete(self, attachment_id: str, request: Request):
        session = self.owned(request)
        self.attachment(session["session_id"], attachment_id)
        self.uploads.delete(session["session_id"], attachment_id)
        history = session.get('ui_history', [])
        for turn in history:
            for card in turn.get('attachments', []):
                if card['id'] == attachment_id:
                    card['status'] = get_settings().ui['deleted']
        self.store.patch('sessions', {'session_id': session['session_id']}, {'$set': {'ui_history': history}})
        return {"deleted": True}

    def file(self, attachment_id: str, request: Request):
        session = self.owned(request, file=True)
        self.attachment(session["session_id"], attachment_id)
        try:
            path = self.uploads.get_file(session["session_id"], attachment_id)
        except FileNotFoundError:
            raise APIError("not_found", "not_found", 404) from None
        card = self.attachment(session["session_id"], attachment_id)
        inline = card["kind"] == "image" or path.suffix.lower() in {".pdf", ".txt", ".md", ".csv"}
        return FileResponse(path, media_type=guess_type(card["name"])[0] or "application/octet-stream", filename=card["name"], content_disposition_type="inline" if inline else "attachment", headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})

    def event(self, body: EventBody, request: Request):
        self.owned(request, body.session_id, touch=True)
        log.step("UI", body.event[:get_settings().ui["event_chars"]], detail=body.detail)
        return {"ok": True}
