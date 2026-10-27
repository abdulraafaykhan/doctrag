"""Resumable ingestion shared by bank scripts and upload processing."""

import base64
import json
from datetime import timedelta
from pathlib import Path

from app.config import ROOT, get_settings
from app.ingest.chunking import chunk_elements, rate_records
from app.ingest.embeddings import Embeddings
from app.ingest.images import ImageCascade
from app.ingest.models import DocumentMeta, Element, digest, utcnow
from app.ingest.parsers import parse_file
from app.ingest.store import Store
from app.ingest.validation import validate_file
from app.llm import QuotaExceeded
from app.log import log


class Pipeline:
    def __init__(self, store=None, *, images=None, embeddings=None):
        self.store = store or Store()
        self.images = images or ImageCascade(self.store)
        self.embeddings = embeddings or Embeddings(self.store)

    def ingest(
        self,
        path: Path,
        meta: DocumentMeta,
        *,
        owner_key="bank",
        defer_embeddings=False,
    ):
        cfg = get_settings()
        customer = owner_key != "bank"
        ext = validate_file(path, customer=customer)
        file_hash = digest(path.read_bytes())
        identity = {"file_hash": file_hash, "owner_key": owner_key}
        document = self.store.one("documents", identity)
        if document and document["processing_state"] in {"ready", "partial"}:
            if not defer_embeddings and document.get("embedding_state") != "ready":
                try:
                    self.embed_document(document["doc_id"])
                except QuotaExceeded:
                    return {**document, "paused_quota": True}
            log.step("INGEST", "unchanged document skipped", doc=document["doc_id"])
            return {**document, "skipped": True}
        if document is None:
            doc_id = "doc_" + digest(f"{owner_key}|{file_hash}")[:24]
            document = {
                **meta.model_dump(),
                **identity,
                "doc_id": doc_id,
                "namespace": "user" if customer else "bank",
                "filename": path.name,
                "mime": ext,
                "size_bytes": path.stat().st_size,
                "storage_path": str(path.resolve()),
                "processing_state": "queued",
                "created_at": utcnow(),
                "counts": {},
                "review_flags": [],
            }
            if customer:
                document["expires_at"] = utcnow() + timedelta(
                    hours=cfg.uploads["expiry_hours"]
                )
            self.store.put("documents", identity, document)
        job_id = "job_" + document["doc_id"]
        job = self.store.one("ingest_jobs", {"job_id": job_id}) or {
            "job_id": job_id,
            "doc_id": document["doc_id"],
            "state": "queued",
            "stage": "parse",
            "cursor": 0,
            "attempts": 0,
        }
        job.update(state="running", attempts=job["attempts"] + 1, updated_at=utcnow())
        self.store.put("ingest_jobs", {"job_id": job_id}, job)
        checkpoint = ROOT / "data/ingest" / f"{document['doc_id']}.json"
        checkpoint.parent.mkdir(parents=True, exist_ok=True)
        try:
            if job["stage"] == "parse":
                if checkpoint.exists():
                    saved = json.loads(checkpoint.read_text(encoding="utf-8"))
                    elements = [
                        Element.model_validate(value) for value in saved["elements"]
                    ]
                    total, processed = saved["total"], saved["processed"]
                else:
                    elements, total, processed = parse_file(path, customer=customer)
                    checkpoint.write_text(
                        json.dumps(
                            {
                                "elements": [value.model_dump() for value in elements],
                                "total": total,
                                "processed": processed,
                                "expires_at": document.get("expires_at").isoformat()
                                if document.get("expires_at")
                                else None,
                            }
                        ),
                        encoding="utf-8",
                    )
                resolved, vlm_pages, ocr_pages = [], 0, 0

                def budget():
                    nonlocal vlm_pages
                    if customer:
                        if vlm_pages >= cfg.uploads["vlm_pages_per_file"]:
                            return False
                        if not self.store.reserve(
                            owner_key,
                            "vlm_pages_used",
                            1,
                            cfg.uploads["vlm_pages_per_session"],
                        ):
                            return False
                    vlm_pages += 1
                    return True

                flags = []
                for element in elements:
                    if element.type != "image":
                        resolved.append(element)
                        continue
                    result = self.images.extract(
                        base64.b64decode(element.content["base64"]),
                        owner_key,
                        mode="online" if customer else "ingest",
                        budget=budget,
                    )
                    if result.get("needs_retry"):
                        # Keep the parse checkpoint so a resume only retries failed crops.
                        document.update(processing_state="needs_retry", retry_after=utcnow() + timedelta(seconds=cfg.knowledge["images"]["retry_cooldown_seconds"]))
                        self.store.put("documents", identity, document)
                        job.update(state="needs_retry", stage="parse", updated_at=utcnow())
                        self.store.put("ingest_jobs", {"job_id": job_id}, job)
                        return document
                    ocr_pages += result["method"] != "decorative"
                    flags.extend(
                        {"page": element.page, "reason": flag}
                        for flag in result["flags"]
                    )
                    structured = result.get("structured")
                    common = {
                        "page": element.page,
                        "section": element.section,
                        "bbox": element.bbox,
                        "source_method": result["method"],
                        "confidence": result["confidence"],
                        "needs_review": result["needs_review"],
                        "flags": result["flags"],
                    }
                    if structured and isinstance(structured.get("rows"), list):
                        resolved.append(
                            Element(type="table", content=structured, **common)
                        )
                        if structured.get("notes"):
                            resolved.append(
                                Element(
                                    type="text",
                                    content="\n".join(structured["notes"]),
                                    **common,
                                )
                            )
                    elif result["text"]:
                        resolved.append(
                            Element(type="text", content=result["text"], **common)
                        )
                document.update(
                    pages_total=total,
                    pages_processed=processed,
                    review_flags=flags,
                    warnings=[],
                    counts={"ocr_pages": ocr_pages, "vlm_pages": vlm_pages},
                )
                if processed < total:
                    document["warnings"].append(
                        cfg.knowledge["messages"]["partial"].format(
                            done=processed, total=total
                        )
                    )
                if flags:
                    message = (
                        "unreadable"
                        if any(flag["reason"] == "unreadable_image" for flag in flags)
                        else "confirm_numbers"
                    )
                    document["warnings"].append(cfg.knowledge["messages"][message])
                log.step('INGEST', 'stage', doc=document['doc_id'], stage='chunk')
                with log.span('CHUNK', 'build chunks'):
                    sections, chunks = chunk_elements(resolved, document)
                if customer:
                    session = (
                        self.store.one("sessions", {"session_id": owner_key}) or {}
                    )
                    used = session.get("counters", {}).get("embed_chunks_used", 0)
                    available = min(
                        cfg.uploads["chunks_per_file"],
                        cfg.uploads["chunks_per_session"] - used,
                        cfg.uploads["user_chunks_total"]
                        - self.store.count("chunks", {"namespace": "user"}),
                    )
                    if len(chunks) > available:
                        chunks = chunks[: max(0, available)]
                        document["warnings"].append(
                            cfg.knowledge["messages"]["chunks_partial"]
                        )
                    # Reserve only new chunks; resume overwrites deterministic identities.
                    new_chunks = len(chunks) - self.store.count(
                        "chunks", {"doc_id": document["doc_id"]}
                    )
                    if new_chunks > 0 and not self.store.reserve(
                        owner_key,
                        "embed_chunks_used",
                        new_chunks,
                        cfg.uploads["chunks_per_session"],
                    ):
                        raise QuotaExceeded("Session chunk allowance exhausted")
                for section in sections:
                    self.store.put(
                        "sections", {"section_id": section["section_id"]}, section
                    )
                for chunk in chunks:
                    self.store.put(
                        "chunks",
                        {"chunk_id": chunk["chunk_id"]},
                        {**chunk, "created_at": utcnow()},
                    )
                tables_count = 0
                for element in resolved:
                    tables, rows = rate_records(element, document)
                    for name, values, key in (
                        ("rate_tables", tables, "table_id"),
                        ("rate_rows", rows, "row_id"),
                    ):
                        for value in values:
                            self.store.put(name, {key: value[key]}, value)
                    tables_count += len(tables)
                document["counts"].update(chunks=len(chunks), tables=tables_count)
                document["embedding_state"] = "pending"
                document["processing_state"] = (
                    "partial" if document["warnings"] or flags else "ready"
                )
                self.store.put("documents", identity, document)
                job.update(
                    stage="embed", cursor=0, chunks_total=len(chunks), chunks_done=0
                )
                self.store.put("ingest_jobs", {"job_id": job_id}, job)
            if defer_embeddings:
                job.update(state="queued", updated_at=utcnow())
                self.store.put("ingest_jobs", {"job_id": job_id}, job)
                return document
            self.embed_document(document["doc_id"])
            return self.store.one("documents", {"doc_id": document["doc_id"]})
        except QuotaExceeded:
            job.update(state="paused_quota", updated_at=utcnow())
            self.store.put("ingest_jobs", {"job_id": job_id}, job)
            log.step(
                "INGEST", "paused quota", doc=document["doc_id"], stage=job["stage"]
            )
            return {**document, "processing_state": "queued", "paused_quota": True}
        except Exception as exc:
            job.update(
                state="failed", last_error=type(exc).__name__, updated_at=utcnow()
            )
            self.store.put("ingest_jobs", {"job_id": job_id}, job)
            raise

    def embed_document(self, doc_id: str):
        document = self.store.one("documents", {"doc_id": doc_id})
        if not document:
            raise ValueError("Unknown document")
        if document.get("processing_state") == "needs_retry":
            log.step("INGEST", "image retry required before completing job", doc=doc_id)
            return
        job_id = "job_" + doc_id
        job = self.store.one("ingest_jobs", {"job_id": job_id})
        missing = self.store.find(
            "chunks", {"doc_id": doc_id, "embedding": {"$exists": False}}
        )
        size = get_settings().knowledge["embedding"]["batch_size"]
        try:
            for start in range(0, len(missing), size):
                batch = missing[start : start + size]
                with log.span('EMBED', 'embed chunks', count=len(batch)):
                    vectors = self.embeddings.embed(
                        [row["text"] for row in batch], upload=document["namespace"] == "user")
                for row, vector in zip(batch, vectors, strict=True):
                    self.store.patch(
                        "chunks",
                        {"chunk_id": row["chunk_id"]},
                        {"$set": {"embedding": vector}},
                    )
                if job:
                    job.update(
                        stage="embed",
                        cursor=job.get("cursor", 0) + len(batch),
                        chunks_done=job.get("chunks_done", 0) + len(batch),
                        updated_at=utcnow(),
                    )
                    self.store.put("ingest_jobs", {"job_id": job_id}, job)
                log.step(
                    "INGEST",
                    "embedding progress",
                    doc=doc_id,
                    chunks_done=job.get("chunks_done", 0) if job else len(batch),
                    chunks_total=document["counts"]["chunks"],
                )
        except QuotaExceeded:
            if job:
                job.update(state="paused_quota", updated_at=utcnow())
                self.store.put("ingest_jobs", {"job_id": job_id}, job)
            raise
        self.store.patch(
            "documents", {"doc_id": doc_id}, {"$set": {"embedding_state": "ready"}}
        )
        if job:
            job.update(state="done", stage="store", updated_at=utcnow())
            self.store.put("ingest_jobs", {"job_id": job_id}, job)
