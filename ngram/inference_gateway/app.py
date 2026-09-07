"""Narrow FastAPI surface: inference only (no host tools, no shell).

Security posture (non-negotiable):
- This process is a **private inference appliance** on the home machine. It must not expose
  shell, filesystem, ngram tools, browser automation, admin consoles, or any host-control API.
- **Cloudflare Tunnel (or equivalent) must terminate only at this gateway** — forward to
  ``127.0.0.1:<gateway_port>`` — never at a generic reverse proxy that also exposes SSH, home
  dashboards, NAS UI, or other host services. The tunnel must not become a broad path into
  the user's computer; only this HTTP surface is in scope.

Exposed HTTP surface (all require ``Authorization: Bearer <INFERENCE_GATEWAY_TOKEN>`` except
that unauthenticated requests receive 401/403 before route logic):
- ``GET /health`` — liveness and downstream model check
- ``GET /v1/models`` — model list (proxied)
- ``POST /v1/chat/completions`` — chat (optional streaming)
- ``POST /v1/embeddings`` — embeddings

``HEAD`` on ``/health`` and ``/v1/models`` is allowed for simple probes.

OpenAPI/Swagger UI is **disabled** so the tunneled origin does not ship interactive admin docs.
Unknown methods/paths receive **404** with a small JSON body (see ``_reject_unknown_surface``).

The origin enforces independent sliding-window limits. Public tunnel launches additionally require
Cloudflare Access, and the gateway validates Access JWTs instead of trusting the edge configuration.
"""

from __future__ import annotations

import asyncio
import hmac
import ipaddress
import logging
import os
import time
from collections import deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import urlparse

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.responses import Response

from ngram.inference_gateway.backends.ollama import OllamaCompatibleBackend

log = logging.getLogger("ngram.inference_gateway")

