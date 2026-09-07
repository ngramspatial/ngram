"""Readiness checks for ``ngram setup`` — short, actionable status lines."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from ngram.utils.cloudflared_config import (
    default_cloudflared_config_path,
    find_cloudflared_executable,
    tunnel_https_url_from_config,
)


@dataclass(frozen=True)
class PreflightItem:
    name: str
    ok: bool
    detail: str
    fix: str | None = None


def _cloudflared_cert_path() -> Path:
    return Path.home() / ".cloudflared" / "cert.pem"


def _tunnel_list_ok(exe: str) -> bool:
    r = subprocess.run(
        [exe, "tunnel", "list", "-o", "json"],
        capture_output=True,
        text=True,
        timeout=45,
    )
    if r.returncode != 0:
        return False
    raw = (r.stdout or "").strip()
    if not raw:
        return False
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return False
    return isinstance(data, list)


def _railway_bin() -> str | None:
    import shutil

    return shutil.which("railway")


def _railway_logged_in() -> bool:
    b = _railway_bin()
    if not b:
        return False
    r = subprocess.run([b, "whoami"], capture_output=True, text=True, timeout=30)
    return r.returncode == 0


def _railway_linked(cwd: Path) -> bool:
    b = _railway_bin()
    if not b:
        return False
    r = subprocess.run(
        [b, "status"],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if r.returncode != 0:
        return False
    out = r.stdout or ""
    return "Project:" in out


def collect_preflight(*, repo_root: Path) -> list[PreflightItem]:
    items: list[PreflightItem] = []

    cf = find_cloudflared_executable()
    if cf:
        items.append(
            PreflightItem(
                name="cloudflared CLI",
                ok=True,
                detail=cf,
                fix=None,
            )
        )
        cert = _cloudflared_cert_path()
        if cert.is_file():
            items.append(
                PreflightItem(
                    name="Cloudflare login cert",
                    ok=True,
                    detail=str(cert),
                    fix=None,
                )
            )
        else:
            items.append(
                PreflightItem(
                    name="Cloudflare login cert",
                    ok=False,
                    detail="~/.cloudflared/cert.pem missing",
                    fix="Run: cloudflared tunnel login   (one-time browser login)",
                )
            )
        if cert.is_file():
            list_ok = _tunnel_list_ok(cf)
            items.append(
                PreflightItem(
                    name="cloudflared API (tunnel list)",
                    ok=list_ok,
                    detail="Can list tunnels" if list_ok else "tunnel list failed",
                    fix="Run: cloudflared tunnel login",
                )
            )
    else:
        items.append(
            PreflightItem(
                name="cloudflared CLI",
                ok=False,
                detail="not on PATH",
                fix="Install: https://developers.cloudflare.com/cloudflare-one/connections/connect-apps/install-and-setup/installation/ "
                "(Windows: winget install Cloudflare.cloudflared)",
            )
        )

    cfg = default_cloudflared_config_path()
    if cfg.is_file():
        url = tunnel_https_url_from_config(cfg)
        items.append(
            PreflightItem(
                name="Tunnel config",
                ok=bool(url),
                detail=f"{cfg} → {url}" if url else f"{cfg} exists (no hostname: in ingress yet)",
                fix=None if url else "Finish tunnel setup or run automated tunnel step in ngram setup",
            )
        )
    else:
        items.append(
            PreflightItem(
                name="Tunnel config",
                ok=False,
                detail=f"{cfg} not found yet",
                fix="Normal before first run — setup can create it",
            )
        )

    rb = _railway_bin()
    if rb:
        items.append(
            PreflightItem(
                name="Railway CLI",
                ok=True,
                detail=rb,
                fix=None,
            )
        )
        items.append(
            PreflightItem(
                name="Railway login",
                ok=_railway_logged_in(),
                detail="authenticated" if _railway_logged_in() else "not logged in",
                fix="Run: railway login",
            )
        )
        if _railway_logged_in():
            linked = _railway_linked(repo_root)
            items.append(
                PreflightItem(
                    name="Railway project link",
                    ok=linked,
                    detail="this repo is linked" if linked else "not linked in this directory",
                    fix="From repo root: railway link",
                )
            )
    else:
        items.append(
            PreflightItem(
                name="Railway CLI",
                ok=False,
                detail="not on PATH",
                fix="Install: https://docs.railway.com/guides/cli  then: railway login",
            )
        )

    return items


def format_preflight_text(items: list[PreflightItem]) -> str:
    lines: list[str] = []
    for it in items:
        mark = "✓" if it.ok else "✗"
        line = f"  [{mark}] {it.name}: {it.detail}"
        lines.append(line)
        if not it.ok and it.fix:
            lines.append(f"       → {it.fix}")
    return "\n".join(lines)
