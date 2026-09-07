import asyncio
from types import MethodType

import pytest
from aiohttp.test_utils import TestClient, TestServer

from ngram.entity import Entity
from ngram.ngram_ar import bridge_server


@pytest.mark.parametrize("text,expected", [
    ("Ok, stop. End turn.", True), ("stop", True), ("Please stop now!", True),
    ("cancel", True), ("end the turn", True), ("don't stop", False),
    ("stop the music", False), ("how do I stop?", False),
])
def test_stop_commands_are_control_events(text, expected):
    event = {"type": "event:user_speech", "text": text, "isFinal": True}
    assert bridge_server._is_stop_event(event) is expected
    assert not bridge_server._is_stop_event({**event, "isFinal": False})


async def receive_until(ws, predicate):
    async with asyncio.timeout(2):
        while True:
            message = await ws.receive_json()
            if predicate(message):
                return message


@pytest.mark.asyncio
@pytest.mark.parametrize("control", ["text", "button", "other_session", "timeout", "disconnect", "brain_switch"])
async def test_inflight_turn_is_interruptible_and_releases_entity_lock(monkeypatch, control):
    monkeypatch.delenv("NGRAM_AR_ENTITY_BRIDGE_TOKEN", raising=False)
    if control == "timeout":
        monkeypatch.setattr(bridge_server, "_AR_TURN_TIMEOUT_SECONDS", 0.1)
    entity = object.__new__(Entity)
    entity._turn_lock = asyncio.Lock()
    entity._turn_activity_sinks = []
    entity.current_platform = None
    started = asyncio.Event()
    cancelled = asyncio.Event()
    calls = []

    async def perceive(self, inp, **_kwargs):
        calls.append(inp.text)
        if len(calls) == 1:
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        return "fresh reply", False

    async def configure(_config):
        assert cancelled.is_set()
        assert not entity._turn_lock.locked()
        return {"provider": "remote_gateway"}

    entity._perceive_once = MethodType(perceive, entity)
    entity.configure_inference = configure
    async with TestClient(TestServer(await bridge_server.create_bridge_app(entity))) as client:
        ws = await client.ws_connect("/")

        async def start(socket, session):
            await socket.send_json({"type": "session.start", "sessionId": session})
            assert (await socket.receive_json())["type"] == "session.ready"

        async def send(socket, ident, event):
            await socket.send_json({"type": "session.event", "id": ident, "event": event})

        await start(ws, "first")
        await send(ws, "running", {"type": "event:user_speech", "text": "work", "isFinal": True})
        await asyncio.wait_for(started.wait(), 1)
        # A long inference must not block the socket reader or its heartbeat.
        await ws.send_json({"type": "ping"})
        await receive_until(ws, lambda message: message["type"] == "pong")
        control_ws = ws
        if control in {"text", "button", "other_session", "brain_switch"}:
            await send(ws, "queued", {"type": "event:user_speech", "text": "queued work", "isFinal": True})
            if control == "other_session":
                control_ws = await client.ws_connect("/")
                await start(control_ws, "second")
            if control == "brain_switch":
                await control_ws.send_json({
                    "type": "brain.configure", "id": "switch", "interrupt": True,
                    "config": {"mode": "private", "provider": "remote_gateway"},
                })
                response = await receive_until(control_ws, lambda message: message["type"] == "brain.configured")
                assert response["ok"] is True
            else:
                event = {"type": "event:cancel_turn"} if control != "text" else {
                    "type": "event:user_speech", "text": "Ok, stop. End turn.", "isFinal": True,
                }
                await send(control_ws, "stop", event)
                await receive_until(control_ws, lambda message: message.get("replyTo") == "stop")
        elif control == "disconnect":
            await ws.close()
            control_ws = await client.ws_connect("/")
            await start(control_ws, "replacement")
        else:
            await send(ws, "queued", {"type": "event:user_speech", "text": "queued work", "isFinal": True})
            response = await receive_until(ws, lambda message: any(
                action.get("type") == "action:turn_cancelled" and action.get("reason") == "timeout"
                for action in message.get("actions", [])
            ))
            assert response["actions"][0]["reason"] == "timeout"
        await asyncio.wait_for(cancelled.wait(), 1)
        assert not entity._turn_lock.locked()
        assert calls == ["work"]  # stop and cancelled queued input never reach inference
        await send(control_ws, "fresh", {"type": "event:user_speech", "text": "new request", "isFinal": True})
        await receive_until(control_ws, lambda message: message.get("replyTo") == "fresh")
        assert calls == ["work", "new request"]
        await control_ws.close()
        await ws.close()
