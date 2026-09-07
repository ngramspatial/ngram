from __future__ import annotations

import asyncio
import json
from types import MethodType, SimpleNamespace

import pytest
from aiohttp.test_utils import TestClient, TestServer

from ngram.entity import Entity
from ngram.inference.control import InferenceControl
from ngram.models import Input
from ngram.ngram_ar.spatial_tools import register_ngram_ar_spatial_tools
from ngram.presence.tools.registry import ToolRegistry
from ngram.presence.tools.runtime import ToolRuntimeContext, reset_tool_runtime, set_tool_runtime
from ngram.utils.ollama_client import ToolCallSpec
from ngram.ngram_ar.bridge_server import (
    _BufferedArPlatform,
    _check_token,
    _external_message_activity_actions,
    _handle_shell_event,
    _safe_tool_output,
    _update_spatial_context,
    bridge_person_id,
    bridge_person_name,
    create_bridge_app,
    health_handler,
)


@pytest.mark.asyncio
async def test_telegram_tool_reaches_ready_body_and_receives_ack_without_perceive(monkeypatch):
    monkeypatch.delenv("NGRAM_AR_ENTITY_BRIDGE_TOKEN", raising=False)
    entity = object.__new__(Entity)
    entity._turn_lock = asyncio.Lock()
    entity._turn_activity_sinks = []
    client = TestClient(TestServer(await create_bridge_app(entity)))
    await client.start_server()
    ws = await client.ws_connect("/")
    registry = ToolRegistry()
    register_ngram_ar_spatial_tools(registry)
    state = {}
    token = set_tool_runtime(ToolRuntimeContext(
        entity=entity,
        inp=Input("spawn a purple ball", "person", "Person", platform="telegram", channel="123"),
        state=state,
    ))
    try:
        await ws.send_json({"type": "session.start", "sessionId": "desktop-body"})
        assert (await ws.receive_json())["type"] == "session.ready"
        # Message API sockets alone are not bodies.
        assert entity._ngram_ar_sessions.select() is None
        await ws.send_json({"type": "session.event", "id": "ready", "event": {
            "type": "event:shell_ready", "shellName": "Rook",
            "spatialContext": {"surface": {"mode": "desktop"}},
        }})
        assert (await ws.receive_json())["replyTo"] == "ready"
        # Hold the cognition lock as a real Telegram turn does. The receipt
        # must resolve directly in the socket reader, without another model turn.
        async with entity._turn_lock:
            pending = asyncio.create_task(registry.execute(ToolCallSpec(
                name="ar_spawn_toy", arguments={
                    "object_id": "purple-ball", "toy_type": "bouncy_ball", "color": "#800080",
                },
            )))
            message = await asyncio.wait_for(ws.receive_json(), timeout=1)
            action = message["actions"][0]
            assert action["sessionId"] == "desktop-body"
            assert action["type"] == "action:spawn_toy"
            assert not pending.done()
            await ws.send_json({"type": "session.event", "id": "ack", "event": {
                "type": "event:action_completed", "completedActionId": action["actionId"],
                "status": "completed",
            }})
            assert "spawn_toy completed" in await asyncio.wait_for(pending, timeout=1)
            assert (await ws.receive_json())["replyTo"] == "ack"
        await ws.close()
        for _ in range(50):
            if entity._ngram_ar_sessions.select() is None:
                break
            await asyncio.sleep(0.01)
        result = await registry.execute(ToolCallSpec(
            name="ar_spawn_toy", arguments={"object_id": "ball", "toy_type": "ball"},
        ))
        assert "no connected Spatial session" in result
        assert "_ngram_ar_spatial_actions" not in state
        # A gateway-to-entity reconnect restores an already-open renderer
        # without needing a new browser connection or replaying old actions.
        ws = await client.ws_connect("/")
        await ws.send_json({
            "type": "session.start", "sessionId": "desktop-body",
            "surfaceReady": {"type": "event:shell_ready", "shellName": "Rook"},
        })
        assert (await ws.receive_json())["type"] == "session.ready"
        assert entity._ngram_ar_sessions.select().session_id == "desktop-body"
        assert not entity._ngram_ar_sessions.select().pending
    finally:
        reset_tool_runtime(token)
        await ws.close()
        await client.close()


