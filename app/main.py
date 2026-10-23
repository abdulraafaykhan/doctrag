"""FastAPI entry point.

Run with:  uv run uvicorn app.main:app --reload --no-proxy-headers
Then open: http://127.0.0.1:8000/docs    (Swagger: upload files and chat)

"""

import asyncio
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.bank.db import close_bank
from app.config import get_settings
from app.db import close_mongo, connect_to_mongo, ensure_indexes, vector_index_status
from app.logging_setup import RequestLoggingMiddleware, configure_logging
from app.routers import attachments, chat
from app.schemas import HealthResponse
from app.services.maintenance import purge_expired, retention_loop


@asynccontextmanager
async def lifespan(app: FastAPI):
    cleanup_task = None
    try:
        await connect_to_mongo()
        await ensure_indexes()
        await purge_expired()
        cleanup_task = asyncio.create_task(retention_loop())
        yield
    finally:
        if cleanup_task is not None:
            cleanup_task.cancel()
            with suppress(asyncio.CancelledError):
                await cleanup_task
        await close_bank()
        await close_mongo()


configure_logging()
app = FastAPI(
    title="Harbourline Bank Assistant",
    description="Anonymous bank chat with in-chat verification.",
    lifespan=lifespan,
)

# Only explicitly configured browser origins may access the API.
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().allowed_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(RequestLoggingMiddleware)


@app.exception_handler(RequestValidationError)
async def validation_error(request, error):
    # Default validation bodies echo input, which could contain verification credentials.
    return JSONResponse(
        status_code=422,
        content={
            "detail": "The request could not be accepted. Check its fields and try again."
        },
    )


app.include_router(attachments.router)
app.include_router(chat.router)

# Keep the API root and Swagger routes intact; Vite uses the same /app/ base.
frontend_dist = Path(__file__).resolve().parents[1] / "frontend" / "dist"
if frontend_dist.is_dir():
    app.mount("/app", StaticFiles(directory=frontend_dist, html=True), name="frontend")


# --- Swagger file-upload fix -------------------------------------------------------------
# Recent FastAPI/Pydantic versions describe file fields as `contentMediaType:
# application/octet-stream`, which Swagger UI doesn't recognise, so it shows a text box.
# Swagger draws a real file picker for `format: binary`, so we rewrite the schema.
def _fix_binary(node) -> None:
    if isinstance(node, dict):
        if (
            node.get("type") == "string"
            and node.get("contentMediaType") == "application/octet-stream"
        ):
            node.pop("contentMediaType")
            node["format"] = "binary"
        for value in node.values():
            _fix_binary(value)
    elif isinstance(node, list):
        for value in node:
            _fix_binary(value)


_default_openapi = app.openapi


def _custom_openapi():
    schema = _default_openapi()
    _fix_binary(schema)
    return schema


app.openapi = _custom_openapi
# ------------------------------------------------------------------------------------------


@app.get("/", include_in_schema=False)
async def index():
    """Open the API test harness."""
    return RedirectResponse("/docs")


@app.get("/health", tags=["meta"], response_model=HealthResponse)
async def health():
    return {"status": "ok", "vector_index": await vector_index_status()}
