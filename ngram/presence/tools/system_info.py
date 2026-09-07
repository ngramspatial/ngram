"""Read-only system diagnostics."""

from __future__ import annotations

import asyncio
import json
import platform
import shutil
import subprocess

from ngram.presence.tools.execution_rpc import (
    local_tool_block_message,
    local_body_host_permitted,
)
from ngram.presence.tools.registry import tool
from ngram.presence.tools.runtime import require_tool_runtime


def _gpu_info() -> str:
    nvsmi = shutil.which("nvidia-smi")
    if not nvsmi:
        return "nvidia-smi not available"
    try:
        out = subprocess.check_output(
            [
                nvsmi,
                "--query-gpu=name,memory.total,memory.used,driver_version",
                "--format=csv,noheader,nounits",
            ],
            text=True,
            timeout=4,
        )
        rows = [x.strip() for x in out.splitlines() if x.strip()]
        return "; ".join(rows[:4]) if rows else "no GPU rows"
    except Exception as e:
        return f"gpu query failed: {e}"


@tool(
    name="get_system_info",
    description="Check what machine you're running on — CPU, memory, GPU, disk space",
)
async def get_system_info() -> str:
    ctx = require_tool_runtime()
    if not local_body_host_permitted(ctx.entity):
        return json.dumps({"error": local_tool_block_message(ctx.entity)})
    try:
        import psutil  # type: ignore[import-not-found]
    except ImportError:
        return json.dumps({"error": "psutil not installed. Install with: pip install psutil"})
    vm = psutil.virtual_memory()
    du = psutil.disk_usage("/")
    cpu = psutil.cpu_percent(interval=0.3)
    gpu = await asyncio.to_thread(_gpu_info)
    out = {
        "platform": platform.platform(),
        "cpu_percent": cpu,
        "cpu_cores_logical": psutil.cpu_count(logical=True),
        "memory_used_gb": round((vm.total - vm.available) / (1024**3), 2),
        "memory_total_gb": round(vm.total / (1024**3), 2),
        "disk_used_gb": round(du.used / (1024**3), 2),
        "disk_total_gb": round(du.total / (1024**3), 2),
        "gpu": gpu,
    }
    return json.dumps(out, ensure_ascii=False)
