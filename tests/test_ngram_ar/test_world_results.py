"""The renderer's world data must survive the live-body tool receipt path."""

import json
from types import SimpleNamespace

import pytest

from ngram.ngram_ar.spatial_sessions import SpatialSession, SpatialSessions
from ngram.ngram_ar.spatial_tools import register_ngram_ar_spatial_tools
from ngram.presence.tools.registry import ToolRegistry
from ngram.presence.tools.runtime import ToolRuntimeContext, reset_tool_runtime, set_tool_runtime
from ngram.inference.types import ToolCallSpec


@pytest.mark.asyncio
async def test_world_observation_returns_structured_renderer_data_without_a_new_turn():
    sessions = SpatialSessions()
    sent = []
    observed = {"revision": 12, "entities": [{"id": "pendulum", "heldBy": "xr:left"}]}

    async def send(actions):
        sent.extend(actions)
        session.acknowledge({
            "completedActionId": actions[0]["actionId"], "status": "completed", "result": observed,
        })

    session = SpatialSession("body", send, dict)
    sessions.register(session)
    token = set_tool_runtime(ToolRuntimeContext(
        entity=SimpleNamespace(_ngram_ar_sessions=sessions), inp=None, state={},
    ))
    try:
        registry = ToolRegistry()
        register_ngram_ar_spatial_tools(registry)
        result = json.loads(await registry.execute(ToolCallSpec(
            name="ar_world", arguments={"command": "observe", "payload": {"ids": ["pendulum"]}},
        )))
    finally:
        reset_tool_runtime(token)
    assert result == {"status": "completed", "sessionId": "body", "result": observed}
    assert len(sent) == 1
    assert sent[0]["type"] == "action:world"
    assert sent[0]["payload"] == {"ids": ["pendulum"]}
    assert not session.pending
