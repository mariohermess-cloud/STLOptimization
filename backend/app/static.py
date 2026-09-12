"""Serving the built web UI from the API process.

In the container deployment nginx serves the frontend bundle and proxies
``/api`` to this application. The portable build has no nginx: one process
serves both, which is also why the frontend's same-origin ``/api`` calls keep
working without a single change.

The mount is conditional on ``PEO_STATIC_DIR`` pointing at a directory that
contains ``index.html``, so the container path is unaffected.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

logger = logging.getLogger(__name__)

#: Paths owned by the API; never answered with the SPA shell.
API_PREFIXES = ("/api", "/health", "/docs", "/redoc", "/openapi.json")


def static_directory() -> Path | None:
    """The frontend bundle to serve, or ``None`` when there is none."""
    configured = os.environ.get("PEO_STATIC_DIR")
    if not configured:
        return None
    directory = Path(configured).expanduser()
    if not (directory / "index.html").is_file():
        logger.warning(
            "PEO_STATIC_DIR does not contain index.html; not serving the web UI",
            extra={"operation": "startup", "path": str(directory)},
        )
        return None
    return directory


def mount_web_ui(app: FastAPI, directory: Path) -> None:
    """Serve ``directory`` as the web UI, with an SPA fallback.

    Hashed asset files get a long cache lifetime; ``index.html`` must not be
    cached, otherwise a rebuilt bundle keeps loading the previous asset names.
    """
    assets = directory / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    index = directory / "index.html"

    @app.get("/", include_in_schema=False)
    def serve_index() -> FileResponse:
        return FileResponse(index, headers={"Cache-Control": "no-store"})

    @app.get("/{path:path}", include_in_schema=False)
    def serve_spa(request: Request, path: str) -> FileResponse:
        if any(request.url.path.startswith(prefix) for prefix in API_PREFIXES):
            raise HTTPException(status_code=404, detail="Not found")

        # A real file (favicon, manifest, source map) is served as itself;
        # anything else is a client-side route and gets the SPA shell.
        candidate = (directory / path).resolve()
        try:
            candidate.relative_to(directory.resolve())
        except ValueError:
            # Escaped the bundle directory - refuse rather than serve it.
            raise HTTPException(status_code=404, detail="Not found") from None
        if candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(index, headers={"Cache-Control": "no-store"})

    logger.info(
        "serving the web UI", extra={"operation": "startup", "path": str(directory)}
    )
