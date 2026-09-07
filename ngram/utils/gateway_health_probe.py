"""HTTP probes for the inference gateway ``GET /health`` (Bearer token)."""

from __future__ import annotations

import json
from typing import Any

import click
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def health_url_from_base(base: str) -> str:
    """Turn a host root or /health URL into a GET /health URL."""
    u = base.strip().rstrip("/")
    if u.lower().endswith("/health"):
        return u
    return f"{u}/health"


def probe_gateway_health(
    base_url: str,
    token: str,
    *,
    access_client_id: str = "",
    access_client_secret: str = "",
    timeout: float = 15.0,
) -> tuple[bool, str]:
    """
    GET ``/health`` with ``Authorization: Bearer``. Returns (success, short message).
    Success means HTTP 200 and JSON body has ``"ok": true`` when parseable.
    """
    url = health_url_from_base(base_url)
    headers = {"Authorization": f"Bearer {token}"}
    if access_client_id and access_client_secret:
        headers["CF-Access-Client-Id"] = access_client_id
        headers["CF-Access-Client-Secret"] = access_client_secret
    req = Request(url, headers=headers, method="GET")
    try:
        with urlopen(req, timeout=timeout) as resp:  # noqa: S310 — user-controlled URL from setup
            raw = resp.read().decode("utf-8", errors="replace")
            code = resp.getcode()
    except HTTPError as e:
        return False, f"HTTP {e.code}"
    except URLError as e:
        reason = getattr(e.reason, "args", e.reason)
        return False, f"unreachable: {reason!s}"
    except OSError as e:
        return False, str(e)[:200]

    if code != 200:
        return False, f"HTTP {code}"

    try:
        data: Any = json.loads(raw)
    except json.JSONDecodeError:
        return True, f"HTTP {code} (non-JSON)"

    if isinstance(data, dict) and data.get("ok") is True:
        lat = data.get("latency_ms")
        extra = f" latency_ms={lat}" if lat is not None else ""
        return True, f"ok{extra}"

    err = ""
    if isinstance(data, dict):
        err = str(data.get("error", ""))[:120]
    return False, err or raw[:200]


def format_probe_result(ok: bool, detail: str) -> str:
    return f"{'OK ' if ok else 'FAIL'} {detail}".strip()


def print_gateway_health_block(
    *,
    gateway_host: str,
    gateway_port: int,
    public_base_url: str,
    token: str,
    access_client_id: str = "",
    access_client_secret: str = "",
) -> None:
    """Print local (and optional tunnel) ``/health`` results for setup wizards."""
    click.echo("\n--- Gateway health ---\n")
    local_root = f"http://{gateway_host}:{gateway_port}"
    ok, msg = probe_gateway_health(local_root, token)
    click.echo(f"  Local:  [{format_probe_result(ok, msg)}]")
    if public_base_url:
        ok2, msg2 = probe_gateway_health(
            public_base_url,
            token,
            access_client_id=access_client_id,
            access_client_secret=access_client_secret,
        )
        click.echo(f"  Tunnel: [{format_probe_result(ok2, msg2)}]")
    else:
        ok2 = True
    if not ok or (public_base_url and not ok2):
        click.echo(
            "  Tip: start Ollama, run the inference gateway, then cloudflared; "
            "see README.md (Hybrid deployment) and .env.example.",
            err=True,
        )
