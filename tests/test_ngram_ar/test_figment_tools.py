from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from ngram.models import Input
from ngram.ngram_ar.figment_tools import FIGMENT_TOOLS
from ngram.ngram_ar.spatial_sessions import SpatialSession, SpatialSessions
from ngram.ngram_ar.spatial_tools import register_ngram_ar_spatial_tools
from ngram.presence.tools.registry import ToolRegistry
from ngram.presence.tools.runtime import ToolRuntimeContext, reset_tool_runtime, set_tool_runtime
from ngram.utils.ollama_client import ToolCallSpec


@pytest.mark.parametrize("name,command", [(name, commands[0]) for name, (commands, _) in FIGMENT_TOOLS.items()])
@pytest.mark.asyncio
async def test_figment_bundle_routes_from_telegram_to_the_connected_renderer(name, command):
    registry = ToolRegistry()
    register_ngram_ar_spatial_tools(registry)
    sessions, delivered = SpatialSessions(), []

    async def send(actions):
        delivered.extend(actions)
        session.acknowledge({
            "completedActionId": actions[0]["actionId"], "status": "completed",
            "result": {"id": "lamp", "confirmed": True},
        })

    session = SpatialSession("body", send, dict)
    sessions.register(session)
    token = set_tool_runtime(ToolRuntimeContext(
        entity=SimpleNamespace(_ngram_ar_sessions=sessions),
        inp=Input("change my lamp", "person", "Person", platform="telegram"), state={},
    ))
    try:
        result = await registry.execute(ToolCallSpec(name=name, arguments={
            "command": command, "payload": {"id": "lamp", "command": "untrusted-override"},
        }))
        invalid = await registry.execute(ToolCallSpec(name=name, arguments={"command": "not-supported"}))
    finally:
        reset_tool_runtime(token)
    assert json.loads(result)["result"]["confirmed"] is True
    assert len(delivered) == 1
    assert delivered[0]["type"] == "action:world"
    assert delivered[0]["command"] == "figment"
    assert delivered[0]["payload"] == {"id": "lamp", "command": command}
    assert "invalid Figment command" in invalid


@pytest.mark.asyncio
async def test_figments_require_a_renderer_and_reject_large_model_payloads():
    registry = ToolRegistry()
    register_ngram_ar_spatial_tools(registry)
    token = set_tool_runtime(ToolRuntimeContext(entity=object(), inp=None, state={}))
    try:
        result = await registry.execute(ToolCallSpec(name="ar_figment", arguments={"command": "inspect"}))
        assert "no connected Spatial session" in result
        result = await registry.execute(ToolCallSpec(name="ar_figment_library", arguments={
            "command": "import", "payload": {"package": "x" * 2_000_001},
        }))
        assert "import packages by URL" in result
    finally:
        reset_tool_runtime(token)
