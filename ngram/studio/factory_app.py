"""Loopback-only Studio API for the model-free Entity Factory."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from ngram.container.restore import runtime_entity_key
from ngram.entity_factory import (
    FACTORY_SCHEMA_VERSION,
    EntityFactoryError,
    build_deployment_plan,
    build_entity_manifest,
    create_entity_config,
    entity_status,
    validate_entity,
)


def create_entity_factory_app(declarations_dir: Path) -> FastAPI:
    """Create a local API that never initializes inference or provider clients."""
    root = declarations_dir.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    app = FastAPI(
        title="ngram Studio · Entity Factory",
        version="1.0.0",
        docs_url=None,
        redoc_url=None,
    )
    app.state.declarations_dir = root
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
        response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.exception_handler(EntityFactoryError)
    async def factory_error(_request: Request, exc: EntityFactoryError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    def declaration(key: str) -> Path:
        normalized = runtime_entity_key(key)
        if normalized != key:
            raise EntityFactoryError(f"invalid entity key: {key!r}")
        path = (root / f"{normalized}.yaml").resolve()
        if path.parent != root:
            raise EntityFactoryError("entity path escapes the declarations directory")
        if not path.is_file():
            raise EntityFactoryError(f"entity declaration not found: {normalized}")
        return path

    @app.get("/api/factory")
    async def overview() -> dict[str, Any]:
        rows = []
        for path in sorted(root.glob("*.yaml"), key=lambda item: item.name):
            rows.append(validate_entity(path).as_dict())
        return {
            "schema_version": FACTORY_SCHEMA_VERSION,
            "model_loaded": False,
            "provider_contacted": False,
            "declarations_dir": str(root),
            "entities": rows,
        }

    @app.post("/api/factory/entities")
    async def create(payload: dict[str, Any]) -> dict[str, Any]:
        allowed = {"name", "archetype", "model", "surface"}
        unknown = sorted(set(payload) - allowed)
        if unknown:
            raise EntityFactoryError("unknown creation fields: " + ", ".join(unknown))
        name = str(payload.get("name") or "").strip()
        if not name:
            raise EntityFactoryError("entity name is required")
        path = create_entity_config(
            name,
            root,
            archetype=str(payload.get("archetype") or "curious"),
            model=str(payload.get("model") or "") or None,
            surface=str(payload.get("surface") or "cli"),
        )
        return entity_status(path)

    @app.get("/api/factory/entities/{key}")
    async def status(key: str) -> dict[str, Any]:
        return entity_status(declaration(key))

    @app.get("/api/factory/entities/{key}/manifest")
    async def manifest(key: str) -> dict[str, Any]:
        return build_entity_manifest(declaration(key))

    @app.get("/api/factory/entities/{key}/plan")
    async def plan(key: str) -> dict[str, Any]:
        return build_deployment_plan(declaration(key))

    return app
