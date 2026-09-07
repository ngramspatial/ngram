"""Locate and run the home gateway helper (``scripts/gateway.ps1`` on Windows, ``scripts/gateway.sh`` on macOS/Linux)."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import click


def _gateway_script_name() -> str:
    return "gateway.ps1" if os.name == "nt" else "gateway.sh"


def gateway_script_path() -> Path:
    name = _gateway_script_name()
    candidates = [
        Path.cwd() / "scripts" / name,
        Path(__file__).resolve().parent.parent.parent / "scripts" / name,
    ]
    for p in candidates:
        if p.is_file():
            return p
    checked = ", ".join(str(p) for p in candidates)
    raise click.ClickException(
        f"Could not locate scripts/{name}. Checked: {checked}. Run from the repo root."
    )


def gateway_script_available() -> bool:
    try:
        gateway_script_path()
    except click.ClickException:
        return False
    return True


def run_gateway_script(action: str, extra_args: list[str] | None = None) -> int:
    """Run gateway.ps1 (Windows) or gateway.sh (POSIX). Returns process exit code."""
    script = gateway_script_path()
    if os.name == "nt":
        cmd = [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            action,
        ]
    else:
        bash = shutil.which("bash") or "/bin/bash"
        cmd = [bash, str(script), action]
    if extra_args:
        cmd.extend(extra_args)
    return subprocess.call(cmd)
