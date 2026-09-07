"""Load repo `.env` without importing the full CLI stack."""

from __future__ import annotations

import os
from pathlib import Path


def _apply_dotenv_file(path: Path) -> None:
    """Set os.environ keys from a single .env file.

    Non-empty existing values are kept; missing or blank entries are filled from the file.
    """
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip().removeprefix("\ufeff")
        val = val.strip().strip('"').strip("'")
        if not key:
            continue
        if (os.environ.get(key) or "").strip():
            continue
        os.environ[key] = val


def load_repo_dotenv(*, anchor: Path) -> None:
    """Load ``.env`` from cwd, then repo root.

    ``anchor`` must be ``Path(__file__)`` of a module one level inside a top-level
    package at the repo root (e.g. ``ngram/main.py``).
    """
    _apply_dotenv_file(Path.cwd() / ".env")
    repo_root = anchor.resolve().parent.parent
    _apply_dotenv_file(repo_root / ".env")
