"""Foundation API with explicit startup dependency checks."""

from contextlib import asynccontextmanager
from time import perf_counter

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from app.config import get_settings
from app.db import close_db, index_status, ping
from app.llm import QuotaGuard
from app.log import log


def health_checks() -> dict:
    settings = get_settings()
    ping()
    indexes = index_status()
    return {
        "status": "ok" if indexes["ready"] else "degraded",
        "db": "ok",
        "indexes": indexes,
        "quota_remaining": QuotaGuard().remaining(),
        "keys_present": True,
        "models_configured": settings.models.model_dump(),
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        try:
            state = await run_in_threadpool(health_checks)
        except Exception as exc:
            log.step(
                "API", "startup checks failed", error=type(exc).__name__, level="ERROR"
            )
            raise RuntimeError(
                f"Startup dependency check failed ({type(exc).__name__})"
            ) from None
        log.step("API", "startup checks", **state)
        if state["status"] != "ok":
            raise RuntimeError(
                "Required indexes are not READY; run scripts/create_indexes.py"
            )
        from app.uploads import UploadWorker

        worker = UploadWorker()
        worker.start()
        try:
            yield
        finally:
            worker.stop()
    finally:
        close_db()


app = FastAPI(
    title=get_settings().bank["name"],
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


@app.middleware("http")
async def request_logging(request, call_next):
    started = perf_counter()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        return response
    finally:
        log.step(
            "API",
            "request",
            route=request.url.path,
            status=status,
            ms=round((perf_counter() - started) * 1000),
        )


@app.get("/api/health")
def health():
    try:
        state = health_checks()
        return JSONResponse(state, status_code=200 if state["status"] == "ok" else 503)
    except Exception as exc:
        log.step("API", "health unavailable", error=type(exc).__name__, level="ERROR")
        return JSONResponse(
            {
                "error": "dependency_unavailable",
                "message": get_settings().messages["answers"]["retry_later"],
            },
            status_code=503,
        )
