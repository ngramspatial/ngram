"""Best-effort git metadata for the running install (no hard dependency on .git)."""

from __future__ import annotations

import subprocess
from pathlib import Path

# Package dir (…/ngram/) — typically inside a git checkout when developing or deployed from git.
_PACKAGE_DIR = Path(__file__).resolve().parent.parent


def git_head_info() -> tuple[str | None, str | None]:
    """Return ``(short_hash, subject)`` for HEAD, or ``(None, None)`` if unavailable."""
    try:
        rh = subprocess.run(
            ["git", "-C", str(_PACKAGE_DIR), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=4,
        )
        rs = subprocess.run(
            ["git", "-C", str(_PACKAGE_DIR), "log", "-1", "--pretty=%s"],
            capture_output=True,
            text=True,
            timeout=4,
        )
        if rh.returncode != 0 or rs.returncode != 0:
            return None, None
        h = (rh.stdout or "").strip() or None
        subj = (rs.stdout or "").strip() or None
        return h, subj
    except (OSError, subprocess.TimeoutExpired):
        return None, None
