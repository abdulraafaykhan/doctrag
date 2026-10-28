"""Owning-session file access, one ingestion worker and TTL cleanup."""

import json
import shutil
from datetime import datetime
from pathlib import Path
from mimetypes import guess_type
from threading import Event, Thread
from time import monotonic
from uuid import uuid4

from app.config import ROOT, get_settings
from app.ingest.models import DocumentMeta, digest, utcnow
from app.ingest.pipeline import Pipeline
from app.ingest.store import Store
from app.ingest.validation import InvalidFile, validate_file
from app.llm import QuotaExceeded
from app.log import log, request_context
from app.usage import token_scope


class UploadService:
    def __init__(self, store=None, pipeline=None):
        self.store = store or Store()
        self.pipeline = pipeline or Pipeline(self.store)

    def save(self, owner_key: str, filename: str, content: bytes):
        cfg = get_settings()
        if not owner_key or owner_key == "bank":
            raise ValueError(cfg.knowledge["messages"]["inaccessible"])
        if len(content) > cfg.uploads["max_mb"] * 1024 * 1024:
            raise InvalidFile(cfg.knowledge["parsing"]["too_large_error"])
        safe_name = Path(filename.replace("\\", "/")).name
        if (
            Path(safe_name).suffix.lower().lstrip(".")
            not in cfg.uploads["allowed_extensions"]
        ):
            raise InvalidFile(cfg.knowledge["parsing"]["unsupported_error"])
        directory = ROOT / "data/uploads" / digest(owner_key)
        directory.mkdir(parents=True, exist_ok=True)
        file_hash = digest(content)
        path = directory / f"{file_hash}{Path(safe_name).suffix.lower()}"
        log.upload_details(parser=path.suffix.lstrip('.'), ocr_pages=0, vlm_pages=0, embed_chunks=0, source='queued')
        previous = self.store.one(
            "documents", {"owner_key": owner_key, "file_hash": file_hash}
        )
        if previous and previous.get("expires_at", utcnow()) > utcnow():
            return {**previous["attachment"], "reused": True}
        path.write_bytes(content)
        try:
            ext = validate_file(path, customer=True)
            if not self.store.reserve(
                owner_key, "uploads_count", 1, cfg.uploads["max_files_per_session"]
            ):
                raise ValueError(cfg.knowledge["messages"]["upload_limit"])
        except Exception:
            path.unlink(missing_ok=True)
            raise
        from datetime import timedelta

        attachment = {
            "id": uuid4().hex,
            "name": safe_name,
            "mime": guess_type(safe_name)[0] or "application/octet-stream",
            "size": len(content),
            "kind": "image" if ext in {"png", "jpg", "jpeg", "webp"} else "document",
            "status": "Uploading",
            "warnings": [],
        }
        doc = {
            **DocumentMeta(title=safe_name).model_dump(),
            "doc_id": "doc_" + digest(f"{owner_key}|{file_hash}")[:24],
            "owner_key": owner_key,
            "namespace": "user",
            "file_hash": file_hash,
            "filename": safe_name,
            "storage_path": str(path),
            "size_bytes": len(content),
            "mime": ext,
            "created_at": utcnow(),
            "expires_at": utcnow() + timedelta(hours=cfg.uploads["expiry_hours"]),
            "processing_state": "queued",
            "attachment": attachment,
            "request_id": request_context.get()["id"] if request_context.get() else None,
            "counts": {},
            "review_flags": [],
        }
        self.store.put("documents", {"doc_id": doc["doc_id"]}, doc)
        self.store.patch(
            "sessions",
            {"session_id": owner_key},
            {"$addToSet": {"attachments": attachment, 'uploads': doc['doc_id']}},
        )
        log.step("UPLOAD", "accepted", attachment=attachment["id"], size=len(content))
        return attachment

    def process(self, owner_key: str, attachment_id: str, *, defer_embeddings=False):
        document = self._owned(owner_key, attachment_id)
        self._status(document, "Processing")
        try:
            result = self.pipeline.ingest(
                Path(document["storage_path"]),
                DocumentMeta.model_validate(document),
                owner_key=owner_key,
                defer_embeddings=defer_embeddings,
            )
            status = (
                "Processing"
                if result.get("paused_quota") or result.get("processing_state") == "needs_retry"
                else "Partial"
                if result["processing_state"] == "partial"
                else "Ready"
            )
            log.upload_details(parser=document['mime'], ocr_pages=result.get('counts', {}).get('ocr_pages', 0),
                               vlm_pages=result.get('counts', {}).get('vlm_pages', 0), embed_chunks=result.get('counts', {}).get('chunks', 0))
            document = self.store.one("documents", {"doc_id": document["doc_id"]})
            self._status(document, status, result.get("warnings", []))
            return document["attachment"]
        except QuotaExceeded:
            self._status(
                document,
                "Processing",
                [get_settings().knowledge["messages"]["quota_wait"]],
            )
            return document["attachment"]
        except Exception as exc:
            self._status(
                document,
                "Failed",
                [
                    str(exc)
                    if isinstance(exc, InvalidFile)
                    else get_settings().knowledge["parsing"]["corrupt_error"]
                ],
            )
            return document["attachment"]

    def _status(self, document, status, warnings=None):
        document["attachment"].update(
            status=status, warnings=warnings or document.get("warnings", [])
        )
        self.store.patch(
            "documents",
            {"doc_id": document["doc_id"]},
            {"$set": {"attachment": document["attachment"]}},
        )
        log.step(
            "UPLOAD", "status", attachment=document["attachment"]["id"], status=status
        )

    def _owned(self, owner, attachment):
        document = self.store.one(
            "documents",
            {
                "owner_key": owner,
                "attachment.id": attachment,
                "expires_at": {"$gt": utcnow()},
            },
        )
        if not document:
            raise FileNotFoundError(
                get_settings().knowledge["messages"]["inaccessible"]
            )
        return document

    def get_file(self, owner_key, attachment_id):
        document = self._owned(owner_key, attachment_id)
        path = Path(document["storage_path"]).resolve()
        root = (ROOT / "data/uploads" / digest(owner_key)).resolve()
        if not path.is_relative_to(root) or not path.exists():
            raise FileNotFoundError(
                get_settings().knowledge["messages"]["inaccessible"]
            )
        return path

    def delete(self, owner_key, attachment_id):
        document = self._owned(owner_key, attachment_id)
        return self._delete_document(document)

    def _delete_document(self, document, counts=None):
        counts = counts if counts is not None else {}
        doc_id, owner = document["doc_id"], document["owner_key"]
        if owner == "bank":
            raise ValueError("Bank documents are not customer uploads")
        for name in ("chunks", "sections", "rate_rows", "rate_tables", "extractions"):
            removed = self.store.delete(
                name,
                {
                    "doc_id": doc_id,
                    "owner_key": owner,
                },
            )
            counts[name] = counts.get(name, 0) + removed
        path = Path(document["storage_path"]).resolve()
        if path.is_relative_to((ROOT / "data/uploads" / digest(owner)).resolve()):
            path.unlink(missing_ok=True)
        (ROOT / "data/ingest" / f"{doc_id}.json").unlink(missing_ok=True)
        for name, query in [('documents', {'doc_id': doc_id, 'owner_key': owner})]:
            counts[name] = counts.get(name, 0) + self.store.delete(name, query)
        for cached in (ROOT / "data/test_cache").glob(f"vision_{digest(owner)}_*.json"):
            cached.unlink(missing_ok=True)
        self.store.patch(
            "sessions",
            {"session_id": owner},
            {
                "$pull": {
                    "attachments": {"id": document.get("attachment", {}).get("id")},
                    'uploads': doc_id,
                }
            },
        )
        self.store.patch(
            "sessions",
            {"session_id": owner},
            {
                "$inc": {
                    "counters.uploads_count": -1,
                    "counters.embed_chunks_used": -document.get("counts", {}).get(
                        "chunks", 0
                    ),
                }
            },
        )
        return True

    def cleanup(self):
        now = utcnow()
        from datetime import timedelta
        counts = {}
        idle = now - timedelta(minutes=get_settings().session['idle_minutes'])
        for session in self.store.find('sessions', {'last_active_at': {'$lte': idle}}):
            for name, removed in self.expire_session(session['session_id'], log_result=False).items():
                counts[name] = counts.get(name, 0) + removed
        expired = self.store.find(
            "documents", {"namespace": "user", "expires_at": {"$lte": now}}
        )
        for document in expired:
            self._delete_document(document, counts)
        # TTL may delete document records first; dependent collections expire independently.
        for name in ('chunks', 'sections', 'rate_rows', 'rate_tables', 'extractions'):
            counts[name] = counts.get(name, 0) + self.store.delete(name, {'expires_at': {'$lte': now}})
        from datetime import timedelta

        cutoff = (
            now - timedelta(hours=get_settings().uploads["expiry_hours"])
        ).timestamp()
        root = ROOT / "data/uploads"
        if root.exists():
            for path in root.glob("*/*"):
                if (
                    path.is_file()
                    and path.stat().st_mtime <= cutoff
                    and not self.store.one("documents", {"storage_path": str(path)})
                ):
                    path.unlink()
                    counts['upload_files'] = counts.get('upload_files', 0) + 1
        # Checkpoints contain raw uploads too, including when MongoDB TTL ran first.
        for checkpoint in (ROOT / "data/ingest").glob("*.json"):
            saved = json.loads(checkpoint.read_text(encoding="utf-8"))
            if (
                saved.get("expires_at")
                and datetime.fromisoformat(saved["expires_at"]) <= now
            ):
                checkpoint.unlink(missing_ok=True)
                counts['checkpoints'] = counts.get('checkpoints', 0) + 1
        for cached in (ROOT / "data/test_cache").glob("vision_*.json"):
            if (
                not cached.name.startswith(f"vision_{digest('bank')}_")
                and cached.stat().st_mtime <= cutoff
            ):
                cached.unlink(missing_ok=True)
                counts['cache_files'] = counts.get('cache_files', 0) + 1
        if any(counts.values()):
            log.step('CLEANUP', 'expired session content deleted', why='idle deadline or upload retention reached', **{name: count for name, count in counts.items() if count})
        return len(expired)

    def expire_session(self, owner, *, log_result=True):
        """Delete owner-scoped content, retaining only audit and process accounting."""
        if not owner or owner == 'bank':
            raise ValueError('A customer session is required')
        counts = {}
        documents = self.store.find('documents', {'owner_key': owner})
        for document in documents:
            (ROOT / 'data/ingest' / f"{document['doc_id']}.json").unlink(missing_ok=True)
        for name in ('documents', 'sections', 'chunks', 'rate_tables', 'rate_rows', 'extractions'):
            counts[name] = self.store.delete(name, {'owner_key': owner})
        for name in ('pending_actions', 'sessions'):
            counts[name] = self.store.delete(name, {'session_id': owner})
        for directory in ('uploads', 'crops'):
            root = (ROOT / 'data' / directory).resolve()
            folder = (root / digest(owner)).resolve()
            if folder.is_relative_to(root) and folder != root and folder.exists():
                counts[directory + '_files'] = sum(path.is_file() for path in folder.rglob('*'))
                shutil.rmtree(folder)
        for prefix in ('vision', 'agent'):
            for path in (ROOT / 'data/test_cache').glob(f'{prefix}_{digest(owner)}_*.json'):
                path.unlink(missing_ok=True)
                counts['cache_files'] = counts.get('cache_files', 0) + 1
        if log_result and any(counts.values()):
            log.step('CLEANUP', 'idle session deleted', session=owner[:8], why='no user action within session.idle_minutes', **{key: value for key, value in counts.items() if value})
        return counts