# Authoritative allowlist — must stay in sync with route handlers below.
GATEWAY_ALLOWED_METHOD_PATH: frozenset[tuple[str, str]] = frozenset(
    {
        ("GET", "/health"),
        ("HEAD", "/health"),
        ("GET", "/v1/models"),
        ("HEAD", "/v1/models"),
        ("POST", "/v1/chat/completions"),
        ("POST", "/v1/embeddings"),
    }
)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = (os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(f"{name} must be true or false")


def _bounded_env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    try:
        value = int(raw) if raw else default
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc
    if value < minimum or value > maximum:
        raise RuntimeError(f"{name} must be between {minimum} and {maximum}")
    return value


def _bearer_matches(authorization: str | None, expected: str) -> bool:
    if not expected or not authorization or not authorization.startswith("Bearer "):
        return False
    presented = authorization[7:].strip()
    return bool(presented) and hmac.compare_digest(presented, expected)


def _cloudflare_edge_request(request: Request) -> bool:
    """Cloudflare always injects these at the edge; the origin is loopback-only."""
    return bool(request.headers.get("cf-ray") or request.headers.get("cf-connecting-ip"))


def _loopback_request(request: Request) -> bool:
    """Only a literal loopback peer may use the operator's local bypass."""
    if not request.client or not request.client.host:
        return False
    host = request.client.host.strip().split("%", 1)[0]
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host.lower() == "localhost"


def _caller_key(request: Request) -> str:
    if request.headers.get("cf-ray"):
        edge_ip = (request.headers.get("cf-connecting-ip") or "").strip()
        if edge_ip:
            return edge_ip[:128]
    if request.client and request.client.host:
        return request.client.host[:128]
    return "unknown"


class SlidingWindowRateLimiter:
    """Small bounded in-process limiter for the single-user inference appliance."""

    def __init__(
        self,
        *,
        window_seconds: int,
        authenticated_limit: int,
        unauthenticated_limit: int,
        global_limit: int,
    ) -> None:
        self.window_seconds = window_seconds
        self.authenticated_limit = authenticated_limit
        self.unauthenticated_limit = unauthenticated_limit
        self.global_limit = global_limit
        self._events: dict[str, deque[float]] = {}
        self._lock = asyncio.Lock()
        self._checks = 0

    async def admit(self, request: Request, *, authenticated: bool) -> tuple[bool, int, int]:
        caller = _caller_key(request)
        buckets: list[tuple[str, int]]
        if authenticated:
            buckets = [
                ("authenticated:global", self.global_limit),
                (f"authenticated:{caller}", self.authenticated_limit),
            ]
        else:
            buckets = [(f"unauthenticated:{caller}", self.unauthenticated_limit)]

        now = time.monotonic()
        cutoff = now - self.window_seconds
        async with self._lock:
            self._checks += 1
            queues: list[tuple[deque[float], int]] = []
            for key, limit in buckets:
                queue = self._events.setdefault(key, deque())
                while queue and queue[0] <= cutoff:
                    queue.popleft()
                queues.append((queue, limit))

            blocked = [(queue, limit) for queue, limit in queues if len(queue) >= limit]
            if blocked:
                retry_after = max(
                    1,
                    min(
                        self.window_seconds,
                        max(
                            int(self.window_seconds - (now - queue[0])) + 1 for queue, _ in blocked
                        ),
                    ),
                )
                return False, 0, retry_after

            for queue, _ in queues:
                queue.append(now)

            if self._checks % 256 == 0:
                stale = [
                    key for key, queue in self._events.items() if not queue or queue[-1] <= cutoff
                ]
                for key in stale:
                    self._events.pop(key, None)
                if len(self._events) > 4096:
                    oldest = sorted(self._events, key=lambda key: self._events[key][-1])
                    for key in oldest[: len(self._events) - 4096]:
                        self._events.pop(key, None)

            remaining = min(limit - len(queue) for queue, limit in queues)
            return True, max(0, remaining), 0


class CloudflareAccessVerifier:
    """Validate the Access application JWT that Cloudflare adds at the edge."""

    def __init__(self, *, team_domain: str, audience: str) -> None:
        raw_domain = team_domain.strip()
        if "://" not in raw_domain:
            raw_domain = f"https://{raw_domain}"
        parsed = urlparse(raw_domain)
        hostname = (parsed.hostname or "").lower()
        if (
            parsed.scheme != "https"
            or not hostname.endswith(".cloudflareaccess.com")
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
        ):
            raise RuntimeError(
                "CF_ACCESS_TEAM_DOMAIN must be an HTTPS *.cloudflareaccess.com origin"
            )
        if not audience.strip():
            raise RuntimeError("CF_ACCESS_AUD is required when Cloudflare Access is enabled")
        try:
            import jwt
        except ImportError as exc:  # pragma: no cover - packaging guard
            raise RuntimeError("Cloudflare Access validation requires PyJWT[crypto]") from exc

        self._jwt = jwt
        self.issuer = f"https://{hostname}"
        self.audience = audience.strip()
        self._jwks = jwt.PyJWKClient(
            f"{self.issuer}/cdn-cgi/access/certs",
            cache_keys=True,
            lifespan=3600,
            timeout=10,
        )

    @classmethod
    def from_environment(cls) -> CloudflareAccessVerifier:
        team_domain = (os.environ.get("CF_ACCESS_TEAM_DOMAIN") or "").strip()
        audience = (os.environ.get("CF_ACCESS_AUD") or "").strip()
        if not team_domain or not audience:
            raise RuntimeError(
                "INFERENCE_GATEWAY_REQUIRE_CF_ACCESS is enabled, but "
                "CF_ACCESS_TEAM_DOMAIN or CF_ACCESS_AUD is missing"
            )
        return cls(team_domain=team_domain, audience=audience)

    def _verify_sync(self, token: str) -> None:
        signing_key = self._jwks.get_signing_key_from_jwt(token).key
        self._jwt.decode(
            token,
            signing_key,
            algorithms=["RS256"],
            audience=self.audience,
            issuer=self.issuer,
            leeway=30,
            options={"require": ["exp", "iat", "iss", "aud"]},
        )

    async def verify(self, token: str) -> None:
        if not token or len(token) > 16_384:
            raise ValueError("missing or oversized Cloudflare Access JWT")
        await asyncio.to_thread(self._verify_sync, token)


def _expected_token() -> str:
    return (os.environ.get("INFERENCE_GATEWAY_TOKEN") or "").strip()


def _max_body() -> int:
    raw = (os.environ.get("INFERENCE_GATEWAY_MAX_BODY_BYTES") or "8000000").strip()
    try:
        return max(64_000, int(raw))
    except ValueError:
        return 8_000_000


def _backend_timeout() -> float:
    raw = (os.environ.get("INFERENCE_GATEWAY_BACKEND_TIMEOUT") or "120").strip()
    try:
        return max(5.0, float(raw))
    except ValueError:
        return 120.0


def _normalize_path(path: str) -> str:
    p = path.rstrip("/") or "/"
    return p if p.startswith("/") else f"/{p}"


def _reject_unknown_surface(method: str, raw_path: str) -> JSONResponse | None:
    m = method.upper()
    path = _normalize_path(raw_path)
    if (m, path) in GATEWAY_ALLOWED_METHOD_PATH:
        return None
    log.info(
        "gateway_rejected_route",
        extra={"method": m, "path": raw_path},
    )
    return JSONResponse(
        status_code=404,
        content={
            "error": "not_found",
            "service": "ngram-inference-gateway",
            "message": (
                "Only inference routes exist: GET /health, GET /v1/models, "
                "POST /v1/chat/completions, POST /v1/embeddings. "
                "No shell, filesystem, tools, or admin API is exposed."
            ),
        },
    )


def create_app() -> FastAPI:
    ollama_url = (os.environ.get("OLLAMA_BASE_URL") or "http://127.0.0.1:11434").strip().rstrip("/")
    require_cf_access = _env_bool("INFERENCE_GATEWAY_REQUIRE_CF_ACCESS", False)
    access_verifier = CloudflareAccessVerifier.from_environment() if require_cf_access else None
    rate_limiter = SlidingWindowRateLimiter(
        window_seconds=_bounded_env_int(
            "INFERENCE_GATEWAY_RATE_LIMIT_WINDOW_SECONDS", 60, minimum=10, maximum=3600
        ),
        authenticated_limit=_bounded_env_int(
            "INFERENCE_GATEWAY_RATE_LIMIT_AUTHENTICATED", 90, minimum=1, maximum=10_000
        ),
        unauthenticated_limit=_bounded_env_int(
            "INFERENCE_GATEWAY_RATE_LIMIT_UNAUTHENTICATED", 10, minimum=1, maximum=1_000
        ),
        global_limit=_bounded_env_int(
            "INFERENCE_GATEWAY_RATE_LIMIT_GLOBAL", 180, minimum=1, maximum=20_000
        ),
    )
    backend = OllamaCompatibleBackend(
        ollama_url,
        timeout=_backend_timeout(),
        max_body_bytes=_max_body(),
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        yield
        await backend.aclose()

    app = FastAPI(
        title="ngram Inference Gateway",
        version="1.0.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )

    @app.middleware("http")
    async def limit_body(request: Request, call_next):
        max_b = _max_body()
        cl = request.headers.get("content-length")
        if cl:
            try:
                if int(cl) > max_b:
                    return JSONResponse(
                        status_code=413,
                        content={"error": "payload_too_large", "max_bytes": max_b},
                    )
            except ValueError:
                pass
        return await call_next(request)

    @app.middleware("http")
    async def inference_surface_only(request: Request, call_next):
        """Registered after ``limit_body`` so this runs first inbound — reject junk paths early."""
        rej = _reject_unknown_surface(request.method, request.url.path)
        if rej is not None:
            return rej
        return await call_next(request)

    @app.middleware("http")
    async def security_boundary(request: Request, call_next):
        """Fail closed at the origin, even if an edge policy is later weakened."""
        authenticated = _bearer_matches(
            request.headers.get("authorization"),
            _expected_token(),
        )
        allowed, remaining, retry_after = await rate_limiter.admit(
            request,
            authenticated=authenticated,
        )
        if not allowed:
            log.warning(
                "gateway_rate_limited",
                extra={"path": request.url.path, "authenticated": authenticated},
            )
            return JSONResponse(
                status_code=429,
                content={"error": "rate_limited", "retry_after_seconds": retry_after},
                headers={"Retry-After": str(retry_after)},
            )

        # The tunnel arrives from loopback, but Cloudflare always adds its edge
        # headers. A non-loopback peer must also prove Access even if someone
        # manually widens the listener beyond the supported launcher settings.
        requires_access = access_verifier is not None and (
            _cloudflare_edge_request(request) or not _loopback_request(request)
        )
        if requires_access:
            assertion = request.headers.get("cf-access-jwt-assertion") or ""
            try:
                await access_verifier.verify(assertion)
            except Exception:
                log.warning("gateway_cf_access_rejected", extra={"path": request.url.path})
                return JSONResponse(
                    status_code=403,
                    content={"error": "cloudflare_access_required"},
                    headers={
                        "X-RateLimit-Remaining": str(remaining),
                        "X-Content-Type-Options": "nosniff",
                        "Cache-Control": "no-store",
                    },
                )

        response = await call_next(request)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        return response

    def _auth(authorization: str | None) -> None:
        exp = _expected_token()
        if not exp:
            raise HTTPException(
                status_code=503,
                detail="server_misconfigured: INFERENCE_GATEWAY_TOKEN not set",
            )
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="missing_bearer")
        if not _bearer_matches(authorization, exp):
            raise HTTPException(status_code=403, detail="invalid_token")

    @app.get("/health")
    async def health(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        _auth(authorization)
        t0 = time.perf_counter()
        try:
            await backend.get_models()
            latency_ms = round((time.perf_counter() - t0) * 1000, 2)
            return {
                "ok": True,
                "backend": "ollama_compatible",
                "ollama_base": ollama_url,
                "latency_ms": latency_ms,
            }
        except Exception as e:
            log.warning("health_failed", extra={"error": str(e)})
            return {"ok": False, "error": str(e)[:300]}

    @app.head("/health")
    async def health_head(authorization: str | None = Header(default=None)) -> Response:
        """Minimal probe without JSON body (auth still required)."""
        _auth(authorization)
        return Response(status_code=200)

    @app.get("/v1/models")
    async def list_models(authorization: str | None = Header(default=None)) -> Any:
        _auth(authorization)
        data = await backend.get_models()
        return data

    @app.head("/v1/models")
    async def list_models_head(authorization: str | None = Header(default=None)) -> Response:
        _auth(authorization)
        return Response(status_code=200)

    @app.post("/v1/chat/completions")
    async def chat_completions(
        request: Request,
        authorization: str | None = Header(default=None),
    ) -> Any:
        _auth(authorization)
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="invalid_json") from None
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail="expected_object")
        if body.get("stream"):
            resp = await backend.open_chat_completions_stream(body)
            if resp.status >= 400:
                err = await resp.text()
                await resp.release()
                raise HTTPException(status_code=resp.status, detail=err[:800])

            async def gen():
                try:
                    async for chunk in resp.content.iter_chunked(8192):
                        if chunk:
                            yield chunk
                finally:
                    await resp.release()

            ct = resp.headers.get("content-type", "text/event-stream")
            return StreamingResponse(gen(), status_code=resp.status, media_type=ct)

        status, result = await backend.post_chat_completions(body)
        if status >= 400:
            return JSONResponse(
                status_code=status,
                content=result if isinstance(result, dict) else {"error": str(result)},
            )
        return result

    @app.post("/v1/embeddings")
    async def embeddings(
        request: Request,
        authorization: str | None = Header(default=None),
    ) -> Any:
        _auth(authorization)
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="invalid_json") from None
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail="expected_object")
        status, result = await backend.post_embeddings(body)
        if status >= 400:
            return JSONResponse(status_code=status, content=result)
        return result

    return app