def test_external_message_activity_drives_texting_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("NGRAM_AR_PERSON_ID", raising=False)
    started = _external_message_activity_actions(
        {
            "phase": "started",
            "platform": "telegram",
            "person_id": "123",
            "timestamp": 10,
        },
        "ar-session",
    )
    finished = _external_message_activity_actions(
        {
            "phase": "finished",
            "platform": "telegram",
            "person_id": "123",
            "timestamp": 20,
        },
        "ar-session",
    )

    assert started == [
        {
            "type": "action:set_agent_state",
            "state": "messaging",
            "message": "Replying on Telegram",
            "sessionId": "ar-session",
            "timestamp": 10,
        }
    ]
    assert finished[0]["state"] == "idle"


def test_external_message_activity_filters_same_ar_session_and_other_people(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NGRAM_AR_PERSON_ID", "owner")
    assert _external_message_activity_actions(
        {
            "phase": "started",
            "turn_kind": "message",
            "platform": "ngram_ar",
            "channel": "ar-session",
            "person_id": "owner",
        },
        "ar-session",
    ) == []
    cross_session = _external_message_activity_actions(
        {
            "phase": "started",
            "turn_kind": "message",
            "platform": "ngram_ar",
            "channel": "gateway-api-session",
            "person_id": "owner",
        },
        "ar-session",
    )
    assert cross_session[0]["state"] == "messaging"
    assert _external_message_activity_actions(
        {
            "phase": "started",
            "turn_kind": "camera",
            "platform": "ngram_ar",
            "channel": "another-spatial-session",
            "person_id": "owner",
        },
        "ar-session",
    ) == []
    assert _external_message_activity_actions(
        {"phase": "started", "platform": "discord", "person_id": "someone-else"},
        "ar-session",
    ) == []


def test_default_unpaired_person_does_not_disable_message_presence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NGRAM_AR_PERSON_ID", "ar_user")
    actions = _external_message_activity_actions(
        {"phase": "started", "platform": "telegram", "person_id": "123"},
        "ar-session",
    )
    assert actions[0]["state"] == "messaging"


@pytest.mark.asyncio
async def test_gateway_session_drives_texting_on_another_spatial_session(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Exercise the actual local-gateway -> Entity -> spatial websocket path."""
    monkeypatch.delenv("NGRAM_AR_ENTITY_BRIDGE_TOKEN", raising=False)
    monkeypatch.setenv("NGRAM_AR_PERSON_ID", "ar_user")

    entity = object.__new__(Entity)
    entity.inference_control = InferenceControl(tmp_path / 'paused')
    entity._turn_lock = asyncio.Lock()
    entity._turn_activity_sinks = []
    entity.current_platform = None
    model_started = asyncio.Event()
    release_model = asyncio.Event()

    async def blocked_perceive_once(
        self: Entity, _inp: object, **_kwargs: object
    ) -> tuple[str, bool]:
        model_started.set()
        await release_model.wait()
        return "reply", False

    entity._perceive_once = MethodType(blocked_perceive_once, entity)
    client = TestClient(TestServer(await create_bridge_app(entity)))
    await client.start_server()
    spatial_ws = await client.ws_connect("/")
    api_ws = await client.ws_connect("/")

    try:
        await spatial_ws.send_json({
            "type": "session.start",
            "sessionId": "spatial-session",
            "shellName": "Rook",
            "shellSlug": "rook",
        })
        await api_ws.send_json({
            "type": "session.start",
            "sessionId": "gateway-api-session",
            "shellName": "Rook",
            "shellSlug": "rook",
        })
        assert (await spatial_ws.receive_json())["type"] == "session.ready"
        assert (await api_ws.receive_json())["type"] == "session.ready"

        await api_ws.send_json({
            "type": "session.event",
            "id": "gateway-message",
            "event": {
                "type": "event:user_speech",
                "text": "hello from another surface",
                "isFinal": True,
            },
        })
        await asyncio.wait_for(model_started.wait(), timeout=1)

        started = await asyncio.wait_for(spatial_ws.receive_json(), timeout=1)
        assert started["type"] == "actions"
        assert started["actions"][0]["state"] == "messaging"

        release_model.set()
        finished = await asyncio.wait_for(spatial_ws.receive_json(), timeout=1)
        assert finished["actions"][0]["state"] == "idle"

        # The source may receive its normal thinking/idle telemetry, but it
        # must not classify its own turn as activity on another surface.
        source_states: list[str] = []
        while True:
            source_reply = await asyncio.wait_for(api_ws.receive_json(), timeout=1)
            assert source_reply["type"] == "actions"
            if source_reply.get("replyTo") == "gateway-message":
                break
            source_states.extend(
                str(action.get("state"))
                for action in source_reply.get("actions", [])
                if action.get("type") == "action:set_agent_state"
            )
        assert "messaging" not in source_states
    finally:
        release_model.set()
        await spatial_ws.close()
        await api_ws.close()
        await client.close()


@pytest.mark.asyncio
async def test_external_texting_prioritizes_then_restores_overlapping_spatial_turn(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.delenv("NGRAM_AR_ENTITY_BRIDGE_TOKEN", raising=False)
    monkeypatch.setenv("NGRAM_AR_PERSON_ID", "ar_user")

    entity = object.__new__(Entity)
    entity.inference_control = InferenceControl(tmp_path / 'paused')
    entity._turn_lock = asyncio.Lock()
    entity._turn_activity_sinks = []
    entity.current_platform = None
    local_model_started = asyncio.Event()
    release_local_model = asyncio.Event()

    async def blocked_perceive_once(
        self: Entity, _inp: object, **_kwargs: object
    ) -> tuple[str, bool]:
        local_model_started.set()
        await release_local_model.wait()
        return "spatial reply", False

    entity._perceive_once = MethodType(blocked_perceive_once, entity)
    client = TestClient(TestServer(await create_bridge_app(entity)))
    await client.start_server()
    spatial_ws = await client.ws_connect("/")
    external = Input(
        text="hello",
        person_id="owner",
        person_name="Owner",
        channel="telegram-chat",
        platform="telegram",
    )

    try:
        await spatial_ws.send_json({
            "type": "session.start",
            "sessionId": "spatial-session",
            "shellName": "Rook",
            "shellSlug": "rook",
        })
        assert (await spatial_ws.receive_json())["type"] == "session.ready"

        await entity._emit_turn_activity("started", external)
        started = await asyncio.wait_for(spatial_ws.receive_json(), timeout=1)
        assert started["actions"][0]["state"] == "messaging"

        await spatial_ws.send_json({
            "type": "session.event",
            "id": "spatial-message",
            "event": {
                "type": "event:user_speech",
                "text": "what are you doing?",
                "isFinal": True,
            },
        })
        await asyncio.wait_for(local_model_started.wait(), timeout=1)
        prioritized = await asyncio.wait_for(spatial_ws.receive_json(), timeout=1)
        assert prioritized["actions"][0]["state"] == "messaging"

        # The external turn ends while local spatial cognition is still live.
        # Restore thinking now, then allow the local turn's own idle to settle.
        await entity.finish_turn_activity(external)
        resumed = await asyncio.wait_for(spatial_ws.receive_json(), timeout=1)
        assert resumed["actions"][0]["state"] == "thinking"

        release_local_model.set()
        settled = await asyncio.wait_for(spatial_ws.receive_json(), timeout=1)
        response = await asyncio.wait_for(spatial_ws.receive_json(), timeout=1)
        assert settled["actions"][0]["state"] == "idle"
        assert response["replyTo"] == "spatial-message"
    finally:
        release_local_model.set()
        await entity.finish_turn_activity(external)
        await spatial_ws.close()
        await client.close()


@pytest.mark.asyncio
async def test_reconnected_spatial_session_replays_active_external_texting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("NGRAM_AR_ENTITY_BRIDGE_TOKEN", raising=False)
    monkeypatch.setenv("NGRAM_AR_PERSON_ID", "ar_user")

    entity = object.__new__(Entity)
    entity._turn_activity_sinks = []
    client = TestClient(TestServer(await create_bridge_app(entity)))
    await client.start_server()
    first_ws = await client.ws_connect("/")
    external = Input(
        text="hello",
        person_id="owner",
        person_name="Owner",
        channel="telegram-chat",
        platform="telegram",
    )

    try:
        await first_ws.send_json({
            "type": "session.start",
            "sessionId": "first-spatial-session",
            "shellName": "Rook",
            "shellSlug": "rook",
        })
        assert (await first_ws.receive_json())["type"] == "session.ready"

        await entity._emit_turn_activity("started", external)
        started = await asyncio.wait_for(first_ws.receive_json(), timeout=1)
        assert started["actions"][0]["state"] == "messaging"
        await first_ws.close()

        second_ws = await client.ws_connect("/")
        await second_ws.send_json({
            "type": "session.start",
            "sessionId": "second-spatial-session",
            "shellName": "Rook",
            "shellSlug": "rook",
        })
        assert (await second_ws.receive_json())["type"] == "session.ready"
        replayed = await asyncio.wait_for(second_ws.receive_json(), timeout=1)
        assert replayed["actions"][0]["state"] == "messaging"

        await entity.finish_turn_activity(external)
        finished = await asyncio.wait_for(second_ws.receive_json(), timeout=1)
        assert finished["actions"][0]["state"] == "idle"
        await second_ws.close()
    finally:
        await entity.finish_turn_activity(external)
        if not first_ws.closed:
            await first_ws.close()
        await client.close()


def _request(*, authorization: str = "", token: str = "") -> SimpleNamespace:
    return SimpleNamespace(
        headers={"Authorization": authorization} if authorization else {},
        query={"token": token} if token else {},
    )


def test_bridge_allows_requests_when_auth_is_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NGRAM_AR_ENTITY_BRIDGE_TOKEN", raising=False)
    assert _check_token(_request())


def test_bridge_accepts_bearer_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NGRAM_AR_ENTITY_BRIDGE_TOKEN", "rook-secret")
    assert _check_token(_request(authorization="Bearer rook-secret"))
    assert _check_token(_request(authorization="bearer rook-secret"))
    assert not _check_token(_request(authorization="Bearer wrong"))


def test_bridge_retains_query_token_compatibility(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NGRAM_AR_ENTITY_BRIDGE_TOKEN", "rook-secret")
    assert _check_token(_request(token="rook-secret"))
    assert not _check_token(_request(token="wrong"))


def test_bridge_uses_configured_cross_surface_person(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NGRAM_AR_PERSON_ID", "123456789")
    monkeypatch.setenv("NGRAM_AR_PERSON_NAME", "Operator")
    assert bridge_person_id() == "123456789"
    assert bridge_person_name() == "Operator"


def test_bridge_person_defaults_are_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NGRAM_AR_PERSON_ID", raising=False)
    monkeypatch.delenv("NGRAM_AR_PERSON_NAME", raising=False)
    assert bridge_person_id() == "ar_user"
    assert bridge_person_name() == "You"


@pytest.mark.asyncio
async def test_health_response_contains_no_entity_details() -> None:
    response = await health_handler(_request())
    assert response.status == 200
    assert json.loads(response.text) == {
        "ok": True,
        "service": "ngram-entity-bridge",
    }


@pytest.mark.asyncio
async def test_user_speech_routes_harness_say_to_ar_speech_action() -> None:
    class FakeEntity:
        current_platform = None

        async def perceive(self, inp, *, reply_platform, spatial_actions_out):
            spatial_actions_out.append(
                {
                    "type": "action:gesture",
                    "gesture": "wave",
                    "sessionId": inp.channel,
                    "timestamp": 1,
                }
            )
            await reply_platform.send_message(inp.channel, "I remember you.")
            return "", False

    actions = await _handle_shell_event(
        FakeEntity(),
        "event:user_speech",
        {"text": "Do you remember me?", "isFinal": True},
        "ar-session",
        "rook",
        "Rook",
    )

    assert [action["type"] for action in actions] == [
        "action:gesture",
        "action:speak",
    ]
    assert actions[1]["text"] == "I remember you."


@pytest.mark.asyncio
async def test_user_speech_restores_previous_entity_platform() -> None:
    previous_platform = object()

    class FakeEntity:
        current_platform = previous_platform

        async def perceive(self, inp, *, reply_platform, spatial_actions_out):
            self.current_platform = reply_platform
            await reply_platform.send_message(inp.channel, "Here.")
            return "", False

    entity = FakeEntity()
    await _handle_shell_event(
        entity,
        "event:user_speech",
        {"text": "Hello", "isFinal": True},
        "ar-session",
        "rook",
        "Rook",
    )

    assert entity.current_platform is previous_platform


@pytest.mark.asyncio
async def test_user_speech_injects_live_spatial_context() -> None:
    seen = {}

    class FakeEntity:
        current_platform = None

        async def perceive(self, inp, *, reply_platform, spatial_actions_out):
            seen.update(inp.metadata)
            return "I can see the spatial state.", False

    await _handle_shell_event(
        FakeEntity(),
        "event:user_speech",
        {"text": "Where am I?", "isFinal": True},
        "ar-session",
        "rook",
        "Rook",
        spatial_context={
            "version": "1.0",
            "surface": {"mode": "ar"},
            "proximity": {"distanceMeters": 1.25, "approaching": True},
        },
    )

    context = seen["ngram_ar_context"]
    assert "Live spatial state" in context
    assert '"mode":"ar"' in context
    assert '"distanceMeters":1.25' in context


@pytest.mark.asyncio
async def test_ar_platform_streams_safe_tool_telemetry() -> None:
    batches = []

    async def emit(actions):
        batches.append(actions)

    platform = _BufferedArPlatform("ar-session", emit)
    await platform.send_tool_activity("checking the workspace")
    await platform.send_tool_result("read_file", "token=super-secret\nready")

    assert batches[0][0]["state"] == "tool_running"
    assert batches[0][1]["type"] == "action:terminal_output"
    assert batches[1][0]["tool"] == "read_file"
    assert "super-secret" not in batches[1][0]["output"]
    assert "[redacted]" in batches[1][0]["output"]


@pytest.mark.asyncio
async def test_ar_platform_streams_speech_before_turn_completion() -> None:
    batches = []

    async def emit(actions):
        batches.append(actions)

    platform = _BufferedArPlatform("ar-session", emit)
    await platform.send_message("ar-session", "I am here.")

    assert batches[0][0]["type"] == "action:speak"
    assert batches[0][0]["text"] == "I am here."
    assert platform.actions == []
    assert platform.sent_live_speech is True


@pytest.mark.asyncio
async def test_live_speech_is_not_duplicated_in_final_action_batch() -> None:
    batches = []

    async def emit(actions):
        batches.append(actions)

    class FakeEntity:
        current_platform = None

        async def perceive(self, inp, *, reply_platform, spatial_actions_out):
            await reply_platform.send_message(inp.channel, "Already delivered.")
            return "", False

    actions = await _handle_shell_event(
        FakeEntity(),
        "event:user_speech",
        {"text": "Hello", "isFinal": True},
        "ar-session",
        "rook",
        "Rook",
        emit_actions=emit,
    )

    assert any(batch[0]["type"] == "action:speak" for batch in batches)
    assert actions == []


def test_spatial_context_prefers_complete_embedded_snapshot() -> None:
    result = _update_spatial_context(
        {"version": "1.0", "proximity": {"distanceMeters": 5}},
        {
            "type": "event:user_speech",
            "spatialContext": {
                "version": "1.0",
                "surface": {"mode": "desktop"},
                "scene": {"anchorCount": 0},
            },
        },
    )
    assert result["surface"]["mode"] == "desktop"
    assert "proximity" not in result


def test_safe_tool_output_is_bounded_and_redacted() -> None:
    result = _safe_tool_output("Authorization: Bearer abc123 password=hunter2 " + "x" * 5000)
    assert "abc123" not in result
    assert "hunter2" not in result
    assert result.endswith("[truncated]")


@pytest.mark.asyncio
async def test_camera_frame_reaches_entity_as_image_turn() -> None:
    seen = {}

    class FakeEntity:
        current_platform = None

        async def perceive(self, inp, *, reply_platform, spatial_actions_out):
            seen["input"] = inp
            return "I can see it.", False

    actions = await _handle_shell_event(
        FakeEntity(),
        "event:camera_frame",
        {
            "image": "data:image/jpeg;base64,QUJDRA==",
            "prompt": "What is in front of me?",
        },
        "ar-session",
        "rook",
        "Rook",
        spatial_context={"version": "1.0", "surface": {"mode": "ar"}},
    )

    inp = seen["input"]
    assert inp.text == "What is in front of me?"
    assert inp.images == [{"base64": "QUJDRA==", "mime": "image/jpeg"}]
    assert inp.metadata["spatial_camera_capture"] is True
    assert actions[0]["type"] == "action:speak"


@pytest.mark.asyncio
async def test_panel_interaction_routes_back_to_same_entity() -> None:
    seen = {}

    class FakeEntity:
        current_platform = None

        async def perceive(self, inp, *, reply_platform, spatial_actions_out):
            seen["input"] = inp
            return "Done.", False

    await _handle_shell_event(
        FakeEntity(),
        "event:panel_interaction",
        {
            "panelId": "task-panel",
            "action": "complete",
            "data": {"item": "first"},
        },
        "ar-session",
        "rook",
        "Rook",
    )

    assert "panel=task-panel" in seen["input"].text
    assert seen["input"].metadata["panel_interaction"] is True
