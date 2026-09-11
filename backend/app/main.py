"""FastAPI application entry point."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import router
from app.core.config import settings
from app.core.errors import AppError
from app.core.logging_config import configure_logging
from app.storage.jobs import jobs
from app.storage.store import store

logger = logging.getLogger(__name__)

CLEANUP_INTERVAL_SECONDS = 300


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    logger.info(
        "starting",
        extra={"operation": "startup", "version": settings.version, "storage": str(settings.storage_dir)},
    )
    task = asyncio.create_task(_janitor())
    try:
        yield
    finally:
        task.cancel()
        jobs.shutdown()
        store.clear()
        logger.info("stopped", extra={"operation": "shutdown"})


async def _janitor() -> None:
    """Delete expired uploads. Temporary files never outlive their model."""
    while True:
        try:
            await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)
            store.cleanup()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("cleanup failed", extra={"operation": "cleanup"})


app = FastAPI(
    title=settings.app_name,
    version=settings.version,
    description=(
        "Engineering-oriented FDM print optimisation: geometry analysis, "
        "anisotropy-aware orientation search and print settings recommendation. "
        "Not a certified structural analysis system."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def request_logging(request: Request, call_next):
    request_id = uuid.uuid4().hex[:12]
    started = time.perf_counter()
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    logger.info(
        "request",
        extra={
            "operation": "http",
            "request_id": request_id,
            "method": request.method,
            "path": request.url.path,
            "status": response.status_code,
            "duration_ms": round((time.perf_counter() - started) * 1000, 2),
        },
    )
    return response


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    logger.info(
        "handled error",
        extra={"operation": "http", "path": request.url.path, "code": exc.code},
    )
    return JSONResponse(status_code=exc.status_code, content=exc.to_payload())


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Turn Pydantic validation errors into a readable message.

    The raw pydantic error list is kept in ``details`` for the UI, but the
    ``message`` is a sentence a user can act on.
    """
    problems = []
    for error in exc.errors():
        location = " -> ".join(str(part) for part in error.get("loc", ()) if part != "body")
        problems.append(f"{location or 'request'}: {error.get('msg', 'invalid value')}")
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "invalid_request",
                "message": "The request could not be processed: " + "; ".join(problems[:5]),
                "details": {"problems": problems},
            }
        },
    )


@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception(
        "unhandled error", extra={"operation": "http", "path": request.url.path}
    )
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "code": "internal_error",
                "message": "An unexpected error occurred. The details were logged on the server.",
                "details": {},
            }
        },
    )


@app.get("/health", tags=["system"])
def health() -> dict:
    return {
        "status": "ok",
        "version": settings.version,
        "models_in_memory": len(store.list()),
    }


app.include_router(router)
