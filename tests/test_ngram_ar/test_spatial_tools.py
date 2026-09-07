from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from ngram.models import Input
from ngram.ngram_ar.spatial_tools import register_ngram_ar_spatial_tools
from ngram.ngram_ar.spatial_sessions import SpatialSession, SpatialSessions
from ngram.presence.tools.registry import ToolRegistry
from ngram.presence.tools.runtime import ToolRuntimeContext, reset_tool_runtime, set_tool_runtime
from ngram.utils.ollama_client import ToolCallSpec


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    register_ngram_ar_spatial_tools(registry)
    return registry


def test_spatial_surface_and_motion_tools_are_registered() -> None:
    registry = _registry()
    names = {name for name, _ in registry.list_tools()}
    assert {
        "ar_inspect_surface",
        "ar_move_to",
        "ar_speak",
        "ar_show_panel",
        "ar_terminal",
        "ar_open_browser",
        "ar_play_youtube",
        "ar_spawn_object",
        "ar_spawn_toy",
        "ar_spawn_text",
        "ar_remove_object",
        "ar_clear_objects",
        "ar_draw_annotation",
        "ar_set_environment",
        "ar_request_capture",
        "ar_generate_motion",
    } <= names
    gesture = registry.tool_discovery_detail("ar_gesture")
    assert gesture is not None
    assert "greet" in gesture["parameters"]["properties"]["gesture"]["enum"]


@pytest.mark.asyncio
async def test_surface_inspection_reports_live_api_contract() -> None:
    state = {}
    token = set_tool_runtime(
        ToolRuntimeContext(
            entity=object(),
            inp=Input(
                text="what can you do here?",
                person_id="person",
                person_name="Person",
                channel="ar-session",
                platform="ngram_ar",
                metadata={"ngram_ar_context": "surface mode: desktop"},
            ),
            state=state,
        )
    )
    try:
        result = await _registry().execute(
            ToolCallSpec(name="ar_inspect_surface", arguments={})
        )
    finally:
        reset_tool_runtime(token)

    assert '"surface": "ngram_ar"' in result
    assert "ar_show_panel" in result
    assert "surface mode: desktop" in result


@pytest.mark.asyncio
async def test_surface_tools_queue_protocol_actions() -> None:
    state = {}
    token = set_tool_runtime(
        ToolRuntimeContext(
            entity=object(),
            inp=Input(
                text="show it",
                person_id="person",
                person_name="Person",
                channel="ar-session",
                platform="ngram_ar",
            ),
            state=state,
        )
    )
    try:
        registry = _registry()
        await registry.execute(
            ToolCallSpec(
                name="ar_show_panel",
                arguments={"panel_id": "notes", "content": "Spatial notes"},
            )
        )
        await registry.execute(
            ToolCallSpec(
                name="ar_generate_motion",
                arguments={
                    "prompt": "take a careful step forward",
                    "duration_seconds": 2,
                    "root_target": "forward",
                },
            )
        )
    finally:
        reset_tool_runtime(token)

    actions = state["_ngram_ar_spatial_actions"]
    assert actions[0]["type"] == "action:show_panel"
    assert actions[0]["panel"]["id"] == "notes"
    assert "position" not in actions[0]["panel"]
    assert actions[1]["type"] == "action:generate_motion"
    assert actions[1]["constraints"] == {"rootTarget": "forward"}
    assert actions[1]["requestId"]


@pytest.mark.asyncio
async def test_shape_and_toy_tools_emit_real_physics_objects() -> None:
    state = {}
    token = set_tool_runtime(
        ToolRuntimeContext(
            entity=object(),
            inp=Input(
                text="put a ball on my left and a sphere on my right",
                person_id="person",
                person_name="Person",
                channel="ar-session",
                platform="ngram_ar",
            ),
            state=state,
        )
    )
    try:
        registry = _registry()
        await registry.execute(
            ToolCallSpec(
                name="ar_spawn_toy",
                arguments={
                    "object_id": "left-ball",
                    "toy_type": "bouncy_ball",
                    "position": "left",
                    "impulse": {"x": 0, "y": 1.5, "z": 0},
                },
            )
        )
        await registry.execute(
            ToolCallSpec(
                name="ar_spawn_object",
                arguments={
                    "object_id": "right-sphere",
                    "shape": "sphere",
                    "position": "right",
                    "physics": True,
                },
            )
        )
    finally:
        reset_tool_runtime(token)

    toy, primitive = state["_ngram_ar_spatial_actions"]
    assert toy["type"] == "action:spawn_toy"
    assert toy["toyType"] == "bouncy_ball"
    assert toy["position"] == "left"
    assert toy["impulse"] == {"x": 0.0, "y": 1.5, "z": 0.0}
    assert primitive["type"] == "action:spawn_object"
    assert primitive["shape"] == "sphere"
    assert primitive["position"] == "right"
    assert primitive["physics"] is True


def test_shape_tool_descriptions_disambiguate_geometry_from_text() -> None:
    tools = {
        row["name"]: row for row in _registry().tool_discovery_summaries()
    }
    assert "physical object" in tools["ar_spawn_text"]["description"]
    assert "spawn a real 3D geometric primitive" in tools["ar_spawn_object"]["description"]
    assert "balls" in tools["ar_spawn_toy"]["description"]


