"""FastAPI entry point.

Run with:  uv run uvicorn app.main:app --reload
Then open: http://127.0.0.1:8000/docs    (Swagger: upload files and chat)

"""
from contextlib import asynccontextmanager


from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from app.db import close_mongo, connect_to_mongo, ensure_indexes, vector_index_status
from app.routers import chat, documents
from app.auth.router import router as auth_router
from app.config import get_settings
from app.schemas import HealthResponse
from app.services.maintenance import sweep_stuck_documents



@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        await connect_to_mongo()
        await ensure_indexes()
        await sweep_stuck_documents()
        yield
    finally:
        await close_mongo()


app = FastAPI(title="doctrag", description="RAG over your documents, with multi-turn chat.", lifespan=lifespan)

# Only explicitly configured browser origins may access the API.
app.add_middleware(CORSMiddleware, allow_origins=get_settings().allowed_origins, allow_methods=["*"], allow_headers=["*"])

app.include_router(auth_router)
app.include_router(documents.router)
app.include_router(chat.router)


# --- Swagger file-upload fix -------------------------------------------------------------
# Recent FastAPI/Pydantic versions describe file fields as `contentMediaType:
# application/octet-stream`, which Swagger UI doesn't recognise, so it shows a text box.
# Swagger draws a real file picker for `format: binary`, so we rewrite the schema.
def _fix_binary(node) -> None:
    if isinstance(node, dict):
        if node.get("type") == "string" and node.get("contentMediaType") == "application/octet-stream":
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
