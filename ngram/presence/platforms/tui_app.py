"""ngram Chat TUI — full-screen Textual app with bordered panels.

Inspired by Animus TUI / Claude Code / OpenCode: bordered transcript box,
bordered input box, Footer with keybindings, command palette support.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from rich.text import Text

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Input, Static, Footer

from ngram.models import Input as EntityInput
from ngram.entity import Entity

from ngram.presence.platforms.cli_render import (
    CLIHeaderSnapshot,
    build_side_panel,
    build_memories_introspection,
    build_feelings_introspection,
)

# ── Colors ──────────────────────────────────────────────────────
C_GOLD = "#d4a656"
C_DIM = "#666666"
C_DIM_GOLD = "#8a7a4a"
C_USER_BG = "#2d2d30"


# ── ChatTranscript ──────────────────────────────────────────────

class ChatTranscript(VerticalScroll):
    """Scrollable chat area — messages rendered into a child Static widget."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._entries: list[dict] = []
        self._streaming_text = ""
        self._streaming = False
        self._last_rebuild = 0.0

    def compose(self) -> ComposeResult:
        yield Static("", id="chat-content")

    def show_welcome(self, snap: CLIHeaderSnapshot) -> None:
        self._entries.clear()
        w = Text()
        w.append("\n")
        w.append("  ✦ ", style=C_DIM_GOLD)
        w.append(f"{snap.entity_name}\n", style=f"bold {C_GOLD}")
        w.append(f"  ngram v{snap.app_version}\n", style=C_DIM)
        w.append("\n")
        w.append(f"  {snap.reflex_model}", style="")
        w.append(" · ", style=C_DIM)
        w.append(f"{snap.max_context_tokens // 1000}k context", style="")
        w.append(" · ", style=C_DIM)
        w.append(f"{snap.tool_count} tools\n", style="")
        w.append("  mood: ", style=C_DIM)
        w.append(f"{snap.mood_label}", style="")
        w.append(" · ", style=C_DIM)
        w.append(f"{snap.drive_line}\n", style="")
        w.append(f"  {snap.episode_count} episodes", style="")
        w.append(" · ", style=C_DIM)
        w.append(f"{snap.people_count} relationships", style="")
        w.append(" · awake ", style=C_DIM)
        w.append(f"{snap.awake_summary}\n", style="")
        w.append("\n")
        w.append("  ─────────────────────────────────────────────────\n", style=C_DIM)
        w.append("    /status    entity info        ctrl+s  self\n", style=C_DIM)
        w.append("    /memories  recent memories    ctrl+p  palette\n", style=C_DIM)
        w.append("    /feelings  emotional state    ctrl+t  tools\n", style=C_DIM)
        w.append("    /compact   compress context   /bye    quit\n", style=C_DIM)
        w.append("  ─────────────────────────────────────────────────\n", style=C_DIM)
        w.append("    Type a message to begin.\n", style=C_DIM)
        self.query_one("#chat-content", Static).update(w)

    def append_turn(self, role: str, content: str, name: str = "") -> None:
        self._entries.append({
            "role": role, "content": content, "name": name,
            "time": datetime.now().strftime("%H:%M"),
        })
        self._rebuild()
        self.scroll_end()

    def begin_stream(self, name: str) -> None:
        self._streaming = True
        self._streaming_text = ""
        self._entries.append({
            "role": "entity", "content": "", "name": name,
            "time": datetime.now().strftime("%H:%M"),
        })

    def stream_append(self, text: str) -> None:
        if self._entries:
            self._streaming_text += text
            self._entries[-1]["content"] = self._streaming_text
            now = time.time()
            if now - self._last_rebuild > 0.05:  # Max 20 FPS for streaming
                self._rebuild()
                self._last_rebuild = now
            self.scroll_end()

    def stream_end(self) -> None:
        self._streaming = False
        self._streaming_text = ""
        self._rebuild()

    def _rebuild(self) -> None:
        d = Text()
        for entry in self._entries:
            role, content = entry["role"], entry["content"]
            name, ts = entry.get("name", ""), entry.get("time", "")
            if role == "user":
                d.append("\n")
                for line in content.split("\n"):
                    d.append(f"  {line}  \n", style=f"bold on {C_USER_BG}")
                d.append(f"  You ({ts})\n", style=C_DIM)
            elif role == "entity":
                d.append("\n")
                d.append(f"{content}\n")
                d.append(f"  {name} ({ts})\n", style=C_DIM)
            elif role == "tool":
                d.append(f"  ⟡ {content}\n", style=f"italic {C_DIM}")
            elif role == "system":
                d.append(f"\n{content}\n", style=C_DIM)
            elif role == "thinking":
                d.append("  ··· thinking\n", style=C_DIM_GOLD)
        self.query_one("#chat-content", Static).update(d)


# ── Main App ────────────────────────────────────────────────────

