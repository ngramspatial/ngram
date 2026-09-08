"""Terminal UI: Textual ChatApp, entitative session chrome."""

from __future__ import annotations

import asyncio
import sqlite3
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from rich.text import Text

from ngram.__version__ import __version__
from ngram.models import Input
from ngram.entity import Entity
from ngram.presence.platforms.base import Platform
from ngram.presence.platforms.cli_render import (
    CLIHeaderSnapshot,
    SessionShutdownSummary,
    compute_awake_summary,
    dominant_drive_line,
    STYLE_LABEL, STYLE_VALUE, STYLE_WHISPER
)


def _sync_episode_count(db_path: str) -> int:
    """Avoid aiosqlite here — daemon + REPL can race on Windows/asyncio."""
    p = Path(db_path).expanduser()
    if not p.is_file():
        return 0
    try:
        with sqlite3.connect(str(p)) as conn:
            row = conn.execute("SELECT COUNT(*) FROM episodes").fetchone()
            return int(row[0]) if row else 0
    except sqlite3.Error:
        return 0


def _sync_relationship_count(db_path: str) -> int:
    p = Path(db_path).expanduser()
    if not p.is_file():
        return 0
    try:
        with sqlite3.connect(str(p)) as conn:
            row = conn.execute("SELECT COUNT(*) FROM relationships").fetchone()
            return int(row[0]) if row else 0
    except sqlite3.Error:
        return 0


def _sync_min_episode_timestamp(db_path: str) -> float | None:
    p = Path(db_path).expanduser()
    if not p.is_file():
        return None
    try:
        with sqlite3.connect(str(p)) as conn:
            row = conn.execute("SELECT MIN(timestamp) FROM episodes").fetchone()
            if row and row[0] is not None:
                return float(row[0])
    except sqlite3.Error:
        pass
    return None


