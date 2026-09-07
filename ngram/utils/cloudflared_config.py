"""Read tunnel hostname from a cloudflared ``config.yml`` (same heuristics as ``gateway.ps1`` / ``gateway.sh``)."""

from __future__ import annotations

import os
import shutil
from pathlib import Path


def find_cloudflared_executable() -> str | None:
    """Resolve ``cloudflared`` on PATH, with a WinGet shim path on Windows."""
    w = shutil.which("cloudflared")
    if w:
        return w
    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA", "")
        if local:
            link = Path(local) / "Microsoft" / "WinGet" / "Links" / "cloudflared.exe"
            if link.is_file():
                return str(link)
    else:
        for candidate in (Path("/opt/homebrew/bin/cloudflared"), Path("/usr/local/bin/cloudflared")):
            if candidate.is_file():
                return str(candidate)
    return None


def default_cloudflared_config_path() -> Path:
    return Path.home() / ".cloudflared" / "config.yml"


def tunnel_https_url_from_config(config_path: Path | None = None) -> str:
    """Return ``https://<hostname>`` from the first ``hostname:`` ingress line, or ``\"\"``."""
    p = config_path if config_path is not None else default_cloudflared_config_path()
    if not p.is_file():
        return ""
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    for raw in text.splitlines():
        line = raw.strip()
        low = line.lower()
        if low.startswith("- hostname:"):
            h = line.split(":", 1)[1].strip()
            if h:
                return f"https://{h}"
        if low.startswith("hostname:") and not low.startswith("- hostname:"):
            h = line.split(":", 1)[1].strip()
            if h:
                return f"https://{h}"
    return ""
