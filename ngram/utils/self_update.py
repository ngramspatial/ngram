"""Update the running ngram install from its configured source repository."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

UPDATE_REPOSITORY_ENV = "NGRAM_UPDATE_REPOSITORY"


def _lines(msg: str) -> list[str]:
    return [ln for ln in (msg or "").splitlines() if ln.strip()]


def package_install_root() -> Path:
    """Directory containing the ``ngram`` package (…/ngram/ngram)."""
    return Path(__file__).resolve().parent.parent


def find_git_repo_root(start: Path | None = None) -> Path | None:
    """Return nearest ancestor of ``start`` that contains ``.git``, or ``None``."""
    cur = (start or package_install_root()).resolve()
    for p in [cur, *cur.parents]:
        if (p / ".git").exists():
            return p
    return None


def _run_git(
    args: list[str],
    *,
    cwd: Path,
    timeout: float = 120.0,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _configured_update_repository(repo: Path | None) -> str | None:
    configured = os.getenv(UPDATE_REPOSITORY_ENV, "").strip()
    if configured:
        return configured
    if repo is None:
        return None
    cp = _run_git(["remote", "get-url", "origin"], cwd=repo, timeout=10.0)
    remote = (cp.stdout or "").strip()
    return remote if cp.returncode == 0 and remote else None


def _remote_default_branch(repository: str, timeout: float = 60.0) -> str:
    cp = subprocess.run(
        ["git", "ls-remote", "--symref", repository, "HEAD"],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if cp.returncode != 0 or not (cp.stdout or "").strip():
        return "main"
    for line in cp.stdout.splitlines():
        # ref: refs/heads/main	HEAD
        m = re.match(r"ref:\s+refs/heads/(\S+)", line.strip())
        if m:
            return m.group(1).strip()
    return "main"


def _git_head_short(repo: Path) -> str:
    cp = _run_git(["rev-parse", "--short", "HEAD"], cwd=repo, timeout=10.0)
    return (cp.stdout or "").strip() if cp.returncode == 0 else "unknown"


def _git_current_branch(repo: Path) -> str | None:
    cp = _run_git(["branch", "--show-current"], cwd=repo, timeout=10.0)
    name = (cp.stdout or "").strip()
    return name if name and cp.returncode == 0 else None


def _pip_editable_install(repo_root: Path, log: Callable[[str], None]) -> dict[str, Any]:
    cmd = [sys.executable, "-m", "pip", "install", "-e", "."]
    log(f"Running: {' '.join(cmd)} (cwd={repo_root})")
    cp = subprocess.run(
        cmd,
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    out = ((cp.stdout or "") + "\n" + (cp.stderr or "")).strip()
    if cp.returncode != 0:
        return {"ok": False, "step": "pip", "error": out or f"exit {cp.returncode}"}
    return {"ok": True, "step": "pip", "output": out}


def _pip_install_from_repository(
    repository: str,
    log: Callable[[str], None],
) -> dict[str, Any]:
    spec = f"git+{repository}"
    cmd = [sys.executable, "-m", "pip", "install", "--upgrade", spec]
    log(f"Running: {' '.join(cmd)}")
    cp = subprocess.run(cmd, capture_output=True, text=True, timeout=600, check=False)
    out = ((cp.stdout or "") + "\n" + (cp.stderr or "")).strip()
    if cp.returncode != 0:
        return {"ok": False, "step": "pip_git", "error": out or f"exit {cp.returncode}"}
    return {"ok": True, "step": "pip_git", "output": out}


def perform_self_update(
    *,
    pip_reinstall: bool = True,
    dry_run: bool = False,
    log: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """
    Update from ``NGRAM_UPDATE_REPOSITORY`` or the clone's ``origin`` remote.

    Inside a clone, fetch and fast-forward the current default branch. Outside a
    clone, install from ``NGRAM_UPDATE_REPOSITORY`` with pip.

    ``pip_reinstall`` (git path only): run ``pip install -e .`` after a successful pull so the
    active environment matches the working tree.
    """
    _log = log or (lambda s: None)
    repo = find_git_repo_root()
    repository = _configured_update_repository(repo)
    if not repository:
        return {
            "ok": False,
            "error": (
                f"No update source configured. Set {UPDATE_REPOSITORY_ENV} to the "
                "repository URL, or configure an origin remote in this clone."
            ),
        }
    if repo is None:
        if dry_run:
            _log(f"Would run: {sys.executable} -m pip install --upgrade git+{repository}")
            return {"ok": True, "method": "pip_git", "dry_run": True}
        _log("No .git next to this install — upgrading via pip from the configured repository.")
        res = _pip_install_from_repository(repository, _log)
        res["method"] = "pip_git"
        return res

    if not shutil.which("git"):
        return {
            "ok": False,
            "error": "git not found on PATH; install Git to update this clone, or reinstall with pip.",
            "repo": str(repo),
        }

    before = _git_head_short(repo)
    branch = _remote_default_branch(repository)
    current = _git_current_branch(repo)
    if not current:
        return {
            "ok": False,
            "method": "git",
            "error": "Detached HEAD or unknown branch — checkout the default branch before running update.",
            "repo": str(repo),
            "before_ref": before,
        }
    if current != branch:
        return {
            "ok": False,
            "method": "git",
            "error": (
                f"Checked out {current!r}; ngram update fast-forwards {branch!r} from {repository}. "
                f"Run: git checkout {branch} && ngram update"
            ),
            "repo": str(repo),
            "before_ref": before,
        }

    if dry_run:
        _log(f"Would fetch {repository} ({branch}) and merge --ff-only FETCH_HEAD in {repo}")
        if pip_reinstall:
            _log(f"Would run: {sys.executable} -m pip install -e . (cwd={repo})")
        return {
            "ok": True,
            "method": "git",
            "dry_run": True,
            "repo": str(repo),
            "before_ref": before,
            "branch": branch,
        }

    fetch = _run_git(["fetch", repository, branch], cwd=repo, timeout=120.0)
    if fetch.returncode != 0:
        err = (fetch.stderr or fetch.stdout or "").strip()
        return {
            "ok": False,
            "method": "git",
            "error": err or f"git fetch failed (exit {fetch.returncode})",
            "repo": str(repo),
            "before_ref": before,
        }

    merge = _run_git(["merge", "--ff-only", "FETCH_HEAD"], cwd=repo, timeout=60.0)
    if merge.returncode != 0:
        err = (merge.stderr or merge.stdout or "").strip()
        return {
            "ok": False,
            "method": "git",
            "error": err
            or "git merge --ff-only failed (local commits or diverged branch — resolve manually)",
            "repo": str(repo),
            "before_ref": before,
            "fetch_output": _lines(fetch.stderr + "\n" + fetch.stdout),
        }

    after = _git_head_short(repo)
    messages = [
        f"Fetched and fast-forwarded to {after} (was {before}).",
        *((merge.stderr or merge.stdout or "").strip().splitlines() if (merge.stderr or merge.stdout) else []),
    ]

    result: dict[str, Any] = {
        "ok": True,
        "method": "git",
        "repo": str(repo),
        "before_ref": before,
        "after_ref": after,
        "messages": messages,
    }

    if pip_reinstall:
        pip_res = _pip_editable_install(repo, _log)
        result["pip"] = pip_res
        if not pip_res.get("ok"):
            result["ok"] = False
            result["error"] = pip_res.get("error", "pip install failed")

    return result
