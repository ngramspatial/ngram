"""FastAPI surface for the model-free local ngram Studio."""

from __future__ import annotations

import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask
from starlette.middleware.trustedhost import TrustedHostMiddleware

from ngram.studio.service import StudioError, StudioSession


def create_studio_app(container_path: Path) -> FastAPI:
    session = StudioSession(container_path)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        session.start()
        try:
            yield
        finally:
            session.close()

    app = FastAPI(
        title="ngram Studio",
        version="1.0.0",
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.studio = session
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=["127.0.0.1", "localhost", "testserver"],
    )

    @app.middleware("http")
    async def secure_local_surface(request: Request, call_next):
        if request.method in {"POST", "PUT", "PATCH", "DELETE"} and (
            request.headers.get("x-ngram-studio") != "1"
        ):
            return JSONResponse(
                status_code=403,
                content={"detail": "Studio rejected a non-local mutation request"},
            )
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; base-uri 'none'; object-src 'none'; "
            "frame-ancestors 'none'; form-action 'none'; "
            "script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; connect-src 'self'"
        )
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        return response

    @app.exception_handler(StudioError)
    async def studio_error(_request, exc: StudioError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.get("/api/overview")
    async def overview() -> dict[str, Any]:
        return session.overview()

    @app.get("/api/verify")
    async def verify() -> dict[str, Any]:
        return session.overview()["verification"]

    @app.get("/api/lineage")
    async def lineage() -> dict[str, Any]:
        return {"entries": session.lineage()}

    @app.get("/api/domains/{domain}")
    async def domain(domain: str) -> dict[str, Any]:
        return {"domain": domain, "files": session.list_domain(domain)}

    @app.get("/api/file")
    async def inspect_file(path: str = Query(..., min_length=1)) -> dict[str, Any]:
        return session.inspect_file(path)

    @app.get("/api/settings")
    async def settings() -> dict[str, Any]:
        return {"settings": session.settings(), "writable": session.overview()["writable"]}

    @app.put("/api/settings/{section}")
    async def update_setting(
        section: str,
        value: dict[str, Any],
    ) -> dict[str, Any]:
        return session.update_setting(section, value)

    @app.get("/api/soma/simulate")
    async def simulate_soma(
        hours: float = Query(24.0, ge=0.25, le=168.0),
        steps: int = Query(24, ge=2, le=96),
    ) -> dict[str, Any]:
        return session.simulate_soma(hours, steps)

    @app.post("/api/recover")
    async def recover() -> dict[str, Any]:
        return session.recover()

    @app.get("/api/export")
    async def export() -> FileResponse:
        archive, directory, filename = session.export_snapshot()
        return FileResponse(
            archive,
            filename=filename,
            media_type="application/x-tar",
            background=BackgroundTask(shutil.rmtree, directory, True),
        )

    static = Path(__file__).with_name("static")
    app.mount("/", StaticFiles(directory=static, html=True), name="studio")
    return app