class UploadWorker:
    def __init__(self, service=None):
        self.service = service or UploadService()
        self.stop_event = Event()
        self.thread = Thread(target=self._run, name="upload-ingest", daemon=True)

    def start(self):
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        self.thread.join(get_settings().knowledge["worker"]["shutdown_seconds"])

    def _run(self):
        cleanup_at = 0
        cfg = get_settings().knowledge["worker"]
        while not self.stop_event.is_set():
            try:
                if monotonic() >= cleanup_at:
                    self.service.cleanup()
                    cleanup_at = monotonic() + cfg["cleanup_seconds"]
                for document in self.service.store.find(
                    "documents",
                    {
                        "namespace": "user",
                        "$or": [
                            {"processing_state": "queued"},
                            {"processing_state": "needs_retry", "retry_after": {"$lte": utcnow()}},
                            {
                                "embedding_state": "pending",
                                "attachment.status": "Processing",
                            },
                        ],
                    },
                ):
                    if self.stop_event.is_set():
                        break
                    with token_scope(document['owner_key'], self.service.store), log.request('upload', document.get('request_id')):
                        log.step('INGEST', 'job picked up', doc=document['doc_id'], stage=document['processing_state'])
                        self.service.process(document['owner_key'], document['attachment']['id'])
            except Exception as exc:
                log.step("INGEST", "worker error", error=type(exc).__name__)
            self.stop_event.wait(cfg["poll_seconds"])
