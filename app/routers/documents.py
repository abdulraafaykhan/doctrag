"""Upload, list and delete documents."""
from pathlib import PurePath

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile

from app.auth.security import get_current_user
from app.config import get_settings
from app.schemas import DocumentResponse, UploadResponse, DocumentDeleted
from app.db import chunks_col, documents_col
from app.services.ingestion import ingest_document, register_document
from app.services.loaders import SUPPORTED_EXTENSIONS

router = APIRouter(prefix="/documents", tags=["documents"], dependencies=[Depends(get_current_user)])


def _serialize(d: dict) -> dict:
    d["id"] = str(d.pop("_id"))
    d.pop("file_hash", None)
    if d.get("created_at"):
        d["created_at"] = d["created_at"].isoformat()
    return d


def _oid(doc_id: str) -> ObjectId:
    try:
        return ObjectId(doc_id)
    except (InvalidId, TypeError):
        raise HTTPException(status_code=400, detail="Invalid document id")


@router.post("", status_code=202, response_model=list[UploadResponse], response_model_exclude_none=True)
async def upload_documents(background_tasks: BackgroundTasks, files: list[UploadFile] = File(...)):
    """Upload one or more files. Processing happens in the background: poll GET /documents."""
    max_bytes = get_settings().max_upload_mb * 1024 * 1024
    results = []
    for f in files:
        name = f.filename or "unnamed"
        ext = PurePath(name).suffix.lower()
        if ext not in SUPPORTED_EXTENSIONS:
            results.append({"filename": name, "status": "rejected", "error": f"Unsupported type '{ext}'"})
            continue
        data = await f.read()
        if not data:
            results.append({"filename": name, "status": "rejected", "error": "Empty file"})
            continue
        if len(data) > max_bytes:
            results.append({"filename": name, "status": "rejected", "error": "File too large"})
            continue

        doc_id, duplicate = await register_document(name, f.content_type or "", data)
        if not duplicate:
            background_tasks.add_task(ingest_document, doc_id, name, data)
        results.append(
            {
                "filename": name,
                "doc_id": doc_id,
                "status": "ready" if duplicate else "processing",
                "duplicate": duplicate,
            }
        )
    return results


@router.get("", response_model=list[DocumentResponse])
async def list_documents():
    cursor = documents_col().find({}).sort("created_at", -1)
    return [_serialize(d) for d in await cursor.to_list()]


@router.get("/{doc_id}", response_model=DocumentResponse)
async def get_document(doc_id: str):
    doc = await documents_col().find_one({"_id": _oid(doc_id)})
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    return _serialize(doc)


@router.delete("/{doc_id}", response_model=DocumentDeleted)
async def delete_document(doc_id: str):
    oid = _oid(doc_id)
    deleted = await documents_col().delete_one({"_id": oid})
    if deleted.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Document not found")
    removed = await chunks_col().delete_many({"doc_id": doc_id})
    return {"deleted": doc_id, "chunks_removed": removed.deleted_count}