@pytest.mark.asyncio
async def test_youtube_tool_routes_watch_url_to_dedicated_player() -> None:
    state = {}
    token = set_tool_runtime(
        ToolRuntimeContext(
            entity=object(),
            inp=Input(
                text="play it",
                person_id="person",
                person_name="Person",
                channel="ar-session",
                platform="ngram_ar",
            ),
            state=state,
        )
    )
    try:
        result = await _registry().execute(
            ToolCallSpec(
                name="ar_play_youtube",
                arguments={
                    "video": "https://music.youtube.com/watch?v=dQw4w9WgXcQ&feature=share",
                    "title": "Test track",
                    "volume": 65,
                },
            )
        )
    finally:
        reset_tool_runtime(token)

    action = state["_ngram_ar_spatial_actions"][0]
    assert action["type"] == "action:play_youtube"
    assert action["videoId"] == "dQw4w9WgXcQ"
    assert action["title"] == "Test track"
    assert action["volume"] == 65
    assert action["startAt"] == 0
    assert action["sessionId"] == "ar-session"
    assert "queued" in result


@pytest.mark.asyncio
async def test_surface_tools_stream_when_platform_accepts_live_actions() -> None:
    delivered = []
    state = {}

    class LivePlatform:
        async def send_spatial_action(self, action):
            delivered.append(action)
            return True

    token = set_tool_runtime(
        ToolRuntimeContext(
            entity=object(),
            inp=Input(
                text="wave",
                person_id="person",
                person_name="Person",
                channel="ar-session",
                platform="ngram_ar",
            ),
            platform=LivePlatform(),
            state=state,
        )
    )
    try:
        await _registry().execute(
            ToolCallSpec(name="ar_gesture", arguments={"gesture": "wave"})
        )
    finally:
        reset_tool_runtime(token)

    assert delivered[0]["type"] == "action:gesture"
    assert delivered[0]["gesture"] == "wave"
    assert "_ngram_ar_spatial_actions" not in state


@pytest.mark.asyncio
async def test_spatial_tools_report_unavailable_without_connected_body() -> None:
    state = {}
    token = set_tool_runtime(
        ToolRuntimeContext(
            entity=object(),
            inp=Input("hello", "person", "Person", platform="telegram"),
            state=state,
        )
    )
    try:
        result = await _registry().execute(
            ToolCallSpec(name="ar_set_environment", arguments={"preset": "space"})
        )
    finally:
        reset_tool_runtime(token)

    assert "no connected Spatial session" in result
    assert "_ngram_ar_spatial_actions" not in state


@pytest.mark.parametrize("platform", ["telegram", "discord", "autonomous", "ngram_ar", None])
@pytest.mark.asyncio
async def test_any_surface_can_spawn_in_connected_body(platform) -> None:
    delivered = []
    sessions = SpatialSessions()

    async def send(actions):
        delivered.extend(actions)
        session.acknowledge({
            "completedActionId": actions[0]["actionId"], "status": "completed",
        })

    session = SpatialSession("desktop-body", send, lambda: {"mode": "desktop"})
    sessions.register(session)
    state = {}
    token = set_tool_runtime(ToolRuntimeContext(
        entity=SimpleNamespace(_ngram_ar_sessions=sessions),
        inp=Input("spawn a ball", "person", "Person", channel="telegram-chat", platform=platform)
        if platform else None,
        state=state,
    ))
    try:
        registry = _registry()
        inspection = json.loads(await registry.execute(
            ToolCallSpec(name="ar_inspect_surface", arguments={}),
        ))
        result = await registry.execute(ToolCallSpec(name="ar_spawn_toy", arguments={
            "object_id": "purple-ball", "toy_type": "bouncy_ball", "color": "#800080",
        }))
    finally:
        reset_tool_runtime(token)
    assert inspection["sessionId"] == "desktop-body"
    assert inspection["liveContext"] == {"mode": "desktop"}
    assert "spawn_toy completed" in result
    assert delivered[0]["sessionId"] == "desktop-body"
    assert delivered[0]["toyType"] == "bouncy_ball"
    assert delivered[0]["color"] == "#800080"
    assert "_ngram_ar_spatial_actions" not in state


@pytest.mark.asyncio
async def test_delivery_waits_for_matching_receipt_and_reports_failure() -> None:
    sent = asyncio.Event()
    action = {}

    async def send(actions):
        action.update(actions[0])
        sent.set()

    session = SpatialSession("desktop", send, dict)
    pending = asyncio.create_task(session.dispatch({"type": "action:request_capture"}))
    await sent.wait()
    session.acknowledge({"completedActionId": "wrong-action", "status": "completed"})
    assert not pending.done()
    session.acknowledge({
        "completedActionId": action["actionId"],
        "status": "failed", "error": "Vision is disabled by user",
    })
    result = await pending
    assert "request_capture failed" in result
    assert "Vision is disabled" in result
    assert not session.pending


@pytest.mark.asyncio
async def test_timeout_and_disconnect_never_queue_replay() -> None:
    delivered = []

    async def send(actions):
        delivered.extend(actions)

    session = SpatialSession("desktop", send, dict)
    result = await session.dispatch({"type": "action:spawn_toy"}, timeout=0.01)
    assert "execution unknown" in result
    assert not session.pending
    pending = asyncio.create_task(session.dispatch({"type": "action:spawn_toy"}))
    await asyncio.sleep(0)
    session.close()
    result = await pending
    assert "disconnected" in result
    assert "will not be replayed" in result
    assert "unavailable" in await session.dispatch({"type": "action:spawn_toy"})
    assert len(delivered) == 2


def test_sessions_are_entity_scoped_and_select_only_one_body() -> None:
    async def send(actions):
        pass

    sessions = SpatialSessions()
    other_entity = SpatialSessions()
    first = SpatialSession("first", send, dict)
    latest = SpatialSession("latest", send, dict)
    sessions.register(first)
    sessions.register(latest)
    assert sessions.select() is latest
    assert sessions.select("first") is first
    assert other_entity.select() is None
    replacement = SpatialSession("latest", send, dict)
    sessions.register(replacement)
    sessions.unregister(latest)
    assert sessions.select() is replacement
    sessions.unregister(replacement)
    assert sessions.select() is first
