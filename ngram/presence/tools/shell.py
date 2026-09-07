"""Dangerous shell/process tools routed through execution backend."""

from __future__ import annotations

import json
import re
from typing import Any

import structlog

from ngram.presence.tools.execution_rpc import get_execution_client
from ngram.presence.tools.registry import tool
from ngram.presence.tools.runtime import require_tool_runtime

log = structlog.get_logger("ngram.presence.tools.shell")


# Note: do not use bare substring "format" — it matches inside words like "information".
_DEFAULT_DENY = ["rm -rf /", "sudo rm", "shutdown", "reboot", "mkfs", "dd if="]


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = {**base}
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _shell_cfg() -> dict[str, Any]:
    ctx = require_tool_runtime()
    entity = ctx.entity
    base = entity.config.harness.tools if isinstance(entity.config.harness.tools, dict) else {}
    over = entity.config.raw.get("tools") if isinstance(entity.config.raw, dict) else {}
    merged = _deep_merge(base, over if isinstance(over, dict) else {})
    shell = merged.get("shell")
    return shell if isinstance(shell, dict) else {}


def _deny_patterns() -> list[str]:
    cfg = _shell_cfg()
    raw = cfg.get("deny")
    if isinstance(raw, list) and raw:
        return [str(x).lower().strip() for x in raw if str(x).strip()]
    return [x.lower() for x in _DEFAULT_DENY]


def _blocked(command: str) -> str | None:
    cmd_l = (command or "").lower()
    # Windows disk format — word-boundary so "information" / "transformation" are not blocked.
    if re.search(r"\bformat\s+[a-z]\s*:", cmd_l):
        return r"format <drive>:"
    for pat in _deny_patterns():
        if pat in ("format",):
            continue
        if pat and pat in cmd_l:
            return pat
    return None


def _fmt_exec_result(res: dict[str, Any]) -> str:
    if not isinstance(res, dict):
        return json.dumps({"ok": False, "error": "invalid execution result"})
    if not res.get("ok"):
        return json.dumps({"ok": False, "error": str(res.get("error") or "execution failed")})
    return json.dumps(
        {
            "ok": True,
            "exit_code": res.get("exit_code"),
            "stdout": res.get("stdout", ""),
            "stderr": res.get("stderr", ""),
            "process_id": res.get("process_id", ""),
            "running": res.get("running"),
            "stdout_tail": res.get("stdout_tail", ""),
            "stderr_tail": res.get("stderr_tail", ""),
        },
        ensure_ascii=False,
    )


@tool(
    name="run_command",
    description="Run a shell command. You can install packages, run scripts, check system status, curl APIs — anything you'd do in a terminal.",
)
async def run_command(command: str, timeout: int = 30) -> str:
    cmd = (command or "").strip()
    if not cmd:
        return json.dumps({"error": "empty command"})
    bad = _blocked(cmd)
    if bad:
        return json.dumps({"error": f"command blocked by denylist pattern: {bad}"})
    cfg = _shell_cfg()
    to = int(cfg.get("timeout") or timeout or 30)
    client = get_execution_client()
    res = await client.call("run_command", {"command": cmd, "timeout": max(1, to)})
    log.info(
        "tool_run_command",
        command=cmd,
        timeout=to,
        result_ok=bool(res.get("ok")) if isinstance(res, dict) else False,
        stdout=(res.get("stdout") if isinstance(res, dict) else ""),
        stderr=(res.get("stderr") if isinstance(res, dict) else ""),
        exit_code=(res.get("exit_code") if isinstance(res, dict) else None),
    )
    return _fmt_exec_result(res if isinstance(res, dict) else {"ok": False, "error": "invalid result"})


@tool(
    name="run_background",
    description="Start a long-running process in the background",
)
async def run_background(command: str) -> str:
    cmd = (command or "").strip()
    if not cmd:
        return json.dumps({"error": "empty command"})
    bad = _blocked(cmd)
    if bad:
        return json.dumps({"error": f"command blocked by denylist pattern: {bad}"})
    client = get_execution_client()
    res = await client.call("run_background", {"command": cmd})
    log.info("tool_run_background", command=cmd, result=res)
    return _fmt_exec_result(res if isinstance(res, dict) else {"ok": False, "error": "invalid result"})


@tool(
    name="check_process",
    description="Check the status of a background process",
)
async def check_process(process_id: str) -> str:
    pid = (process_id or "").strip()
    if not pid:
        return json.dumps({"error": "empty process_id"})
    client = get_execution_client()
    res = await client.call("check_process", {"process_id": pid})
    return _fmt_exec_result(res if isinstance(res, dict) else {"ok": False, "error": "invalid result"})


@tool(
    name="kill_process",
    description="Stop a background process",
)
async def kill_process(process_id: str) -> str:
    pid = (process_id or "").strip()
    if not pid:
        return json.dumps({"error": "empty process_id"})
    client = get_execution_client()
    res = await client.call("kill_process", {"process_id": pid})
    return _fmt_exec_result(res if isinstance(res, dict) else {"ok": False, "error": "invalid result"})
