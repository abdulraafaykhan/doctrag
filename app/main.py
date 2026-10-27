"""Application factory, startup checks and production UI serving."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from app.config import ROOT, get_settings
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


async def request_logging(request, call_next):
    if request.method == 'POST' and request.url.path in {'/api/chat', '/api/upload'}:
        # A mutable context value carries timing from threadpool endpoints to middleware.
        with log.request('chat' if request.url.path == '/api/chat' else 'upload'):
            return await call_next(request)
    response = await call_next(request)
    log.step('API', 'heartbeat' if request.url.path == '/api/session' and request.method == 'GET' else 'request',
             route=request.url.path, status=response.status_code, level='DEBUG')
    return response


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


def create_app(*, chat=None, uploads=None, checks=True):
    """Dependency injection keeps API contract tests entirely off the network."""
    from app.api import API, APIError, error_response
    from app.chat import Chat
    from app.uploads import UploadService

    instance = FastAPI(title=get_settings().bank["name"], lifespan=lifespan if checks else None, docs_url=None, redoc_url=None, openapi_url=None)
    service = chat or Chat()
    instance.state.api = API(service, uploads or UploadService(service.store))
    instance.middleware("http")(request_logging)

    @instance.exception_handler(APIError)
    async def api_error(request, exc):
        return error_response(exc)

    @instance.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        return error_response(APIError("invalid_request", "bad_request", 422))

    @instance.exception_handler(HTTPException)
    async def http_error(request, exc):
        return error_response(APIError("not_found" if exc.status_code == 404 else "invalid_request", "not_found" if exc.status_code == 404 else "bad_request", exc.status_code))

    @instance.exception_handler(Exception)
    async def unexpected_error(request, exc):
        log.step("API", "request failed", error=type(exc).__name__)
        key = "busy" if str(exc) == "SESSION_BUSY" else "unavailable"
        return error_response(APIError("request_failed", key, 409 if key == "busy" else 503))

    instance.include_router(instance.state.api.router)
    instance.add_api_route("/api/health", health, methods=["GET"])
    @instance.get("/api/{path:path}")
    def missing_api(path: str):
        return error_response(APIError("not_found", "not_found", 404))

    dist = ROOT / "frontend/dist"
    if dist.exists():
        instance.mount("/", StaticFiles(directory=dist, html=True), name="frontend")
    return instance


app = create_app()
