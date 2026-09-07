import json
import sys
import types
from pathlib import Path

import pytest

from ngram.models import Input
from ngram.presence.tools.runtime import ToolRuntimeContext, reset_tool_runtime, set_tool_runtime
from ngram.presence.tools.voice import get_tts_voice, list_tts_voices, set_tts_voice, speak


class _FakeCommunicate:
    calls: list[tuple[str, str]] = []

    def __init__(self, *, text: str, voice: str) -> None:
        self.text = text
        self.voice = voice
        self.__class__.calls.append((text, voice))

    async def save(self, path: str) -> None:
        Path(path).write_bytes(b"mp3")


class _DummyPlatform:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    async def send_audio(self, channel: str, path: str) -> bool:
        self.sent.append((channel, path))
        return True


class _FailingPlatform:
    async def send_audio(self, channel: str, path: str) -> bool:
        return False


class _HarnessCfg:
    def __init__(self) -> None:
        self.tools = {"voice": {"voice_id": "en-US-JennyNeural"}}


class _EntityCfg:
    def __init__(self) -> None:
        self.harness = _HarnessCfg()
        self.raw: dict[str, object] = {}


class _DummyEntity:
    def __init__(self) -> None:
        self.config = _EntityCfg()


async def _fake_list_voices():
    return [
        {
            "ShortName": "en-US-JennyNeural",
            "FriendlyName": "Microsoft Jenny Online (Natural) - English (United States)",
            "Locale": "en-US",
            "Gender": "Female",
        },
        {
            "ShortName": "es-MX-DaliaNeural",
            "FriendlyName": "Microsoft Dalia Online (Natural) - Spanish (Mexico)",
            "Locale": "es-MX",
            "Gender": "Female",
        },
    ]


@pytest.mark.asyncio
async def test_speak_does_not_return_local_path() -> None:
    _FakeCommunicate.calls.clear()
    fake_mod = types.SimpleNamespace(Communicate=_FakeCommunicate, list_voices=_fake_list_voices)
    prev = sys.modules.get("edge_tts")
    sys.modules["edge_tts"] = fake_mod

    platform = _DummyPlatform()
    inp = Input(
        text="speak this",
        person_id="111",
        person_name="Alice",
        channel="chat-1",
        platform="telegram",
    )
    ctx = ToolRuntimeContext(entity=_DummyEntity(), inp=inp, platform=platform, state={})
    tok = set_tool_runtime(ctx)
    try:
        out = await speak("yo")
        data = json.loads(out)
        assert data["ok"] is True
        assert data["delivered"] is True
        assert "path" not in data
        assert platform.sent and platform.sent[0][0] == "chat-1"
        assert ctx.state.get("voice_sent") is True
        assert _FakeCommunicate.calls[-1][1] == "en-US-JennyNeural"
    finally:
        reset_tool_runtime(tok)
        if prev is None:
            del sys.modules["edge_tts"]
        else:
            sys.modules["edge_tts"] = prev


@pytest.mark.asyncio
async def test_speak_returns_error_when_delivery_fails() -> None:
    fake_mod = types.SimpleNamespace(Communicate=_FakeCommunicate, list_voices=_fake_list_voices)
    prev = sys.modules.get("edge_tts")
    sys.modules["edge_tts"] = fake_mod

    inp = Input(
        text="speak this",
        person_id="111",
        person_name="Alice",
        channel="chat-1",
        platform="telegram",
    )
    ctx = ToolRuntimeContext(entity=_DummyEntity(), inp=inp, platform=_FailingPlatform(), state={})
    tok = set_tool_runtime(ctx)
    try:
        out = await speak("yo")
        data = json.loads(out)
        assert data["ok"] is False
        assert "deliver" in str(data["error"]).lower()
    finally:
        reset_tool_runtime(tok)
        if prev is None:
            del sys.modules["edge_tts"]
        else:
            sys.modules["edge_tts"] = prev


@pytest.mark.asyncio
async def test_set_tts_voice_updates_runtime_and_get_reflects_it() -> None:
    fake_mod = types.SimpleNamespace(Communicate=_FakeCommunicate, list_voices=_fake_list_voices)
    prev = sys.modules.get("edge_tts")
    sys.modules["edge_tts"] = fake_mod

    entity = _DummyEntity()
    inp = Input(
        text="change voice",
        person_id="111",
        person_name="Alice",
        channel="chat-1",
        platform="telegram",
    )
    ctx = ToolRuntimeContext(entity=entity, inp=inp, platform=_DummyPlatform(), state={})
    tok = set_tool_runtime(ctx)
    try:
        out = await set_tts_voice("es-MX-DaliaNeural")
        data = json.loads(out)
        assert data["ok"] is True
        assert data["voice_id"] == "es-MX-DaliaNeural"

        cur = json.loads(await get_tts_voice())
        assert cur["voice_id"] == "es-MX-DaliaNeural"
        assert cur["source"] == "entity_runtime_override"
    finally:
        reset_tool_runtime(tok)
        if prev is None:
            del sys.modules["edge_tts"]
        else:
            sys.modules["edge_tts"] = prev


@pytest.mark.asyncio
async def test_list_tts_voices_filters_results() -> None:
    fake_mod = types.SimpleNamespace(Communicate=_FakeCommunicate, list_voices=_fake_list_voices)
    prev = sys.modules.get("edge_tts")
    sys.modules["edge_tts"] = fake_mod
    tok = set_tool_runtime(
        ToolRuntimeContext(
            entity=_DummyEntity(),
            inp=Input(
                text="voices",
                person_id="111",
                person_name="Alice",
                channel="chat-1",
                platform="telegram",
            ),
            platform=_DummyPlatform(),
            state={},
        )
    )
    try:
        out = await list_tts_voices(query="es-mx", limit=10)
        data = json.loads(out)
        assert data["ok"] is True
        assert data["count"] == 1
        assert data["voices"][0]["voice_id"] == "es-MX-DaliaNeural"
    finally:
        reset_tool_runtime(tok)
        if prev is None:
            del sys.modules["edge_tts"]
        else:
            sys.modules["edge_tts"] = prev
