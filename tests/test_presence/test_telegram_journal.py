from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from ngram.presence.platforms.telegram_platform import TelegramPlatform


class _RecordingBot:
    def __init__(self) -> None:
        self.messages: list[dict[str, object]] = []

    async def send_message(self, **kwargs: object) -> None:
        self.messages.append(kwargs)


@pytest.mark.asyncio
async def test_journal_command_reads_canonical_file_and_sends_formatted_html(tmp_path) -> None:
    journal = tmp_path / "journal.md"
    journal.write_text(
        "# journal\n\n## 2026-09-01 21:04:00 #reflection\n\nStill thinking about continuity.\n",
        encoding="utf-8",
    )

    platform = object.__new__(TelegramPlatform)
    platform._entity = SimpleNamespace(
        config=SimpleNamespace(name="Rook", journal_path=lambda: str(journal))
    )
    platform._take_update_if_fresh = AsyncMock(return_value=True)
    platform._check_allowed = AsyncMock(return_value=True)
    platform._remember_last_chat = lambda _update: 42
    bot = _RecordingBot()
    platform.app = SimpleNamespace(bot=bot)

    await platform._on_journal_command(object(), SimpleNamespace(args=["1"]))

    assert len(bot.messages) == 1
    sent = bot.messages[0]
    assert sent["chat_id"] == 42
    assert sent["parse_mode"] == "HTML"
    assert "Rook's journal" in str(sent["text"])
    assert "Still thinking about continuity." in str(sent["text"])
