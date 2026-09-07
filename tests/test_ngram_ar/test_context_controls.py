import asyncio
from types import SimpleNamespace

import pytest
from aiohttp.test_utils import TestClient, TestServer

from ngram.cognition.context_status import context_reporter, report_context_status
from ngram.entity import Entity
from ngram.ngram_ar.bridge_server import _handle_shell_event, create_bridge_app


@pytest.mark.asyncio
@pytest.mark.parametrize("event", [
    {"type": "event:compact_context"},
    {"type": "event:user_speech", "text": "/compact", "isFinal": True},
])
async def test_manual_compact_bypasses_cognition_and_reports_progress(event):
    emitted = []
    lock = asyncio.Lock()

    async def compact(*, passes):
        assert lock.locked()
        assert passes == 1
        await report_context_status("compacting", automatic=False)
        await report_context_status("compacted", automatic=False, estimatedTokens=4000, inputBudgetTokens=256000)
        return {"compacted": True}

    async def emit(actions):
        emitted.extend(actions)

    entity = SimpleNamespace(_turn_lock=lock, compact_context_now=compact)
    assert await _handle_shell_event(entity, event["type"], event, "test", "test", "Test", emit_actions=emit) == []
    assert [action["phase"] for action in emitted] == ["compacting", "compacted"]
    assert all(action["type"] == "action:context_status" for action in emitted)
    assert context_reporter.get() is None


@pytest.mark.asyncio
async def test_stop_cancels_manual_compaction_without_starting_agent(monkeypatch):
    monkeypatch.delenv("NGRAM_AR_ENTITY_BRIDGE_TOKEN", raising=False)
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def compact(**_kwargs):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    entity = object.__new__(Entity)
    entity._turn_lock = asyncio.Lock()
    entity._turn_activity_sinks = []
    entity.compact_context_now = compact
    async with TestClient(TestServer(await create_bridge_app(entity))) as client:
        ws = await client.ws_connect("/")
        await ws.send_json({"type": "session.start", "sessionId": "test"})
        await ws.receive_json()
        await ws.send_json({"type": "session.event", "id": "compact", "event": {"type": "event:compact_context"}})
        await asyncio.wait_for(started.wait(), 1)
        await ws.send_json({"type": "session.event", "id": "stop", "event": {"type": "event:cancel_turn"}})
        async with asyncio.timeout(1):
            while (await ws.receive_json()).get("replyTo") != "stop":
                pass
        assert cancelled.is_set()
        assert not entity._turn_lock.locked()
        await ws.close()
