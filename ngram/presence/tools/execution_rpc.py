"""Shared execution client for dangerous tools (RPC first, guarded local fallback).

Hybrid (`hybrid_railway`) is meant to run the **body** on Railway. Local subprocess/file
fallback is only allowed there (``RAILWAY_ENVIRONMENT``) or when ``tools.execution.allow_local``
is true — so a home workstation with hybrid env vars does not silently become the execution host.
Git/pip **self-update** is a separate gate (``self_update_host_permitted``): hybrid home runners may
refresh their local clone without ``allow_local``, unless ``require_railway_execution`` blocks off-Railway hosts.

Read-only workspace tools (``read_file``, ``list_directory``, ``search_files``) use the same
``ExecutionRPCClient`` as writes: **RPC first** when ``NGRAM_EXECUTION_RPC_URL`` /
``tools.execution.base_url`` is set, otherwise local execution under ``tools.execution.workspace_dir``
when ``local_body_host_permitted`` is true. Paths are resolved under the configured workspace root
(like ``write_file``), not arbitrary absolute paths outside it.

``read_pdf`` and ``get_system_info`` still use direct local OS access with the hybrid guard only.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiohttp
import structlog

from ngram.presence.tools.runtime import require_tool_runtime

log = structlog.get_logger("ngram.presence.tools.execution")

_CLIENTS: dict[
    tuple[str, str, str, float, bool, Path],
    ExecutionRPCClient,
] = {}
_BG_PROCS: dict[str, dict[str, Any]] = {}


def _run_process(command: str | list[str], *, cwd: str, timeout: float, shell: bool = False):
    """Bound the entire command tree, including children holding inherited pipes.

    Killing only a shell on timeout can leave Blender alive and communicate()
    waiting forever. Each invocation owns its process group (or Windows tree).
    """
    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
    # Files avoid unbounded in-memory logs and pipe-reader waits on descendants.
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        process = subprocess.Popen(command, cwd=cwd, shell=shell, stdout=stdout,
                                   stderr=stderr, **options)  # noqa: S602
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired as error:
            if os.name == "nt":
                try:
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                   capture_output=True, timeout=5, creationflags=subprocess.CREATE_NO_WINDOW)
                except (OSError, subprocess.TimeoutExpired):
                    process.kill()
            else:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            raise error
        stdout.seek(0)
        stderr.seek(0)
        return subprocess.CompletedProcess(command, process.returncode,
            stdout.read(20000).decode("utf-8", errors="replace"), stderr.read(20000).decode("utf-8", errors="replace"))


REMOTE_SESSION_RPC_ACTIONS = frozenset(
    {
        "desktop_session_start",
        "desktop_session_status",
        "desktop_session_capture",
        "desktop_session_input",
        "desktop_session_stop",
    }
)


@dataclass
class ExecutionEnvironment:
    kind: str
    workspace_root: str
    base_url: str
    allow_local_backend: bool
    require_railway: bool


def _rpc_error_suggests_unknown_action(res: dict[str, Any]) -> bool:
    """True when the remote execution service does not implement this action (older hands build)."""
    err = str(res.get("error") or "").lower()
    return "unknown action" in err or "unknown_action" in err


def should_fallback_rpc_to_local(res: dict[str, Any]) -> bool:
    """
    True when the HTTP execution RPC failed in a way that suggests the URL is wrong or down,
    so in-container / local execution is a reasonable fallback (e.g. Railway worker with a
    leftover NGRAM_EXECUTION_RPC_URL pointing at a home tunnel that is off).
    """
    err = str(res.get("error") or "").lower()
    if "rpc http 401" in err or "rpc http 403" in err:
        return False
    if "rpc http 404" in err or "rpc http 405" in err or "rpc http 422" in err:
        return False
    if "rpc http 500" in err or "rpc http 501" in err:
        return False
    if "rpc http 502" in err or "rpc http 503" in err or "rpc http 504" in err:
        return True
    transport_markers = (
        "cannot connect",
        "connection refused",
        "name or service not known",
        "getaddrinfo failed",
        "temporary failure in name resolution",
        "timed out",
        "timeout",
        "sslcertverificationerror",
        "certificate verify failed",
        "newconnectionerror",
        "clientconnectorerror",
        "connection reset",
        "nodename nor servname",
    )
    return any(m in err for m in transport_markers)


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {**base}
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _effective_tools_config(entity: Any) -> dict[str, Any]:
    base = entity.config.harness.tools if isinstance(entity.config.harness.tools, dict) else {}
    over = entity.config.raw.get("tools") if isinstance(entity.config.raw, dict) else {}
    if not isinstance(over, dict):
        over = {}
    return _deep_merge(base, over)


def require_railway_execution(entity: Any) -> bool:
    """
    True when this worker must never execute shell/fs/code on an off-Railway host.

    This still allows in-container execution on Railway itself, and still allows
    HTTP execution RPC to another host when ``NGRAM_EXECUTION_RPC_URL`` /
    ``tools.execution.base_url`` is configured.
    """
    tools_cfg = _effective_tools_config(entity)
    exec_cfg = tools_cfg.get("execution") if isinstance(tools_cfg.get("execution"), dict) else {}
    if not isinstance(exec_cfg, dict):
        exec_cfg = {}
    raw = (
        (os.environ.get("NGRAM_EXECUTION_REQUIRE_RAILWAY") or "").strip()
        or str(exec_cfg.get("require_railway") or "").strip()
    )
    return raw.lower() in ("1", "true", "yes", "on")


def _configured_workspace_root(entity: Any) -> Path:
    """Workspace root for local execution fallback / in-container tools."""
    tools_cfg = _effective_tools_config(entity)
    exec_cfg = tools_cfg.get("execution") if isinstance(tools_cfg.get("execution"), dict) else {}
    if not isinstance(exec_cfg, dict):
        exec_cfg = {}
    workspace_dir = (
        (os.environ.get("NGRAM_EXECUTION_WORKSPACE_DIR") or "").strip()
        or str(exec_cfg.get("workspace_dir") or "").strip()
    )
    return Path(workspace_dir).expanduser().resolve() if workspace_dir else Path.cwd().resolve()


def _tail_text(path: Path, max_bytes: int = 12000) -> str:
    if not path.exists():
        return ""
    try:
        data = path.read_bytes()
    except OSError:
        return ""
    if len(data) > max_bytes:
        data = data[-max_bytes:]
    return data.decode("utf-8", errors="replace")


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ExecutionRPCClient:
    base_url: str
    rpc_path: str
    token: str
    timeout: float
    allow_local_backend: bool
    workspace_root: Path

    def environment(self) -> ExecutionEnvironment:
        kind = "rpc" if self.base_url else "local"
        if kind == "local" and (os.environ.get("RAILWAY_ENVIRONMENT") or "").strip():
            kind = "railway"
        return ExecutionEnvironment(
            kind=kind,
            workspace_root=str(self.workspace_root),
            base_url=self.base_url,
            allow_local_backend=self.allow_local_backend,
            require_railway=bool((os.environ.get("NGRAM_EXECUTION_REQUIRE_RAILWAY") or "").strip()),
        )

    def _ops_dir(self) -> Path:
        p = self.workspace_root / ".ngram" / "ops"
        p.mkdir(parents=True, exist_ok=True)
        return p

    def _checkpoints_dir(self) -> Path:
        p = self._ops_dir() / "checkpoints"
        p.mkdir(parents=True, exist_ok=True)
        return p

    def _audit_log_path(self) -> Path:
        return self._ops_dir() / "action_log.jsonl"

    def _write_audit_event(
        self,
        *,
        action: str,
        payload: dict[str, Any],
        result: dict[str, Any],
    ) -> None:
        rec = {
            "at": _iso_now(),
            "action": action,
            "workspace_root": str(self.workspace_root),
            "environment": self.environment().__dict__,
            "payload": payload,
            "result": result,
        }
        try:
            with self._audit_log_path().open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except OSError:
            pass

    def _create_checkpoint(self, target: Path, action: str) -> dict[str, Any]:
        cp_id = f"cp_{uuid.uuid4().hex[:12]}"
        cp_dir = self._checkpoints_dir()
        meta_path = cp_dir / f"{cp_id}.json"
        data_path = cp_dir / f"{cp_id}.bak"
        existed = target.exists()
        is_file = target.is_file()
        if existed and is_file:
            shutil.copy2(target, data_path)
        meta = {
            "id": cp_id,
            "action": action,
            "target": str(target),
            "workspace_root": str(self.workspace_root),
            "created_at": _iso_now(),
            "existed": existed,
            "is_file": is_file,
            "backup_path": str(data_path) if existed and is_file else "",
        }
        meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
        return meta

    def _list_checkpoints(self, limit: int) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for meta_path in sorted(
            self._checkpoints_dir().glob("*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )[:limit]:
            try:
                rows.append(json.loads(meta_path.read_text(encoding="utf-8", errors="replace")))
            except (OSError, json.JSONDecodeError):
                continue
        return rows

    def _rollback_checkpoint(self, checkpoint_id: str) -> dict[str, Any]:
        cid = (checkpoint_id or "").strip()
        if not cid:
            return {"ok": False, "error": "empty checkpoint_id"}
        meta_path = self._checkpoints_dir() / f"{cid}.json"
        if not meta_path.is_file():
            return {"ok": False, "error": f"unknown checkpoint_id: {cid}"}
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, json.JSONDecodeError) as e:
            return {"ok": False, "error": str(e)}
        target = Path(str(meta.get("target") or ""))
        if not target:
            return {"ok": False, "error": "checkpoint missing target"}
        backup_path = Path(str(meta.get("backup_path") or ""))
        existed = bool(meta.get("existed"))
        try:
            if existed and backup_path.is_file():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(backup_path, target)
            else:
                target.unlink(missing_ok=True)
            return {"ok": True, "checkpoint_id": cid, "target": str(target)}
        except OSError as e:
            return {"ok": False, "error": str(e)}

    async def call(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        if self.base_url:
            res = await self._call_http(action, payload)
            if res.get("ok"):
                return res
            # A persistent project must never silently migrate to another computer.
            if action == "blender":
                return res
            if self.allow_local_backend and (
                should_fallback_rpc_to_local(res) or _rpc_error_suggests_unknown_action(res)
            ):
                log.warning(
                    "execution_rpc_unreachable_fallback_local",
                    action=action,
                    error=str(res.get("error") or "")[:300],
                )
                return await asyncio.to_thread(self._call_local, action, payload)
            return res
        if self.allow_local_backend:
            return await asyncio.to_thread(self._call_local, action, payload)
        return {
            "ok": False,
            "error": (
                "Execution backend unavailable. Configure tools.execution.base_url "
                "(or NGRAM_EXECUTION_RPC_URL), or enable tools.execution.allow_local."
            ),
        }

    async def _call_http(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        base = self.base_url.rstrip("/")
        path = self.rpc_path.strip() or "/rpc"
        if not path.startswith("/"):
            path = "/" + path
        url = f"{base}{path}"
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        body = {"action": action, "payload": payload or {}}
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=max(5.0, self.timeout)),
            ) as session:
                async with session.post(url, json=body, headers=headers) as resp:
                    txt = await resp.text()
                    try:
                        data = json.loads(txt) if txt else {}
                    except json.JSONDecodeError:
                        data = {"ok": False, "error": txt[:600]}
                    if resp.status >= 400 and "error" not in data:
                        data["error"] = f"rpc http {resp.status}"
                    if "ok" not in data:
                        data["ok"] = resp.status < 400
                    return data
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def _resolve_workspace_path(self, path: str) -> Path:
        p = Path(path).expanduser()
        if p.is_absolute():
            resolved = p.resolve()
        else:
            resolved = (self.workspace_root / p).resolve()
        root = self.workspace_root.resolve()
        if resolved != root and root not in resolved.parents:
            raise RuntimeError(f"path escapes workspace: {resolved}")
        return resolved

    def _call_local(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            if action == "blender":
                from ngram.presence.tools.blender_runtime import runtime_for
                return runtime_for(self.workspace_root).command(payload)
            if action == "get_execution_context":
                return {"ok": True, "environment": self.environment().__dict__}

            if action == "list_checkpoints":
                limit = max(1, min(int(payload.get("limit") or 20), 100))
                return {"ok": True, "checkpoints": self._list_checkpoints(limit)}

            if action == "rollback_checkpoint":
                return self._rollback_checkpoint(str(payload.get("checkpoint_id") or ""))

            if action == "run_command":
                cmd = str(payload.get("command") or "").strip()
                timeout = int(payload.get("timeout") or self.timeout)
                if not cmd:
                    return {"ok": False, "error": "empty command"}
                p = _run_process(
                    cmd,
                    shell=True,
                    cwd=str(self.workspace_root),
                    timeout=max(1, timeout),
                )
                res = {
                    "ok": True,
                    "exit_code": int(p.returncode),
                    "stdout": (p.stdout or "")[:20000],
                    "stderr": (p.stderr or "")[:20000],
                }
                self._write_audit_event(action="run_command", payload=payload, result=res)
                return res

            if action == "run_background":
                cmd = str(payload.get("command") or "").strip()
                if not cmd:
                    return {"ok": False, "error": "empty command"}
                bg_dir = self.workspace_root / ".ngram" / "background"
                bg_dir.mkdir(parents=True, exist_ok=True)
                proc_id = f"bg_{uuid.uuid4().hex[:10]}"
                out_path = bg_dir / f"{proc_id}.out.log"
                err_path = bg_dir / f"{proc_id}.err.log"
                with out_path.open("wb") as out_f, err_path.open("wb") as err_f:
                    proc = subprocess.Popen(  # noqa: S602
                        cmd,
                        shell=True,
                        cwd=str(self.workspace_root),
                        stdout=out_f,
                        stderr=err_f,
                    )
                _BG_PROCS[proc_id] = {
                    "proc": proc,
                    "out": out_path,
                    "err": err_path,
                    "command": cmd,
                    "started_at": time.time(),
                }
                res = {"ok": True, "process_id": proc_id, "pid": int(proc.pid)}
                self._write_audit_event(action="run_background", payload=payload, result=res)
                return res

            if action == "check_process":
                proc_id = str(payload.get("process_id") or "").strip()
                item = _BG_PROCS.get(proc_id)
                if not item:
                    return {"ok": False, "error": f"unknown process_id: {proc_id}"}
                proc = item["proc"]
                code = proc.poll()
                return {
                    "ok": True,
                    "process_id": proc_id,
                    "running": code is None,
                    "exit_code": None if code is None else int(code),
                    "stdout_tail": _tail_text(item["out"]),
                    "stderr_tail": _tail_text(item["err"]),
                    "command": item["command"],
                }

            if action == "kill_process":
                proc_id = str(payload.get("process_id") or "").strip()
                item = _BG_PROCS.get(proc_id)
                if not item:
                    return {"ok": False, "error": f"unknown process_id: {proc_id}"}
                proc = item["proc"]
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=4)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                res = {
                    "ok": True,
                    "process_id": proc_id,
                    "exit_code": None if proc.poll() is None else int(proc.poll()),
                }
                self._write_audit_event(action="kill_process", payload=payload, result=res)
                return res

            if action == "list_directory":
                raw_path = str(payload.get("path") or ".").strip() or "."
                target = self._resolve_workspace_path(raw_path)
                if not target.exists():
                    return {"ok": False, "error": f"path not found: {target}"}
                if not target.is_dir():
                    return {"ok": False, "error": f"not a directory: {target}"}
                try:
                    out: list[dict[str, str | int]] = []
                    for child in sorted(target.iterdir(), key=lambda c: (not c.is_dir(), c.name.lower()))[:500]:
                        kind = "dir" if child.is_dir() else "file"
                        size = child.stat().st_size if child.is_file() else 0
                        out.append({"name": child.name, "kind": kind, "size": int(size)})
                    return {"ok": True, "path": str(target), "entries": out}
                except OSError as e:
                    return {"ok": False, "error": str(e)}

            if action == "read_file":
                max_bytes = int(payload.get("max_bytes") or 48000)
                # 1 MiB cap — send_file and large logs (e.g. autonomy_transcript.md); default reads stay small.
                max_bytes = max(1024, min(max_bytes, 1_048_576))
                raw_path = str(payload.get("path") or ".").strip() or "."
                target = self._resolve_workspace_path(raw_path)
                if not target.exists():
                    return {"ok": False, "error": f"path not found: {target}"}
                if not target.is_file():
                    return {"ok": False, "error": f"not a file: {target}"}
                try:
                    def _int_field(key: str) -> int:
                        v = payload.get(key)
                        if v is None or v == "":
                            return 0
                        try:
                            return int(v)
                        except (TypeError, ValueError):
                            return 0

                    start_line = _int_field("start_line")
                    end_line = _int_field("end_line")
                    if start_line > 0:
                        if end_line > 0 and end_line < start_line:
                            return {"ok": False, "error": "end_line must be >= start_line"}
                        end_eff = end_line if end_line >= start_line else start_line
                        max_span = 400
                        if end_eff - start_line + 1 > max_span:
                            return {
                                "ok": False,
                                "error": f"line range too wide (max {max_span} lines per call)",
                            }
                        lines_out: list[tuple[int, str]] = []
                        with target.open("r", encoding="utf-8", errors="replace") as f:
                            for i, line in enumerate(f, start=1):
                                if i > end_eff:
                                    break
                                if i >= start_line:
                                    lines_out.append((i, line.rstrip("\r\n")))
                        if not lines_out:
                            return {
                                "ok": False,
                                "error": f"no lines in range {start_line}-{end_eff} (file may be shorter)",
                            }
                        header = f"--- lines {start_line}-{lines_out[-1][0]} of {target.name} (1-based) ---\n"
                        body = "\n".join(f"{n:6d}|{text}" for n, text in lines_out)
                        return {
                            "ok": True,
                            "path": str(target),
                            "content": header + body,
                            "line_mode": True,
                            "start_line": start_line,
                            "end_line": lines_out[-1][0],
                        }
                    data = target.read_bytes()[:max_bytes]
                    return {
                        "ok": True,
                        "path": str(target),
                        "content": data.decode("utf-8", errors="replace"),
                    }
                except OSError as e:
                    return {"ok": False, "error": str(e)}

            if action == "search_files":
                directory = str(payload.get("directory") or ".").strip() or "."
                pattern = str(payload.get("pattern") or "").strip() or "*"
                d = self._resolve_workspace_path(directory)
                if not d.exists() or not d.is_dir():
                    return {"ok": False, "error": f"directory not found: {d}"}
                try:
                    matches = [str(p) for p in d.rglob(pattern) if p.is_file()][:1000]
                    return {"ok": True, "directory": str(d), "pattern": pattern, "matches": matches}
                except OSError as e:
                    return {"ok": False, "error": str(e)}

            if action == "write_file":
                target = self._resolve_workspace_path(str(payload.get("path") or ""))
                content = str(payload.get("content") or "")
                checkpoint = self._create_checkpoint(target, "write_file")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
                res = {
                    "ok": True,
                    "path": str(target),
                    "bytes": len(content.encode("utf-8")),
                    "checkpoint_id": checkpoint.get("id"),
                }
                self._write_audit_event(action="write_file", payload=payload, result=res)
                return res

            if action == "append_file":
                target = self._resolve_workspace_path(str(payload.get("path") or ""))
                content = str(payload.get("content") or "")
                checkpoint = self._create_checkpoint(target, "append_file")
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("a", encoding="utf-8") as f:
                    f.write(content)
                res = {
                    "ok": True,
                    "path": str(target),
                    "bytes": len(content.encode("utf-8")),
                    "checkpoint_id": checkpoint.get("id"),
                }
                self._write_audit_event(action="append_file", payload=payload, result=res)
                return res

            if action == "execute_python":
                code = str(payload.get("code") or "")
                timeout = int(payload.get("timeout") or self.timeout)
                if not code.strip():
                    return {"ok": False, "error": "empty code"}
                tmp = Path(tempfile.gettempdir()) / f"ngram_py_{uuid.uuid4().hex[:8]}.py"
                tmp.write_text(code, encoding="utf-8")
                try:
                    p = _run_process(
                        [sys.executable, str(tmp)],
                        cwd=str(self.workspace_root),
                        timeout=max(1, timeout),
                    )
                    res = {
                        "ok": True,
                        "exit_code": int(p.returncode),
                        "stdout": (p.stdout or "")[:20000],
                        "stderr": (p.stderr or "")[:20000],
                    }
                    self._write_audit_event(action="execute_python", payload=payload, result=res)
                    return res
                finally:
                    try:
                        tmp.unlink(missing_ok=True)
                    except OSError:
                        pass

            if action == "execute_javascript":
                code = str(payload.get("code") or "")
                timeout = int(payload.get("timeout") or self.timeout)
                if not code.strip():
                    return {"ok": False, "error": "empty code"}
                node = shutil.which("node")
                if not node:
                    return {"ok": False, "error": "node not available in execution environment"}
                tmp = Path(tempfile.gettempdir()) / f"ngram_js_{uuid.uuid4().hex[:8]}.js"
                tmp.write_text(code, encoding="utf-8")
                try:
                    p = _run_process(
                        [node, str(tmp)],
                        cwd=str(self.workspace_root),
                        timeout=max(1, timeout),
                    )
                    res = {
                        "ok": True,
                        "exit_code": int(p.returncode),
                        "stdout": (p.stdout or "")[:20000],
                        "stderr": (p.stderr or "")[:20000],
                    }
                    self._write_audit_event(action="execute_javascript", payload=payload, result=res)
                    return res
                finally:
                    try:
                        tmp.unlink(missing_ok=True)
                    except OSError:
                        pass

            if action in (
                "browser_navigate",
                "browser_screenshot",
                "browser_click",
                "browser_type",
                "generate_image",
            ) or action in REMOTE_SESSION_RPC_ACTIONS:
                return {
                    "ok": False,
                    "error": (
                        f"{action} requires an RPC execution backend with browser/image or remote-session "
                        "capabilities. Set tools.execution.base_url."
                    ),
                }

            return {"ok": False, "error": f"unknown action: {action}"}
        except subprocess.TimeoutExpired as e:
            return {"ok": False, "error": f"timeout: {e}"}
        except Exception as e:
            log.warning("execution_local_failed", action=action, error=str(e))
            return {"ok": False, "error": str(e)}


HYBRID_OFF_RAILWAY_TOOL_BLOCK = (
    "Tool disabled: hybrid_railway on a host without RAILWAY_ENVIRONMENT (this Python process is not "
    "the Railway container). Run the worker on Railway, or set tools.execution.allow_local in entity YAML "
    "for local debugging. For file tools without touching this PC, set NGRAM_EXECUTION_RPC_URL (or "
    "tools.execution.base_url) to a hands host that implements the execution RPC. If you use Telegram and "
    "only wanted the cloud bot, stop any local `ngram run` that uses the same TELEGRAM_TOKEN."
)

RAILWAY_REQUIRED_TOOL_BLOCK = (
    "Tool disabled: execution is locked to Railway. This Python process is not running inside the "
    "Railway container, so it may not use local disk/shell/code on this machine. Run the worker on "
    "Railway, or set NGRAM_EXECUTION_RPC_URL (or tools.execution.base_url) to a remote execution "
    "host. To relax this for local debugging, unset NGRAM_EXECUTION_REQUIRE_RAILWAY (or set "
    "tools.execution.require_railway: false)."
)


def execution_rpc_url_configured(entity: Any) -> bool:
    """True when an HTTP execution backend URL is configured (env or entity tools.execution)."""
    tools_cfg = _effective_tools_config(entity)
    exec_cfg = tools_cfg.get("execution") if isinstance(tools_cfg.get("execution"), dict) else {}
    if not isinstance(exec_cfg, dict):
        exec_cfg = {}
    base = (
        (os.environ.get("NGRAM_EXECUTION_RPC_URL") or "").strip()
        or str(exec_cfg.get("base_url") or "").strip()
    )
    return bool(base.rstrip("/"))


def local_tool_block_message(entity: Any) -> str:
    """Why local disk/shell/code is blocked for this process."""
    if require_railway_execution(entity) and not (
        (os.environ.get("RAILWAY_ENVIRONMENT") or "").strip()
    ):
        return RAILWAY_REQUIRED_TOOL_BLOCK
    return HYBRID_OFF_RAILWAY_TOOL_BLOCK


def read_only_workspace_fs_allowed(entity: Any) -> bool:
    """True when read-only workspace file tools may run (local/Railway body or RPC URL set)."""
    return local_body_host_permitted(entity) or execution_rpc_url_configured(entity)


def local_body_host_permitted(entity: Any) -> bool:
    """True if this process may use local disk/shell for tools (not RPC-only hybrid on a dev PC)."""
    tools_cfg = _effective_tools_config(entity)
    exec_cfg = tools_cfg.get("execution") if isinstance(tools_cfg.get("execution"), dict) else {}
    if not isinstance(exec_cfg, dict):
        exec_cfg = {}
    mode = (entity.config.harness.deployment.mode or "local").strip().lower()
    allow_local = bool(exec_cfg.get("allow_local", False))
    on_railway = bool((os.environ.get("RAILWAY_ENVIRONMENT") or "").strip())
    if require_railway_execution(entity) and not on_railway:
        return False
    return (
        allow_local
        or mode == "local"
        or (mode in {"hybrid_railway", "cloud"} and on_railway)
    )


def self_update_host_permitted(entity: Any) -> bool:
    """
    True if this process may run git/pip self-update (narrower than arbitrary local execution).

    Hybrid home setups use ``hybrid_railway`` with inference remote but often keep a local clone;
    they should be able to update that install without ``tools.execution.allow_local``. Still
    blocked when ``require_railway_execution`` is set and this process is not on Railway (same
    safety boundary as other host-local tools).
    """
    if local_body_host_permitted(entity):
        return True
    mode = (entity.config.harness.deployment.mode or "local").strip().lower()
    if mode != "hybrid_railway":
        return False
    on_railway = bool((os.environ.get("RAILWAY_ENVIRONMENT") or "").strip())
    if require_railway_execution(entity) and not on_railway:
        return False
    return True


def self_update_tool_block_message(entity: Any) -> str:
    """Why ``update_ngram_from_upstream`` / Telegram ``/update`` cannot run here."""
    on_railway = bool((os.environ.get("RAILWAY_ENVIRONMENT") or "").strip())
    if require_railway_execution(entity) and not on_railway:
        return (
            RAILWAY_REQUIRED_TOOL_BLOCK
            + " Self-update from chat must target the Railway worker, or relax require_railway for a local runner."
        )
    return (
        "Self-update is only available when deployment is local or hybrid_railway on a host that may "
        "modify this Python install."
    )


def get_execution_client_for_entity(entity: Any) -> ExecutionRPCClient:
    """Build or return cached execution client (RPC and/or local disk under workspace)."""
    tools_cfg = _effective_tools_config(entity)
    exec_cfg = tools_cfg.get("execution") if isinstance(tools_cfg.get("execution"), dict) else {}
    if not isinstance(exec_cfg, dict):
        exec_cfg = {}
    base_url = (
        (os.environ.get("NGRAM_EXECUTION_RPC_URL") or "").strip()
        or str(exec_cfg.get("base_url") or "").strip()
    ).rstrip("/")
    token_env = str(exec_cfg.get("token_env") or "NGRAM_EXECUTION_RPC_TOKEN")
    token = (os.environ.get(token_env) or "").strip()
    timeout = float(exec_cfg.get("timeout") or 45.0)
    rpc_path = str(exec_cfg.get("rpc_path") or "/rpc")
    workspace_root = _configured_workspace_root(entity)
    allow_local_backend = local_body_host_permitted(entity)
    key = (
        base_url,
        rpc_path,
        token,
        timeout,
        allow_local_backend,
        workspace_root,
    )
    cached = _CLIENTS.get(key)
    if cached is not None:
        return cached

    client = ExecutionRPCClient(
        base_url=base_url,
        rpc_path=rpc_path,
        token=token,
        timeout=timeout,
        allow_local_backend=allow_local_backend,
        workspace_root=workspace_root,
    )
    _CLIENTS[key] = client
    return client


def get_execution_client() -> ExecutionRPCClient:
    return get_execution_client_for_entity(require_tool_runtime().entity)
