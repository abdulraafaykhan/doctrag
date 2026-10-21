"""Upload and list files belonging to the verified user's chat."""
from pathlib import PurePath
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile

from app.auth.security import CurrentUser, get_current_user
from app.config import get_settings
from app.db import documents_col
from app.schemas import AttachmentResponse, UploadResponse
from app.services.document_store import ingest_scoped, register_document, user_scope
from app.services.loaders import SUPPORTED_EXTENSIONS
from app.services.memory import require_session, _iso

router = APIRouter(prefix="/sessions/{session_id}/attachments", tags=["attachments"],
                   dependencies=[Depends(get_current_user)])


@router.post("", status_code=202, response_model=list[UploadResponse], response_model_exclude_none=True)
async def upload_attachments(session_id: str, background_tasks: BackgroundTasks,
    user: Annotated[CurrentUser, Depends(get_current_user)], files: list[UploadFile] = File(...)) -> list[dict]:
    await require_session(session_id, user.user_id)
    if not 1 <= len(files) <= 3:
        raise HTTPException(400, "Upload between 1 and 3 files at a time.")
    settings = get_settings()
    max_bytes = settings.user_upload_max_mb * 1024 * 1024
    results = []
    for file in files:
        name = PurePath(file.filename or "unnamed").name
        try:
            if PurePath(name).suffix.lower() not in SUPPORTED_EXTENSIONS:
                results.append({"filename": name, "status": "rejected", "error": "Unsupported file type."})
                continue
            data = await file.read(max_bytes + 1)
            if not data or len(data) > max_bytes:
                results.append({"filename": name, "status": "rejected", "error": "File is empty or exceeds the upload size limit."})
                continue
            try:
                row, duplicate = await register_document(name, file.content_type or "", data,
                    scope="user", owner_id=user.user_id, session_id=session_id)
            except HTTPException as exc:
                if exc.status_code != 400:
                    raise
                results.append({"filename": name, "status": "rejected", "error": exc.detail})
                continue
            doc_id = str(row["_id"])
            if not duplicate:
                background_tasks.add_task(ingest_scoped, doc_id, name, data,
                    scope="user", owner_id=user.user_id, session_id=session_id)
            results.append({"filename": name, "doc_id": doc_id, "status": row["status"], "duplicate": duplicate})
        finally:
            await file.close()
    return results


@router.get("", response_model=list[AttachmentResponse])
async def list_attachments(session_id: str, user: Annotated[CurrentUser, Depends(get_current_user)]) -> list[dict]:
    await require_session(session_id, user.user_id)
    rows = await documents_col().find(user_scope(user.user_id, session_id)).sort("created_at", -1).to_list()
    return [{"id": str(row["_id"]), "filename": row["filename"], "status": row["status"],
        "size_bytes": row["size_bytes"], "num_chunks": row["num_chunks"], "warnings": row.get("warnings", []),
        "created_at": _iso(row["created_at"]), "expires_at": _iso(row["expires_at"])} for row in rows]