class CLIPlatform(Platform):
    def __init__(
        self,
        entity: Entity,
        app_version: str = __version__,
        *,
        immersive: bool = True,
    ) -> None:
        self.entity = entity
        self.entity_name = entity.config.name
        self.app_version = app_version
        self.immersive = immersive
        self._cb: Callable[[Input], Awaitable[Any]] | None = None
        self._connected = False

        self._session_t0: float = 0.0
        self._exchange_count = 0
        self._episodes_start: int | None = None
        self._mood_start_label = ""
        self._session_begun = False

        self._expression_meta = entity.voice_ctl.meta_for_response(
            entity.emotions.get_state(), 80, "cli"
        )

        self.app = None

    async def connect(self) -> None:
        self._connected = True

    async def _build_header_snapshot(self) -> CLIHeaderSnapshot:
        cfg = self.entity.config
        st = self.entity.emotions.get_state()
        if getattr(self.entity.store, "dialect", "sqlite") == "sqlite":
            path = self.entity.store.db_path
            n_ep = _sync_episode_count(path)
            n_people = _sync_relationship_count(path)
            first_ts = _sync_min_episode_timestamp(path)
        else:
            n_ep, n_people, first_ts = await self.entity.fetch_cli_header_counts()
        awake = compute_awake_summary(
            created_raw=cfg.raw.get("created"),
            first_episode_ts=first_ts,
        )
        return CLIHeaderSnapshot(
            app_version=self.app_version,
            entity_name=cfg.name,
            mood_label=st.primary.value,
            drive_line=dominant_drive_line(self.entity.drives.all_drives()),
            episode_count=n_ep,
            reflex_model=cfg.cognition.reflex_model,
            max_context_tokens=cfg.cognition.max_context_tokens,
            tool_count=len(self.entity.tools.openai_tools()),
            awake_summary=awake,
            people_count=n_people,
        )

    async def begin_talk_session(self) -> None:
        """Show startup banner — no auto-generated greeting, just display info and wait."""
        if self._session_begun or not self.immersive:
            return
        self._session_begun = True
        self._session_t0 = time.time()
        self._mood_start_label = self.entity.emotions.get_state().primary.value
        if getattr(self.entity.store, "dialect", "sqlite") == "sqlite":
            self._episodes_start = _sync_episode_count(self.entity.store.db_path)
        else:
            self._episodes_start = (await self.entity.fetch_cli_header_counts())[0]

        # Show banner — no cli_opening() call, just like Claude Code / Gemini CLI
        snap = await self._build_header_snapshot()
        if self.app:
            from ngram.presence.platforms.tui_app import ChatTranscript
            t = self.app.query_one("#transcript", ChatTranscript)
            t.show_welcome(snap)

    async def send_message(self, channel: str, content: str) -> None:
        if self.app:
            self.app.add_message(self.entity_name, content, False)

    async def send_tool_activity(self, description: str) -> None:
        t = (description or "").strip()
        if not t:
            return
        if self.app:
            self.app.add_tool_activity(t)

    async def on_message(self, callback: Callable[..., Any]) -> None:
        self._cb = callback

    async def _cancel_thinking(self) -> None:
        if self.app:
            self.app._hide_thinking()

    async def _before_generation(self, estimated_reply_len: int = 120) -> None:
        st = self.entity.emotions.get_state()
        self._expression_meta = self.entity.voice_ctl.meta_for_response(
            st, estimated_reply_len, "cli",
        )
        # Brief pause for naturalness, but much shorter than before
        delay = min(1.0, self._expression_meta.typing_delay_seconds)
        if delay > 0:
            await asyncio.sleep(delay)

    async def stream_delta(self, s: str) -> None:
        if self.app:
            self.app.append_bot_text(s)

    @staticmethod
    def _sentence_boundary(text: str) -> bool:
        t = text.rstrip()
        if not t:
            return False
        return t[-1] in ".!?…"

    async def _after_generation(self, reply: str) -> None:
        if self.app:
            self.app.finalize_bot_message()

    def _build_tools_list(self) -> Any:
        list_fn = getattr(self.entity.tools, "list_tools", None)
        if callable(list_fn):
            rows = list_fn()
        else:
            rows = []
            for t in self.entity.tools.openai_tools():
                fn = t.get("function", {}) if isinstance(t, dict) else {}
                name = str(fn.get("name") or "").strip()
                desc = str(fn.get("description") or "").strip()
                if name:
                    rows.append((name, desc))
            rows = sorted(rows, key=lambda r: r[0])

        from rich.console import Group

        items = [Text(f"Active tools ({len(rows)})", style=STYLE_LABEL)]
        if not rows:
            items.append(Text("none", style=STYLE_WHISPER))
            return Group(*items)
        for name, desc in rows:
            items.append(Text(f"  {name}", style=STYLE_VALUE))
            if desc:
                items.append(Text(f"    {desc}", style=STYLE_WHISPER))
        return Group(*items)

    async def _slash_compact(self, aggressive: bool = False) -> None:
        try:
            res = await self.entity.compact_context_now(
                aggressive=aggressive, passes=2 if aggressive else 1
            )
        except Exception as e:
            if self.app:
                from ngram.presence.platforms.cli_render import build_error
                self.app.add_system_message("", renderable=build_error(
                    f"context compaction failed: {e}"
                ))
            return
        if not isinstance(res, dict):
            if self.app:
                self.app.add_system_message("Compaction finished.")
            return
        status = "done" if res.get("ok") else "skipped"
        mb = int(res.get("messages_before", 0) or 0)
        ma = int(res.get("messages_after", mb) or mb)
        sb = int(res.get("summary_chars_before", 0) or 0)
        sa = int(res.get("summary_chars_after", sb) or sb)
        msg = f"Compaction {status}: messages {mb}->{ma}, summary chars {sb}->{sa}."
        if self.app:
            self.app.add_system_message("", renderable=Text(msg, style=STYLE_WHISPER))

    async def _graceful_bye(self) -> None:
        if getattr(self.entity.store, "dialect", "sqlite") == "sqlite":
            episodes_end = _sync_episode_count(self.entity.store.db_path)
        else:
            episodes_end = (await self.entity.fetch_cli_header_counts())[0]
        mood_end = self.entity.emotions.get_state().primary.value
        start_ep = (
            self._episodes_start if self._episodes_start is not None else episodes_end
        )
        summary = SessionShutdownSummary(
            entity_name=self.entity_name,
            duration_seconds=max(0.0, time.time() - self._session_t0),
            exchange_count=self._exchange_count,
            mood_start=self._mood_start_label or mood_end,
            mood_end=mood_end,
            episodes_saved=max(0, episodes_end - start_ep),
        )
        try:
            from ngram.presence.platforms.cli_render import build_shutdown
            if self.app:
                self.app.add_system_message("", renderable=build_shutdown(summary))
        except Exception:
            pass
        self._connected = False

    async def run_repl(self) -> None:
        if not self.immersive:
            self._session_t0 = time.time()
            self._mood_start_label = self.entity.emotions.get_state().primary.value
            if getattr(self.entity.store, "dialect", "sqlite") == "sqlite":
                self._episodes_start = _sync_episode_count(self.entity.store.db_path)
            else:
                self._episodes_start = (await self.entity.fetch_cli_header_counts())[0]

        from ngram.presence.platforms.tui_app import ngramTUI
        self.app = ngramTUI(platform=self, entity=self.entity)
        await self.app.run_async()

    async def set_presence(self, status: str) -> None:
        pass

    async def disconnect(self) -> None:
        if self.app:
            self.app.exit()
        self._connected = False

    async def send_audio(self, channel: str, path: str) -> bool:
        return False

    async def send_image(self, channel: str, path: str) -> None:
        return None

    def get_person_id(self, message: Any) -> str:
        return "cli_user"

    def get_person_name(self, message: Any) -> str:
        return "You"
