import json
from typing import Any

import pytest

from ngram.models import Input
from ngram.presence.tools.messaging import list_known_contacts, send_dm, send_message_to
from ngram.presence.tools.runtime import ToolRuntimeContext, reset_tool_runtime, set_tool_runtime


class _DummyEntity:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str, dict[str, Any]]] = []
        self.dms: list[tuple[str, str, str, dict[str, Any]]] = []
        self.routes = [
            {
                "person_name": "Bob",
                "person_id": "222",
                "platform": "telegram",
                "channel": "-10055",
                "chat_type": "group",
                "at": 100.0,
            },
            {
                "person_name": "Bob",
                "person_id": "222",
                "platform": "telegram",
                "channel": "222",
                "chat_type": "private",
                "at": 90.0,
            },
        ]

    async def send_message_to_platform(
        self, platform: str, target: str, message: str, **kw: Any
    ) -> None:
        self.sent.append((platform, target, message, dict(kw)))

    async def send_dm_to_user(self, platform: str, user_id: str, message: str, **kw: Any) -> None:
        self.dms.append((platform, user_id, message, dict(kw)))

    def list_known_person_routes(self, platform: str = "") -> list[dict[str, str | float]]:
        pf = (platform or "").strip().lower()
        if not pf:
            return list(self.routes)
        return [r for r in self.routes if str(r.get("platform") or "").lower() == pf]

    def resolve_person_route(
        self,
        target_person: str,
        *,
        platform: str = "",
        prefer_private: bool = True,
    ) -> dict[str, str | float] | None:
        low = (target_person or "").strip().casefold()
        cands = [r for r in self.routes if str(r.get("person_name") or "").casefold() == low]
        if platform:
            cands = [r for r in cands if str(r.get("platform") or "").lower() == platform]
        if prefer_private:
            priv = [r for r in cands if str(r.get("chat_type") or "").lower() == "private"]
            if priv:
                return dict(priv[0])
        return dict(cands[0]) if cands else None


def _set_ctx(entity: _DummyEntity):
    inp = Input(text="relay this", person_id="111", person_name="Alice", channel="-10055", platform="telegram")
    return set_tool_runtime(ToolRuntimeContext(entity=entity, inp=inp))


@pytest.mark.asyncio
async def test_send_message_to_as_voice_passes_kwargs() -> None:
    entity = _DummyEntity()
    tok = _set_ctx(entity)
    try:
        out = await send_message_to(
            message="hello voice",
            platform="telegram",
            target="12345",
            as_voice=True,
            voice_id="en-US-JennyNeural",
        )
        data = json.loads(out)
        assert data["ok"] is True
        assert entity.sent == [
            (
                "telegram",
                "12345",
                "hello voice",
                {"as_voice": True, "voice_id": "en-US-JennyNeural"},
            )
        ]
    finally:
        reset_tool_runtime(tok)


@pytest.mark.asyncio
async def test_send_message_to_person_confirm_includes_as_voice() -> None:
    entity = _DummyEntity()
    tok = _set_ctx(entity)
    try:
        out = await send_message_to(message="yo", target_person="Bob", as_voice=True, voice_id="en-GB")
        data = json.loads(out)
        assert data["needs_confirmation"] is True
        assert data["as_voice"] is True
        assert data["voice_id"] == "en-GB"
    finally:
        reset_tool_runtime(tok)


@pytest.mark.asyncio
async def test_send_message_to_explicit_destination() -> None:
    entity = _DummyEntity()
    tok = _set_ctx(entity)
    try:
        out = await send_message_to(message="hello", platform="telegram", target="12345")
        data = json.loads(out)
        assert data["ok"] is True
        assert entity.sent == [("telegram", "12345", "hello", {"as_voice": False, "voice_id": ""})]
    finally:
        reset_tool_runtime(tok)


@pytest.mark.asyncio
async def test_send_message_to_person_requires_confirmation_first() -> None:
    entity = _DummyEntity()
    tok = _set_ctx(entity)
    try:
        out = await send_message_to(message="yo", target_person="Bob")
        data = json.loads(out)
        assert data["needs_confirmation"] is True
        assert entity.sent == []
    finally:
        reset_tool_runtime(tok)


@pytest.mark.asyncio
async def test_send_message_to_person_confirm_sends_private_route() -> None:
    entity = _DummyEntity()
    tok = _set_ctx(entity)
    try:
        out = await send_message_to(message="yo", target_person="Bob", confirm=True)
        data = json.loads(out)
        assert data["ok"] is True
        assert entity.sent == [("telegram", "222", "yo", {"as_voice": False, "voice_id": ""})]
    finally:
        reset_tool_runtime(tok)


@pytest.mark.asyncio
async def test_list_known_contacts_returns_routes() -> None:
    entity = _DummyEntity()
    tok = _set_ctx(entity)
    try:
        out = await list_known_contacts(platform="telegram")
        data = json.loads(out)
        assert len(data["contacts"]) == 2
        assert all(c["platform"] == "telegram" for c in data["contacts"])
    finally:
        reset_tool_runtime(tok)


@pytest.mark.asyncio
async def test_send_dm_list_targets_dedupes_user() -> None:
    entity = _DummyEntity()
    tok = _set_ctx(entity)
    try:
        out = await send_dm(list_targets=True)
        data = json.loads(out)
        assert data["ok"] is True
        assert data["count"] == 1
        assert data["targets"][0]["user_id"] == "222"
        assert data["targets"][0]["platform"] == "telegram"
    finally:
        reset_tool_runtime(tok)


@pytest.mark.asyncio
async def test_send_dm_confirmation_includes_as_voice() -> None:
    entity = _DummyEntity()
    tok = _set_ctx(entity)
    try:
        out = await send_dm(message="vm", user_id="222", platform="telegram", as_voice=True, voice_id="fr-FR")
        data = json.loads(out)
        assert data["needs_confirmation"] is True
        assert data["as_voice"] is True
        assert data["voice_id"] == "fr-FR"
    finally:
        reset_tool_runtime(tok)


@pytest.mark.asyncio
async def test_send_dm_requires_confirmation() -> None:
    entity = _DummyEntity()
    tok = _set_ctx(entity)
    try:
        out = await send_dm(message="secret", user_id="222", platform="telegram")
        data = json.loads(out)
        assert data["needs_confirmation"] is True
        assert entity.dms == []
    finally:
        reset_tool_runtime(tok)


@pytest.mark.asyncio
async def test_send_dm_confirm_delivers() -> None:
    entity = _DummyEntity()
    tok = _set_ctx(entity)
    try:
        out = await send_dm(message="hi dm", user_id="222", platform="telegram", confirm=True)
        data = json.loads(out)
        assert data["ok"] is True
        assert entity.dms == [("telegram", "222", "hi dm", {"as_voice": False, "voice_id": ""})]
    finally:
        reset_tool_runtime(tok)


@pytest.mark.asyncio
async def test_send_dm_target_person_confirm() -> None:
    entity = _DummyEntity()
    tok = _set_ctx(entity)
    try:
        out = await send_dm(message="yo", target_person="Bob", platform="telegram", confirm=True)
        data = json.loads(out)
        assert data["ok"] is True
        assert entity.dms == [("telegram", "222", "yo", {"as_voice": False, "voice_id": ""})]
    finally:
        reset_tool_runtime(tok)
