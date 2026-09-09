"""Explicit deadlines must release the goal loop, including command descendants."""

import json
import os
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from ngram.presence.tools import code, shell
from ngram.presence.tools.execution_rpc import _run_process
from ngram.presence.tools.runtime import ToolRuntimeContext, reset_tool_runtime, set_tool_runtime
from ngram.presence.tools.registry import ToolRegistry


@pytest.mark.asyncio
@pytest.mark.parametrize(("module", "name", "args"), [
    (shell, "run_command", {"command": "echo ok"}),
    (code, "execute_python", {"code": "print('ok')"}),
    (code, "execute_javascript", {"code": "console.log('ok')"}),
])
async def test_explicit_timeout_wins_over_large_configured_default(monkeypatch, module, name, args):
    calls = []

    async def call(action, payload):
        calls.append(payload)
        return {"ok": True, "exit_code": 0}

    monkeypatch.setattr(module, "get_execution_client", lambda: SimpleNamespace(call=call))
    entity = SimpleNamespace(config=SimpleNamespace(
        harness=SimpleNamespace(tools={"shell": {"timeout": 1800}, "code": {"timeout": 1800}}), raw={},
    ))
    token = set_tool_runtime(ToolRuntimeContext(entity=entity))
    try:
        fn = getattr(module, name)
        await fn(**args, timeout=180)
        await fn(**args)
    finally:
        reset_tool_runtime(token)
    assert [call["timeout"] for call in calls] == [180, 1800]
    registry = ToolRegistry()
    registry.register_decorated(fn)
    schema = registry.tool_discovery_detail(name)["parameters"]
    assert schema["properties"]["timeout"]["type"] == "integer"
    assert "timeout" not in schema.get("required", [])


def test_deadline_kills_a_command_and_its_child_instead_of_waiting_on_inherited_pipes(tmp_path):
    pid_file = tmp_path / "child.json"
    source = tmp_path / "parent.py"
    source.write_text(
        "import subprocess,sys,time,json\nfrom pathlib import Path\n"
        "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'])\n"
        f"Path({str(pid_file)!r}).write_text(json.dumps(child.pid))\n"
        "print('started',flush=True)\ntime.sleep(60)\n",
        encoding="utf-8",
    )
    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        _run_process([sys.executable, str(source)], cwd=str(tmp_path), timeout=1)
    assert time.monotonic() - started < 9
    pid = json.loads(pid_file.read_text())
    if os.name == "nt":
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if handle:
            try:
                code = ctypes.c_ulong()
                assert ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
                assert code.value != 259, "child is still running"
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)
    else:
        from pathlib import Path
        stat = Path(f"/proc/{pid}/stat")
        if stat.exists():
            assert stat.read_text().split()[2] == "Z", "child is still running"


def test_successful_command_retains_its_output_and_exit_code(tmp_path):
    result = _run_process([sys.executable, "-c", "print('verified'); raise SystemExit(7)"],
                          cwd=str(tmp_path), timeout=5)
    assert result.returncode == 7
    assert result.stdout.strip() == "verified"
