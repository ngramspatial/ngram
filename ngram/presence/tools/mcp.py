"""MCP stdio clients: discover tools and expose them through the entity's ToolRegistry."""

from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any, Callable, Awaitable

import structlog

from ngram.presence.tools.registry import ToolRegistry

log = structlog.get_logger("ngram.presence.tools.mcp")

try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from mcp.types import TextContent

    _MCP_AVAILABLE = True
except ImportError:
    ClientSession = Any  # type: ignore
    StdioServerParameters = Any  # type: ignore
    stdio_client = Any  # type: ignore
    TextContent = Any  # type: ignore
    _MCP_AVAILABLE = False


def _sanitize_registry_name(server_key: str, tool_name: str) -> str:
    raw = f"mcp_{server_key}_{tool_name}"
    s = re.sub(r"[^a-zA-Z0-9_]", "_", raw)
    if s and s[0].isdigit():
        s = "mcp_" + s
    return s[:128]


def _tool_result_to_text(result: Any) -> str:
    parts: list[str] = []
    for block in getattr(result, "content", None) or []:
        if isinstance(block, TextContent):
            parts.append(block.text)
        else:
            t = getattr(block, "type", "")
            if t == "text" and hasattr(block, "text"):
                parts.append(str(block.text))
            else:
                parts.append(str(block))
    structured = getattr(result, "structuredContent", None) or getattr(
        result, "structured_content", None
    )
    if structured is not None:
        parts.append(json.dumps(structured, ensure_ascii=False))
    return "\n".join(p for p in parts if p).strip() or "(empty tool result)"


def _tool_schema(tool: Any) -> dict[str, Any]:
    schema = getattr(tool, "inputSchema", None) or getattr(tool, "input_schema", None)
    if schema is None and hasattr(tool, "model_dump"):
        d = tool.model_dump(mode="json")
        schema = d.get("inputSchema") or d.get("input_schema") or {}
    if isinstance(schema, dict):
        return schema
    return {"type": "object", "properties": {}}


def _tool_description(tool: Any) -> str:
    d = getattr(tool, "description", None) or ""
    return str(d) if d else "MCP tool"


