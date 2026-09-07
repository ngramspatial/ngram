"""Find and stop local ngram CLI processes and optionally every Ollama OS process."""

from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
from collections.abc import Callable

import psutil

# Match ``python -m ngram.main <subcommand>`` but not ``ngram.main stop`` / ``help`` / etc.
_MAIN_SUBCOMMANDS = re.compile(
    r"ngram\.main\s+(?:run|worker|api|talk)\b",
)
_GATEWAY_MOD = re.compile(r"ngram\.inference_gateway\b")


def _cmdline_join(proc: psutil.Process) -> str:
    try:
        return " ".join(proc.cmdline() or ())
    except (psutil.Error, OSError):
        return ""


def iter_local_ngram_stack_pids(*, skip_pids: set[int] | None = None) -> list[int]:
    """PIDs for processes that look like local ngram stack (excluding skip_pids)."""
    skip = set(skip_pids or ())
    skip.add(os.getpid())
    found: set[int] = set()
    for proc in psutil.process_iter():
        try:
            if proc.pid in skip:
                continue
            cl = _cmdline_join(proc)
            if not cl:
                continue
            if _MAIN_SUBCOMMANDS.search(cl) or _GATEWAY_MOD.search(cl):
                found.add(proc.pid)
        except (psutil.Error, OSError):
            continue
    return sorted(found)


def stop_local_ngram_processes(
    *,
    skip_pids: set[int] | None = None,
    dry_run: bool = False,
    log: Callable[[str], None] = print,
) -> list[int]:
    """
    Terminate matching processes (whole tree on Windows per matching root).
    Returns list of PIDs targeted.
    """
    pids = iter_local_ngram_stack_pids(skip_pids=skip_pids)
    if not pids:
        log("No local ngram processes found (run/worker/api/talk/inference_gateway).")
        return []

    if dry_run:
        for pid in pids:
            try:
                p = psutil.Process(pid)
                log(f"Would stop PID {pid}: {_cmdline_join(p)[:200]}")
            except (psutil.Error, OSError):
                log(f"Would stop PID {pid}")
        return pids

    stopped: list[int] = []
    for pid in pids:
        try:
            if sys.platform == "win32":
                r = subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    capture_output=True,
                    text=True,
                    timeout=60,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                if r.returncode == 0:
                    stopped.append(pid)
                    log(f"Stopped process tree (PID {pid}).")
                else:
                    err = (r.stderr or r.stdout or "").strip()
                    log(f"PID {pid}: taskkill exit {r.returncode}" + (f" — {err[:120]}" if err else ""))
            else:
                proc = psutil.Process(pid)
                children = proc.children(recursive=True)
                for c in children:
                    try:
                        c.send_signal(signal.SIGTERM)
                    except (psutil.Error, OSError):
                        try:
                            c.kill()
                        except (psutil.Error, OSError):
                            pass
                try:
                    proc.send_signal(signal.SIGTERM)
                except (psutil.Error, OSError):
                    try:
                        proc.kill()
                    except (psutil.Error, OSError):
                        pass
                stopped.append(pid)
                log(f"Sent SIGTERM to PID {pid} (and children).")
        except (psutil.Error, OSError, subprocess.TimeoutExpired) as e:
            log(f"PID {pid}: {e}")

    return stopped


def _is_ollama_os_process(proc: psutil.Process) -> bool:
    try:
        name = (proc.name() or "").lower()
    except (psutil.Error, OSError):
        return False
    if sys.platform == "win32":
        return name == "ollama.exe"
    return name == "ollama"


def iter_ollama_pids(*, skip_pids: set[int] | None = None) -> list[int]:
    """PIDs whose executable name is Ollama (all instances: Desktop, ``serve``, runners)."""
    skip = set(skip_pids or ())
    skip.add(os.getpid())
    found: set[int] = set()
    for proc in psutil.process_iter():
        try:
            if proc.pid in skip:
                continue
            if _is_ollama_os_process(proc):
                found.add(proc.pid)
        except (psutil.Error, OSError):
            continue
    return sorted(found)


def stop_all_ollama_processes(
    *,
    skip_pids: set[int] | None = None,
    dry_run: bool = False,
    log: Callable[[str], None] = print,
) -> list[int]:
    """
    Terminate every local ``ollama`` / ``ollama.exe`` process (full trees on Windows).
    Use when you want GPU memory released, not only ``ollama serve`` started by the gateway script.
    """
    pids = iter_ollama_pids(skip_pids=skip_pids)
    if not pids:
        log("No Ollama OS processes found (ollama / ollama.exe).")
        return []

    if dry_run:
        for pid in pids:
            try:
                p = psutil.Process(pid)
                log(f"Would stop Ollama PID {pid}: {_cmdline_join(p)[:200]}")
            except (psutil.Error, OSError):
                log(f"Would stop Ollama PID {pid}")
        return pids

    stopped: list[int] = []
    for pid in pids:
        try:
            if sys.platform == "win32":
                r = subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    capture_output=True,
                    text=True,
                    timeout=60,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                if r.returncode == 0:
                    stopped.append(pid)
                    log(f"Stopped Ollama process tree (PID {pid}).")
                else:
                    err = (r.stderr or r.stdout or "").strip()
                    log(
                        f"Ollama PID {pid}: taskkill exit {r.returncode}"
                        + (f" — {err[:120]}" if err else "")
                    )
            else:
                proc = psutil.Process(pid)
                for c in proc.children(recursive=True):
                    try:
                        c.send_signal(signal.SIGTERM)
                    except (psutil.Error, OSError):
                        try:
                            c.kill()
                        except (psutil.Error, OSError):
                            pass
                try:
                    proc.send_signal(signal.SIGTERM)
                except (psutil.Error, OSError):
                    try:
                        proc.kill()
                    except (psutil.Error, OSError):
                        pass
                stopped.append(pid)
                log(f"Sent SIGTERM to Ollama PID {pid} (and children).")
        except (psutil.Error, OSError, subprocess.TimeoutExpired) as e:
            log(f"Ollama PID {pid}: {e}")

    return stopped