class ngramTUI(App):
    """ngram Chat TUI — full-screen with bordered panels."""

    TITLE = "ngram"

    CSS = """
    Screen {
        background: $background;
        color: $text;
    }
    #layout {
        layout: vertical;
        height: 100%;
        padding: 1 2;
    }
    #layout > ChatTranscript {
        margin-bottom: 1;
    }
    #transcript {
        height: 1fr;
        border: solid $primary;
        padding: 1;
        background: $surface;
    }
    #prompt {
        width: 100%;
        border: solid $primary;
        background: $panel;
        color: $text;
        padding: 0 1;
    }
    #prompt:focus {
        border: tall $accent;
    }
    Footer {
        background: $panel;
        color: $text-muted;
    }
    """

    BINDINGS = [
        Binding("ctrl+q", "quit_app", "Quit"),
        Binding("ctrl+s", "show_self", "Self"),
        Binding("ctrl+t", "show_tools", "Tools"),
        Binding("ctrl+n", "new_session", "New"),
        Binding("ctrl+p", "command_palette", "Palette"),
    ]

    def __init__(self, platform: Any, entity: Entity):
        super().__init__()
        self.platform = platform
        self.entity = entity

    def compose(self) -> ComposeResult:
        with Vertical(id="layout"):
            yield ChatTranscript(id="transcript")
            yield Input(placeholder="Type your message here...", id="prompt")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#prompt", Input).focus()
        if self.platform.immersive:
            self.run_worker(self.platform.begin_talk_session())

    # ── Actions ─────────────────────────────────────────────────

    @work
    async def action_quit_app(self) -> None:
        await self.platform._graceful_bye()
        self.exit()

    def action_show_self(self) -> None:
        self._render_to_transcript(build_side_panel(self.entity, 70))

    def action_show_tools(self) -> None:
        self._render_to_transcript(self.platform._build_tools_list())

    def action_new_session(self) -> None:
        t = self.query_one("#transcript", ChatTranscript)
        t._entries.clear()
        self.run_worker(self._show_banner())

    async def _show_banner(self):
        snap = await self.platform._build_header_snapshot()
        self.query_one("#transcript", ChatTranscript).show_welcome(snap)

    def _render_to_transcript(self, renderable) -> None:
        """Render a Rich renderable to plain text and add as system message."""
        from io import StringIO
        from rich.console import Console as RC
        buf = StringIO()
        RC(file=buf, width=76, highlight=False).print(renderable)
        self.query_one("#transcript", ChatTranscript).append_turn(
            "system", buf.getvalue().strip()
        )

    # ── Input handling ──────────────────────────────────────────

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text:
            return
        event.input.value = ""

        if text == "/bye":
            await self.action_quit_app()
            return
        if text.startswith("/"):
            await self._handle_slash(text)
            return

        t = self.query_one("#transcript", ChatTranscript)
        t.append_turn("user", text)
        self.platform._exchange_count += 1

        inp = EntityInput(
            text=text, person_id="cli_user", person_name="You",
            channel="cli", platform="cli",
        )
        if self.platform._cb:
            self.workers.cancel_group(self, "ai_process")
            self.run_worker(self._process(inp), group="ai_process", name="ai_process")

    async def _process(self, inp: EntityInput):
        t = self.query_one("#transcript", ChatTranscript)
        t.append_turn("thinking", "")
        await self.platform._before_generation(estimated_reply_len=len(inp.text))
        try:
            reply = await self.platform._cb(inp)
            reply = reply or ""
            await self.platform._after_generation(str(reply))
        except Exception as e:
            t.append_turn("system", f"Error: {e}")
        finally:
            self._hide_thinking(rebuild=False)
            self.finalize_bot_message()

    async def _handle_slash(self, cmd: str):
        if cmd == "/status":
            await self._show_banner()
        elif cmd == "/self":
            self.action_show_self()
        elif cmd == "/memories":
            summaries = await self.entity.fetch_cli_recent_summaries(5)
            self._render_to_transcript(
                build_memories_introspection(self.entity.config.name, summaries)
            )
        elif cmd == "/feelings":
            self._render_to_transcript(
                build_feelings_introspection(
                    self.entity.config.name, self.entity.emotions.get_state()
                )
            )
        elif cmd == "/tools":
            self.action_show_tools()
        elif cmd.startswith("/compact"):
            aggressive = "aggressive" in cmd.lower()
            self.query_one("#transcript", ChatTranscript).append_turn(
                "system", "Compacting conversation context..."
            )
            self.workers.cancel_group(self, "ai_process")
            self.run_worker(self.platform._slash_compact(aggressive=aggressive), group="ai_process", name="compact")

    # ── Public API for CLIPlatform ──────────────────────────────

    def add_message(self, sender: str, text: str, is_user: bool = False):
        t = self.query_one("#transcript", ChatTranscript)
        t.append_turn("user" if is_user else "entity", text, name=sender)

    def add_system_message(self, text: str, renderable=None):
        if renderable is not None:
            self._render_to_transcript(renderable)
        elif text:
            self.query_one("#transcript", ChatTranscript).append_turn("system", text)

    def add_tool_activity(self, desc: str):
        self.query_one("#transcript", ChatTranscript).append_turn("tool", desc)

    def append_bot_text(self, text: str):
        t = self.query_one("#transcript", ChatTranscript)
        if not t._streaming:
            # Remove thinking indicator if present
            if t._entries and t._entries[-1]["role"] == "thinking":
                t._entries.pop()
            t.begin_stream(self.entity.config.name)
        t.stream_append(text)

    def finalize_bot_message(self):
        self.query_one("#transcript", ChatTranscript).stream_end()

    def _hide_thinking(self, rebuild: bool = True):
        """Remove the thinking indicator from the transcript if present."""
        t = self.query_one("#transcript", ChatTranscript)
        if t._entries and t._entries[-1]["role"] == "thinking":
            t._entries.pop()
            if rebuild:
                t._rebuild()