class MCPWorker:
    """One stdio MCP server: maintains a session and serializes tool calls on a queue."""

    def __init__(self, label: str, command: str, args: list[str], env: dict[str, str] | None) -> None:
        self.label = label
        self.command = command
        self.args = list(args or [])
        self.extra_env = dict(env or {})
        self._run = False
        self._task: asyncio.Task[None] | None = None
        self._q: asyncio.Queue[Any] = asyncio.Queue()
        self._tool_defs: list[Any] | None = None
        self._connected = asyncio.Event()

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._run = True
        self._connected.clear()
        self._tool_defs = None
        self._task = asyncio.create_task(self._connection_loop(), name=f"mcp-{self.label}")

    async def stop(self) -> None:
        self._run = False
        try:
            self._q.put_nowait(None)
        except asyncio.QueueFull:
            pass
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=8.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._task.cancel()
                try:
                    await self._task
                except asyncio.CancelledError:
                    pass
            self._task = None

    async def wait_ready(self, timeout: float = 45.0) -> bool:
        try:
            await asyncio.wait_for(self._connected.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            return False
        return self._tool_defs is not None

    async def invoke(self, mcp_tool_name: str, arguments: dict[str, Any]) -> str:
        fut: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        await self._q.put(("call", fut, mcp_tool_name, arguments))
        return await fut

    async def _connection_loop(self) -> None:
        while self._run:
            merged_env = {**os.environ, **self.extra_env}
            params = StdioServerParameters(
                command=self.command,
                args=self.args,
                env=merged_env,
            )
            try:
                async with stdio_client(params) as streams:
                    read, write = streams
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        listed = await session.list_tools()
                        self._tool_defs = list(listed.tools)
                        self._connected.set()
                        log.info(
                            "mcp_connected",
                            module="presence.tools",
                            server=self.label,
                            tool_count=len(self._tool_defs),
                        )
                        while self._run:
                            try:
                                item = await asyncio.wait_for(self._q.get(), timeout=1.0)
                            except asyncio.TimeoutError:
                                continue
                            if item is None:
                                return
                            kind = item[0]
                            if kind == "call":
                                _, fut, tool_name, arguments = item
                                try:
                                    res = await session.call_tool(
                                        tool_name,
                                        arguments=arguments or {},
                                    )
                                    text = _tool_result_to_text(res)
                                    if not fut.done():
                                        fut.set_result(text)
                                except Exception as e:
                                    log.warning(
                                        "mcp_tool_call_failed",
                                        module="presence.tools",
                                        server=self.label,
                                        tool=tool_name,
                                        error=str(e),
                                    )
                                    if not fut.done():
                                        fut.set_result(json.dumps({"error": str(e)}))
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.warning(
                    "mcp_session_lost",
                    module="presence.tools",
                    server=self.label,
                    error=str(e),
                )
                self._connected.clear()
                self._tool_defs = None
                await asyncio.sleep(2.0)
        log.info("mcp_worker_stopped", module="presence.tools", server=self.label)


class MCPHub:
    """Loads ``mcp_servers`` from entity YAML, connects via stdio, registers dynamic tools."""

    def __init__(self) -> None:
        self._workers: dict[str, MCPWorker] = {}
        self._registered_keys: set[str] = set()

    @staticmethod
    def _normalize_config(raw: dict[str, Any]) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for key, cfg in raw.items():
            if not isinstance(cfg, dict):
                continue
            cmd = cfg.get("command")
            if not cmd:
                continue
            out[str(key)] = {
                "command": str(cmd),
                "args": [str(a) for a in (cfg.get("args") or [])],
                "env": {str(k): str(v) for k, v in (cfg.get("env") or {}).items()},
            }
        return out

    async def refresh(
        self,
        registry: ToolRegistry,
        mcp_servers: dict[str, Any] | None,
    ) -> None:
        if not _MCP_AVAILABLE:
            if mcp_servers:
                log.warning("mcp_package_missing", module="presence.tools")
            return

        spec = self._normalize_config(mcp_servers or {})
        # Drop removed servers
        for label in list(self._workers.keys()):
            if label not in spec:
                await self._workers[label].stop()
                del self._workers[label]

        for label, cfg in spec.items():
            w = self._workers.get(label)
            if w is None:
                w = MCPWorker(label, cfg["command"], cfg["args"], cfg["env"])
                self._workers[label] = w
                await w.start()
            else:
                if (
                    w.command != cfg["command"]
                    or w.args != cfg["args"]
                    or w.extra_env != cfg["env"]
                ):
                    await w.stop()
                    w = MCPWorker(label, cfg["command"], cfg["args"], cfg["env"])
                    self._workers[label] = w
                    await w.start()

        await self._register_tools(registry)

    async def _register_tools(self, registry: ToolRegistry) -> None:
        for key in list(self._registered_keys):
            registry.unregister(key)
        self._registered_keys.clear()

        for label, worker in self._workers.items():
            ok = await worker.wait_ready(timeout=35.0)
            if not ok or not worker._tool_defs:
                continue
            for t in worker._tool_defs:
                mcp_name = getattr(t, "name", "") or ""
                if not mcp_name:
                    continue
                reg_name = _sanitize_registry_name(label, mcp_name)
                desc = f"[MCP:{label}] {_tool_description(t)}"

                def bind(
                    w: MCPWorker,
                    orig: str,
                ) -> Callable[..., Awaitable[str]]:
                    async def _fn(**kwargs: Any) -> str:
                        return await w.invoke(orig, kwargs)

                    return _fn

                schema = _tool_schema(t)
                registry.register_fn(
                    reg_name,
                    desc,
                    bind(worker, mcp_name),
                    parameters_schema=schema if isinstance(schema, dict) else None,
                )
                self._registered_keys.add(reg_name)

        log.info(
            "mcp_tools_registered",
            module="presence.tools",
            names=sorted(self._registered_keys),
        )

    async def heartbeat(self, registry: ToolRegistry, mcp_servers: dict[str, Any] | None) -> None:
        """Reconnect stale workers and re-register tools."""
        await self.refresh(registry, mcp_servers)

    async def shutdown(self) -> None:
        for w in self._workers.values():
            await w.stop()
        self._workers.clear()
