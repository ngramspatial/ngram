"""Ensure local Ollama is running; optional explicit model pull for first-time setup."""

from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from urllib.parse import urlparse

import click

from ngram.config import EntityConfig

# When ``--ollama`` starts the server: Popen handle (Unix / graceful) + PIDs to tear down on exit.
# On Windows the CLI often exits while a child keeps port 11434; we snapshot listeners after ready.
_ollama_serve_proc: subprocess.Popen | None = None
_ollama_shutdown_pids: set[int] = set()


def _is_local_ollama(base_url: str) -> bool:
    u = base_url.lower().rstrip("/")
    return "localhost" in u or "127.0.0.1" in u or "0.0.0.0" in u


def _tags_reachable(base_url: str, timeout_s: float = 2.0) -> bool:
    url = f"{base_url.rstrip('/')}/api/tags"
    try:
        with urllib.request.urlopen(url, timeout=timeout_s) as resp:
            code = resp.getcode()
            return 200 <= code < 300
    except (urllib.error.URLError, OSError, TimeoutError):
        return False


def _wait_ready(base_url: str, *, timeout_s: float = 120.0, interval_s: float = 0.5) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if _tags_reachable(base_url):
            return True
        time.sleep(interval_s)
    return False


def _win_no_window_kw() -> dict:
    f = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return {"creationflags": f} if f else {}


def _win_pids_listening_on_tcp_port(port: int) -> set[int]:
    """PIDs with LISTENING sockets on ``port`` (English Windows netstat)."""
    pids: set[int] = set()
    try:
        r = subprocess.run(
            ["cmd", "/c", f"netstat -ano -p TCP | findstr :{port}"],
            capture_output=True,
            text=True,
            timeout=30,
            **_win_no_window_kw(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return pids
    tail_pid = re.compile(r"(\d+)\s*$")
    for line in (r.stdout or "").splitlines():
        up = line.upper()
        if "LISTENING" not in up:
            continue
        if f":{port}" not in line:
            continue
        m = tail_pid.search(line.strip())
        if m:
            try:
                pids.add(int(m.group(1)))
            except ValueError:
                pass
    return pids


def _register_ollama_shutdown_pids_after_ready(port: int = 11434) -> None:
    """Record processes actually holding the API port (esp. Windows child daemons)."""
    global _ollama_shutdown_pids
    if sys.platform == "win32":
        time.sleep(0.35)
        _ollama_shutdown_pids.update(_win_pids_listening_on_tcp_port(port))
    elif _ollama_serve_proc and _ollama_serve_proc.pid:
        _ollama_shutdown_pids.add(_ollama_serve_proc.pid)


def _api_port_from_base_url(base_url: str) -> int:
    u = urlparse(base_url)
    return int(u.port) if u.port is not None else 11434


def _spawn_ollama_serve() -> subprocess.Popen:
    global _ollama_serve_proc
    kwargs: dict = {
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "stdin": subprocess.DEVNULL,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    else:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(["ollama", "serve"], **kwargs)
    _ollama_serve_proc = proc
    if proc.pid:
        _ollama_shutdown_pids.add(proc.pid)
    return proc


def shutdown_spawned_ollama() -> None:
    """Stop Ollama only if ngram started it (``--ollama``). Kills the whole process tree on Windows."""
    global _ollama_serve_proc, _ollama_shutdown_pids
    pids = sorted(_ollama_shutdown_pids, reverse=True)
    _ollama_shutdown_pids.clear()
    proc = _ollama_serve_proc
    _ollama_serve_proc = None

    if sys.platform == "win32":
        for pid in pids:
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=45,
                    **_win_no_window_kw(),
                )
            except (OSError, subprocess.TimeoutExpired):
                pass
        return

    for pid in pids:
        try:
            os.killpg(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                os.kill(pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError, OSError):
                pass
    if proc is not None and proc.poll() is None:
        try:
            proc.wait(timeout=12)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
            except OSError:
                pass
        except OSError:
            pass


def _required_model_names(ec: EntityConfig) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for m in (
        ec.cognition.reflex_model,
        ec.cognition.deliberate_model,
        ec.harness.models.embedding,
    ):
        if m and m not in seen:
            seen.add(m)
            out.append(m)
    return out


def pull_required_models(ec: EntityConfig, info: Callable[[str], None] = print) -> None:
    """Run ``ollama pull`` for each model in config (use when setting up a new machine)."""
    _pull_models_impl(ec, info)


def _pull_models_impl(ec: EntityConfig, info: Callable[[str], None]) -> None:
    models = _required_model_names(ec)
    info(f"Pulling models: {', '.join(models)}")
    for m in models:
        info(f"  → pull {m}")
        r = subprocess.run(["ollama", "pull", m], check=False)
        if r.returncode != 0:
            raise click.ClickException(
                f"`ollama pull {m}` failed (exit {r.returncode}). Fix the error above and retry."
            )
    info("Pull complete.")


def bootstrap_stack(
    ec: EntityConfig,
    info: Callable[[str], None] = print,
    *,
    pull_models: bool = False,
) -> None:
    """
    If Ollama base URL looks local and the API is down, start `ollama serve` and wait.
    Does not pull models unless ``pull_models=True`` (ngram assumes models already exist).
    """
    base = ec.harness.ollama.base_url.rstrip("/")

    if _is_local_ollama(base):
        if _tags_reachable(base):
            info("Ollama is already running.")
        else:
            time.sleep(0.25)
            if _tags_reachable(base):
                info("Ollama is already running.")
                if pull_models:
                    _pull_models_impl(ec, info)
                return
            info("Ollama not reachable — starting `ollama serve` in the background…")
            try:
                _spawn_ollama_serve()
            except FileNotFoundError as e:
                raise click.ClickException(
                    "Could not run `ollama serve`: is Ollama installed and on your PATH?"
                ) from e
            if not _wait_ready(base):
                raise click.ClickException(
                    "Ollama did not become ready in time. Check that port 11434 is free and "
                    "`ollama` works in a terminal."
                )
            _register_ollama_shutdown_pids_after_ready(port=_api_port_from_base_url(base))
            info("Ollama is up.")
        if pull_models:
            _pull_models_impl(ec, info)
        return

    info(f"Using remote Ollama at {base} — not starting a local server.")
    if not _tags_reachable(base, timeout_s=5.0):
        raise click.ClickException(
            f"Cannot reach Ollama at {base}. Start it or fix harness.ollama.base_url."
        )
    if pull_models:
        info("Skipping `ollama pull` (remote server — install models on that host).")
