from __future__ import annotations

import base64
import json

import pytest

from ngram.models import Input
from ngram.presence.tools import remote_session
from ngram.presence.tools.runtime import ToolRuntimeContext, reset_tool_runtime, set_tool_runtime


class _FakeClient:
    def __init__(self, response):
        self.response = response
        self.calls: list[tuple[str, dict]] = []

    async def call(self, action: str, payload: dict):
        self.calls.append((action, payload))
        return dict(self.response)


class _FakePlatform:
    def __init__(self) -> None:
        self.session: dict | None = None
        self.card_updates: list[tuple[str, bytes | None, str, dict | None]] = []
        self.cleared = 0

    def get_active_remote_session(self, channel: str):
        _ = channel
        return self.session

    async def set_active_remote_session(self, channel: str, session: dict | None) -> None:
        _ = channel
        self.session = dict(session) if isinstance(session, dict) else None

    async def upsert_remote_session_card(
        self,
        channel: str,
        *,
        image_bytes: bytes | None,
        caption: str,
        session: dict | None = None,
    ) -> None:
        self.card_updates.append((channel, image_bytes, caption, session))

    async def clear_remote_session_card(self, channel: str) -> None:
        _ = channel
        self.cleared += 1


def _with_runtime(inp: Input, platform: _FakePlatform):
    return set_tool_runtime(ToolRuntimeContext(entity=object(), inp=inp, platform=platform, state={}))


@pytest.mark.asyncio
async def test_desktop_session_view_requires_active_session() -> None:
    platform = _FakePlatform()
    inp = Input(text="look", person_id="1", person_name="u", channel="5", platform="telegram", metadata={})
    tok = _with_runtime(inp, platform)
    try:
        out = json.loads(await remote_session.desktop_session_view())
    finally:
        reset_tool_runtime(tok)
    assert out["ok"] is False
    assert "Use /session_start" in out["error"]


@pytest.mark.asyncio
async def test_desktop_session_view_uses_metadata_session_and_updates_card(monkeypatch) -> None:
    image_bytes = b"jpeg-bytes"
    client = _FakeClient(
        {
            "ok": True,
            "session_id": "sess_1",
            "summary": "Watching Firefox",
            "status": "running",
            "image_base64": base64.b64encode(image_bytes).decode("ascii"),
        }
    )
    monkeypatch.setattr(remote_session, "get_execution_client", lambda: client)
    platform = _FakePlatform()
    inp = Input(
        text="refresh the screen",
        person_id="1",
        person_name="u",
        channel="5",
        platform="telegram",
        metadata={"desktop_session": {"session_id": "sess_1", "summary": "Watching Firefox"}},
    )
    tok = _with_runtime(inp, platform)
    try:
        out = json.loads(await remote_session.desktop_session_view())
    finally:
        reset_tool_runtime(tok)
    assert out["ok"] is True
    assert client.calls == [("desktop_session_capture", {"session_id": "sess_1", "format": "jpeg"})]
    assert platform.session is not None
    assert platform.session["session_id"] == "sess_1"
    assert platform.card_updates
    assert platform.card_updates[0][1] == image_bytes


@pytest.mark.asyncio
async def test_desktop_session_stop_clears_platform_session(monkeypatch) -> None:
    client = _FakeClient({"ok": True, "session_id": "sess_1", "status": "stopped"})
    monkeypatch.setattr(remote_session, "get_execution_client", lambda: client)
    platform = _FakePlatform()
    platform.session = {"session_id": "sess_1"}
    inp = Input(text="stop it", person_id="1", person_name="u", channel="5", platform="telegram", metadata={})
    tok = _with_runtime(inp, platform)
    try:
        out = json.loads(await remote_session.desktop_session_stop())
    finally:
        reset_tool_runtime(tok)
    assert out["ok"] is True
    assert client.calls == [("desktop_session_stop", {"session_id": "sess_1"})]
    assert platform.session is None
    assert platform.cleared == 1
