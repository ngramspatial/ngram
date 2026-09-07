from __future__ import annotations

from types import SimpleNamespace

import pytest

from ngram.presence.platforms.telegram_platform import TelegramPlatform


class _StubEntity:
    def __init__(self) -> None:
        self.config = SimpleNamespace(
            name="ngram",
            raw={},
            harness=SimpleNamespace(
                autonomy=SimpleNamespace(enabled=False),
                tools={},
            ),
            automations=SimpleNamespace(enabled=False),
        )


class _FakeBot:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int, int | None, str]] = []

    async def send_photo(self, *, chat_id: int, photo, caption: str = ""):
        data = photo.read()
        self.calls.append(("send_photo", chat_id, None, caption))
        return SimpleNamespace(message_id=77, byte_count=len(data))

    async def edit_message_media(self, *, chat_id: int, message_id: int, media):
        caption = getattr(media, "caption", "")
        self.calls.append(("edit_message_media", chat_id, message_id, caption))

    async def edit_message_caption(self, *, chat_id: int, message_id: int, caption: str = ""):
        self.calls.append(("edit_message_caption", chat_id, message_id, caption))


def test_menu_hides_commands_for_disabled_capabilities() -> None:
    p = TelegramPlatform("000000:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA", entity=_StubEntity())
    excluded = p._menu_excluded_command_names()
    assert {"private", "privacy", "update"}.issubset(excluded)
    assert {"session_start", "session_status", "session_stop"}.issubset(excluded)
    assert {"routines", "wakequiet"}.issubset(excluded)
    assert "status" not in excluded


@pytest.mark.asyncio
async def test_telegram_msg_meta_includes_active_remote_session() -> None:
    p = TelegramPlatform("000000:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA", entity=_StubEntity())
    await p.set_active_remote_session("99", {"session_id": "sess_1", "summary": "Watching Firefox"})
    msg = SimpleNamespace(chat_id=99, message_id=4, chat=SimpleNamespace(type="supergroup"))
    meta = p._telegram_msg_meta_with_session(msg)
    assert meta["telegram_message_id"] == 4
    assert meta["chat_type"] == "supergroup"
    assert meta["desktop_session"]["session_id"] == "sess_1"


@pytest.mark.asyncio
async def test_upsert_remote_session_card_sends_then_edits() -> None:
    p = TelegramPlatform("000000:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA", entity=_StubEntity())
    p.app = SimpleNamespace(bot=_FakeBot())
    await p.upsert_remote_session_card(
        "99",
        image_bytes=b"first-image",
        caption="first caption",
        session={"session_id": "sess_1"},
    )
    await p.upsert_remote_session_card(
        "99",
        image_bytes=b"second-image",
        caption="second caption",
        session={"session_id": "sess_1"},
    )
    assert p.app.bot.calls[0] == ("send_photo", 99, None, "first caption")
    assert p.app.bot.calls[1] == ("edit_message_media", 99, 77, "second caption")
