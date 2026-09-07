"""Rich renderers for the CLI — startup, shutdown, introspection (kept separate from I/O loop)."""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from rich.console import Group
from rich.panel import Panel
from rich.rule import Rule
from rich.text import Text

from ngram.identity.drives import Drive
from ngram.models import EmotionalState
from ngram.utils.clock import (
    entity_uptime,
    format_local_now_casual_lower,
    parse_entity_created_timestamp,
    time_since_last_interaction,
)


# Restrained palette: warm gold for identity, dim gray for labels, default for values.
STYLE_MARK = "dim #b8a06a"
STYLE_ENTITY = "bold #d4a656"
STYLE_LABEL = "dim"
STYLE_VALUE = ""
STYLE_RULE = "dim"
STYLE_ERROR = "#c45c5c"
STYLE_WHISPER = "dim italic"


def _sync_recent_summaries(db_path: str, limit: int = 5) -> list[str]:
    p = Path(db_path).expanduser()
    if not p.is_file():
        return []
    try:
        with sqlite3.connect(str(p)) as conn:
            cur = conn.execute(
                "SELECT summary FROM episodes ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            )
            return [str(r[0]) for r in cur.fetchall() if r and r[0]]
    except sqlite3.Error:
        return []


@dataclass
class CLIHeaderSnapshot:
    app_version: str
    entity_name: str
    mood_label: str
    drive_line: str
    episode_count: int
    reflex_model: str
    max_context_tokens: int
    tool_count: int
    awake_summary: str
    people_count: int


@dataclass
class SessionShutdownSummary:
    entity_name: str
    duration_seconds: float
    exchange_count: int
    mood_start: str
    mood_end: str
    episodes_saved: int


def _fmt_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{int(seconds)} seconds"
    if seconds < 3600:
        m = int(seconds // 60)
        return f"{m} minute{'s' if m != 1 else ''}"
    if seconds < 86400 * 2:
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        if m:
            return f"{h}h {m}m"
        return f"{h} hour{'s' if h != 1 else ''}"
    d = int(seconds // 86400)
    return f"{d} day{'s' if d != 1 else ''}"


def _parse_created_iso(raw: object) -> float | None:
    if not raw or not isinstance(raw, str):
        return None
    s = raw.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except ValueError:
        return None


def compute_awake_summary(
    *,
    created_raw: object,
    first_episode_ts: float | None,
    now: float | None = None,
) -> str:
    t = now or time.time()
    origin = _parse_created_iso(created_raw)
    if origin is None and first_episode_ts is not None:
        origin = first_episode_ts
    if origin is None:
        return "just awakened"
    return _fmt_duration(max(0.0, t - origin))


def _drive_word(level: float) -> str:
    if level < 0.22:
        return "quiet"
    if level < 0.48:
        return "stirring"
    if level < 0.72:
        return "building"
    return "pressing"


def dominant_drive_line(drives: list[Drive]) -> str:
    if not drives:
        return "drives: still"
    top = max(drives, key=lambda d: d.level)
    return f"{top.name}: {_drive_word(top.level)}"


def dominant_drive_toolbar(drives: list[Drive]) -> str:
    if not drives:
        return "inner drives are still"
    top = max(drives, key=lambda d: d.level)
    return f"{top.name} is {_drive_word(top.level)}"


def build_startup(snap: CLIHeaderSnapshot) -> Group:
    mark = Text("✦", style=STYLE_MARK)
    title = Text.assemble(
        mark,
        Text(" ngram ", style=STYLE_LABEL),
        Text(f"v{snap.app_version}", style=STYLE_VALUE),
    )

    line1 = Text.assemble(
        Text("mood: ", style=STYLE_LABEL),
        Text(snap.mood_label, style=STYLE_VALUE),
        Text(" · ", style=STYLE_LABEL),
        Text(snap.drive_line, style=STYLE_VALUE),
        Text(" · ", style=STYLE_LABEL),
        Text("memory: ", style=STYLE_LABEL),
        Text(f"{snap.episode_count} episodes", style=STYLE_VALUE),
    )
    line2 = Text.assemble(
        Text("model: ", style=STYLE_LABEL),
        Text(snap.reflex_model, style=STYLE_VALUE),
        Text(" · ", style=STYLE_LABEL),
        Text("context: ", style=STYLE_LABEL),
        Text(f"{snap.max_context_tokens // 1000}k", style=STYLE_VALUE),
        Text(" · ", style=STYLE_LABEL),
        Text("tools: ", style=STYLE_LABEL),
        Text(f"{snap.tool_count} active", style=STYLE_VALUE),
    )
    line3 = Text.assemble(
        Text("awake for ", style=STYLE_LABEL),
        Text(snap.awake_summary, style=STYLE_VALUE),
        Text(" · ", style=STYLE_LABEL),
        Text("relationship profiles ", style=STYLE_LABEL),
        Text(str(snap.people_count), style=STYLE_VALUE),
        Text(" (relational memory)", style=STYLE_LABEL),
    )

    return Group(
        title,
        Text(),
        Text(snap.entity_name, style=STYLE_ENTITY),
        line1,
        line2,
        line3,
        Text(),
        Rule(style=STYLE_RULE, characters="─")
    )


def build_shutdown(summary: SessionShutdownSummary) -> Group:
    rest = Text.assemble(
        Text(summary.entity_name, style=STYLE_ENTITY),
        Text(" is resting", style=STYLE_LABEL),
    )
    dur = _fmt_duration(summary.duration_seconds)
    sess = Text.assemble(
        Text("session: ", style=STYLE_LABEL),
        Text(dur, style=STYLE_VALUE),
        Text(" · ", style=STYLE_LABEL),
        Text(f"{summary.exchange_count} exchanges", style=STYLE_VALUE),
        Text(" · ", style=STYLE_LABEL),
        Text("mood shifted: ", style=STYLE_LABEL),
        Text(f"{summary.mood_start} → {summary.mood_end}", style=STYLE_VALUE),
    )
    mem = Text.assemble(
        Text("memory: ", style=STYLE_LABEL),
        Text(f"{summary.episodes_saved} new episodes saved", style=STYLE_VALUE),
    )
    return Group(
        Text(),
        Rule(style=STYLE_RULE, characters="─"),
        Text(),
        rest,
        sess,
        mem,
        Text(),
        Text("✦", style=STYLE_MARK),
        Text()
    )


def build_error(message: str) -> Text:
    return Text(message, style=STYLE_ERROR)


def build_memories_introspection(
    entity_name: str,
    summaries: list[str],
) -> Group:
    head = Text.assemble(
        Text(entity_name, style=STYLE_ENTITY),
        Text(" leafs through recent memories…", style=STYLE_WHISPER),
    )
    items = [head, Text()]
    if not summaries:
        items.append(Text("…nothing much on the top of the stack yet.", style=STYLE_WHISPER))
        return Group(*items)
    for i, s in enumerate(summaries, 1):
        line = Text.assemble(Text(f"  {i}. ", style=STYLE_LABEL), Text(s, style=STYLE_VALUE))
        items.append(line)
    items.append(Text())
    return Group(*items)


def build_feelings_introspection(
    entity_name: str,
    state: EmotionalState,
) -> Group:
    primary = state.primary.value
    pi = f"{state.intensity:.1f}"
    parts: list[str] = [
        f"{entity_name} is feeling {primary} ({pi})",
    ]
    if state.secondary is not None:
        parts.append(
            f"with an undercurrent of {state.secondary.value} ({state.secondary_intensity:.1f})"
        )
    ago = _fmt_duration(max(0.0, time.time() - state.last_transition))
    parts.append(f"— been in this shade for about {ago}")
    body = " ".join(parts) + "."
    return Group(Text(body, style=STYLE_VALUE), Text())


def build_side_panel(entity: object, width: int = 40) -> Panel:
    """Compact mood / drives / inner voice / memory blips (CLI 'sidebar' column)."""
    st = getattr(entity, "emotions").get_state()
    drives = getattr(entity, "drives").all_drives()
    dline = dominant_drive_line(drives)
    inner_obj = getattr(entity, "inner_voice", None)
    inner = ""
    if inner_obj is not None:
        inner = (inner_obj.recent_summary() or "").strip()[:320]
    if not inner:
        inner = "…"
    store = getattr(entity, "store", None)
    dialect = getattr(store, "dialect", "sqlite") if store is not None else "sqlite"
    db_path = str(getattr(store, "db_path", "") or "")
    if dialect == "sqlite" and db_path:
        mem_lines = _sync_recent_summaries(db_path, 4)
    else:
        mem_lines = []
    mem_preview = "\n".join(
        (f"• {m[:72]}…" if len(m) > 72 else f"• {m}") for m in mem_lines
    ) or "—"
    cfg = getattr(entity, "config", None)
    raw = getattr(cfg, "raw", {}) or {} if cfg is not None else {}
    tnow = time.time()
    clock_now = format_local_now_casual_lower()
    origin = parse_entity_created_timestamp(raw.get("created"))
    if origin is not None:
        alive_line = f"alive · {entity_uptime(origin, now=tnow)}"
    else:
        alive_line = "alive · (no created in yaml)"
    completed = getattr(entity, "_last_turn_completed_at", None)
    last_c = getattr(entity, "_last_conversation", {}) or {}
    nm = str(last_c.get("person_name") or "").strip()
    if isinstance(completed, (int, float)) and float(completed) > 0 and nm:
        gap = time_since_last_interaction(float(completed), now=tnow)
        last_line = f"last exchange · {gap} ago · {nm}"
    else:
        last_line = "last exchange · —"
    body = (
        f"{clock_now}\n{alive_line}\n{last_line}\n\n"
        f"mood · {st.primary.value} ({st.intensity:.1f})\n"
        f"drives · {dline}\n\ninner voice\n{inner}\n\nrecent memories\n{mem_preview}"
    )
    return Panel(
        Text(body, style=STYLE_VALUE),
        title=Text("self", style=STYLE_MARK),
        border_style=STYLE_RULE,
        width=width,
    )
