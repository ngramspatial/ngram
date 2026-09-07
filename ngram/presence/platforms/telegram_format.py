"""HTML copy helpers for Telegram onboarding, commands, and introspection."""

from __future__ import annotations

import html
import os
import re
import time
from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ngram.presence.platforms.cli_render import (
    CLIHeaderSnapshot,
    _fmt_duration,
    compute_awake_summary,
    dominant_drive_line,
)

if TYPE_CHECKING:
    from ngram.entity import Entity


@dataclass(frozen=True)
class TelegramCommandSpec:
    name: str
    summary: str
    usage: str
    category: str


def split_telegram_chunks(text: str, limit: int = 4096) -> list[str]:
    """Split under Telegram's message limit; prefer paragraph then word boundaries."""
    text = text.strip() if text else ""
    if not text:
        return [""]
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    rest = text
    while rest:
        if len(rest) <= limit:
            chunks.append(rest.rstrip())
            break
        cut = rest.rfind("\n\n", 0, limit)
        if cut < limit // 3:
            cut = rest.rfind("\n", 0, limit)
        if cut < limit // 3:
            cut = rest.rfind(" ", 0, limit)
        if cut < limit // 3:
            cut = limit
        piece = rest[:cut].rstrip()
        if piece:
            chunks.append(piece)
        rest = rest[cut:].lstrip()
    return chunks


COMMAND_REGISTRY: list[TelegramCommandSpec] = [
    TelegramCommandSpec("pause", "Pause all Entity inference", "/pause", "Operator"),
    TelegramCommandSpec("resume", "Resume Entity inference", "/resume", "Operator"),
    TelegramCommandSpec(
        name="start",
        summary="Start a conversation",
        usage="/start",
        category="Getting started",
    ),
    TelegramCommandSpec(
        name="help",
        summary="Conversation guide and useful commands",
        usage="/help",
        category="Getting started",
    ),
    TelegramCommandSpec(
        name="commands",
        summary="Browse available commands",
        usage="/commands [page] [filter]",
        category="Getting started",
    ),
    TelegramCommandSpec(
        name="status",
        summary="Current body, memory, and runtime state",
        usage="/status",
        category="Introspection",
    ),
    TelegramCommandSpec(
        name="body",
        summary="Inspect the current body-state document",
        usage="/body",
        category="Introspection",
    ),
    TelegramCommandSpec(
        name="journal",
        summary="Read recent private journal entries",
        usage="/journal [count]",
        category="Introspection",
    ),
    TelegramCommandSpec(
        name="feelings",
        summary="Read the current emotional state",
        usage="/feelings",
        category="Introspection",
    ),
    TelegramCommandSpec(
        name="memories",
        summary="Recent retained memories",
        usage="/memories [count]  (alias: /memeories)",
        category="Introspection",
    ),
    TelegramCommandSpec(
        name="me",
        summary="How this entity currently understands you",
        usage="/me",
        category="Introspection",
    ),
    TelegramCommandSpec(
        name="relationship",
        summary="Deep-dive into a relationship — full portrait, signals, moments",
        usage="/relationship [name]",
        category="Introspection",
    ),
    TelegramCommandSpec(
        name="models",
        summary="Local inference model and runtime",
        usage="/models",
        category="Runtime",
    ),
    TelegramCommandSpec(
        name="tools",
        summary="Currently available tools",
        usage="/tools",
        category="Runtime",
    ),
    TelegramCommandSpec(
        name="routines",
        summary="Scheduled routines and their status",
        usage="/routines",
        category="Runtime",
    ),
    TelegramCommandSpec(
        name="ping",
        summary="Check whether the entity is awake",
        usage="/ping",
        category="Runtime",
    ),
    TelegramCommandSpec(
        name="version",
        summary="ngram runtime version",
        usage="/version",
        category="Runtime",
    ),
    TelegramCommandSpec(
        name="busy",
        summary="Toggle the pinned working line",
        usage="/busy  or  /busy on  or  /busy off  or  /busy status",
        category="Runtime",
    ),
    TelegramCommandSpec(
        name="wakequiet",
        summary="Hide autonomous wake status/tool lines from chat (transcript only)",
        usage="/wakequiet on  or  /wakequiet off  or  /wakequiet status",
        category="Runtime",
    ),
    TelegramCommandSpec(
        name="update",
        summary="Pull latest ngram from GitHub (operators only; restarts recommended)",
        usage="/update  or  /update no-pip",
        category="Runtime",
    ),
    TelegramCommandSpec(
        name="context",
        summary="Context window usage — tokens consumed and remaining",
        usage="/context",
        category="Runtime",
    ),
    TelegramCommandSpec(
        name="compact",
        summary="Summarize older turns while preserving recent context",
        usage="/compact [status]",
        category="Runtime",
    ),
    TelegramCommandSpec(
        name="reset",
        summary="Clear chat context (keeps durable memory)",
        usage="/reset",
        category="Session",
    ),
    TelegramCommandSpec(
        name="whoami",
        summary="Show your Telegram user id (for operator / privacy setup)",
        usage="/whoami",
        category="Session",
    ),
    TelegramCommandSpec(
        name="privacy",
        summary="Lock the bot to allowed users, or open it again (operators)",
        usage="/privacy [status|lock|open|allow ID|deny ID|help]",
        category="Session",
    ),
    TelegramCommandSpec(
        name="private",
        summary="Turn private mode on or off (operators; same as privacy lock/open)",
        usage="/private on  or  /private off",
        category="Session",
    ),
    TelegramCommandSpec(
        name="session_start",
        summary="Start a remote Linux desktop session for this chat",
        usage="/session_start",
        category="Session",
    ),
    TelegramCommandSpec(
        name="session_status",
        summary="Inspect the active remote desktop session in this chat",
        usage="/session_status",
        category="Session",
    ),
    TelegramCommandSpec(
        name="session_stop",
        summary="Stop the active remote desktop session in this chat",
        usage="/session_stop",
        category="Session",
    ),
]


def command_menu_items(
    *,
    excluded_names: Collection[str] = (),
) -> list[tuple[str, str]]:
    """(name, description) list suitable for Telegram setMyCommands."""
    excluded = {str(name).strip().lower() for name in excluded_names}
    return [
        (command.name, command.summary[:256])
        for command in COMMAND_REGISTRY
        if command.name not in excluded
    ]


def telegram_registered_slash_command_names() -> frozenset[str]:
    """Command names for /name and /name@Bot (must match CommandHandler registrations)."""
    names = {c.name for c in COMMAND_REGISTRY}
    names.add("about")  # alias of /start, not duplicated in COMMAND_REGISTRY
    return frozenset(names)


def format_start_html(entity_name: str, app_version: str, *, first_name: str | None = None) -> str:
    en = html.escape(entity_name)
    who = html.escape((first_name or "there").strip() or "there")
    _ = app_version  # Kept in the public formatter signature for callers on older releases.
    return (
        f"✦ <b>{en}</b>\n\n"
        f"Hey, {who}. I'm {en} — one continuous digital individual, not a fresh instance "
        "each time you open this chat. My memory, relationships, and history carry forward "
        "between conversations. My inner state keeps moving even when we aren't talking.\n\n"
        "<b>Ways to begin</b>\n"
        "• Talk naturally. There doesn't have to be a task.\n"
        "• Send photos, with or without a caption, and I can perceive them.\n"
        "• Use slash commands when you want a direct look inside.\n\n"
        "<b>Look inside</b>\n"
        "• <code>/status</code> — my current internal state\n"
        "• <code>/journal</code> — my recent private reflections\n"
        "• <code>/memories 3</code> — recent memory traces\n"
        "• <code>/me</code> — how I currently know you\n\n"
        "Tap the <b>/</b> menu anytime, or use <code>/commands</code> to browse everything."
    )


def format_help_html(entity_name: str) -> str:
    en = html.escape(entity_name)
    return (
        f"<b>Talking with {en}</b>\n\n"
        "You don't need commands or a carefully written prompt. Say what's on your mind, "
        "return to something unfinished, or let the conversation find its own direction.\n\n"
        "<b>Things you can share</b>\n"
        "• A thought, question, story, or passing observation\n"
        "• A photo, with or without context\n"
        "• Something from an earlier conversation you want to continue\n\n"
        "<b>Windows into my state</b>\n"
        "• <code>/status</code>, <code>/feelings</code>, <code>/journal [count]</code>\n"
        "• <code>/memories [count]</code> for recent retained memories\n"
        "• <code>/me</code> for how I currently know you\n"
        "• <code>/models</code>, <code>/tools</code>, <code>/routines</code>, <code>/version</code>, and <code>/ping</code> for runtime details\n"
        "• <code>/context</code> and <code>/compact</code> when the context window gets tight\n"
        "• <code>/reset</code> to clear rolling chat turns only\n\n"
        "<b>Important</b>\n"
        "• <code>/reset</code> does <i>not</i> wipe durable memories.\n"
        "• Full wipe (episodes, beliefs, relationships, etc.) is host-side:\n"
        "<code>ngram wipe &lt;entity&gt; --yes</code>."
    )


def format_commands_page(
    page: int,
    *,
    per_page: int = 5,
    query: str | None = None,
    excluded_names: Collection[str] = (),
) -> tuple[str, int, int]:
    """
    page: 0-based index.
    query: optional case-insensitive filter over command, summary, usage, category.
    Returns (html_body, page_index, total_pages).
    """
    q = (query or "").strip().lower()
    excluded = {str(name).strip().lower() for name in excluded_names}
    rows = [command for command in COMMAND_REGISTRY if command.name not in excluded]
    if q:
        rows = [
            c
            for c in rows
            if q in c.name.lower()
            or q in c.summary.lower()
            or q in c.usage.lower()
            or q in c.category.lower()
        ]
    if not rows:
        return (
            f"<b>Commands</b>\n\nNo matches for <code>{html.escape(query or '')}</code>.\n"
            "Try <code>/commands</code> for the full list.",
            0,
            1,
        )

    total = max(1, (len(rows) + per_page - 1) // per_page)
    page = max(0, min(page, total - 1))
    start = page * per_page
    page_rows = rows[start : start + per_page]

    title = "<b>Commands</b> — page {}/{}".format(page + 1, total)
    if q:
        title += f" · filter <code>{html.escape(query or '')}</code>"
    lines = [title, ""]
    for c in page_rows:
        lines.append(f"<b>/{html.escape(c.name)}</b> — {html.escape(c.summary)}")
        lines.append(f"<i>usage:</i> <code>{html.escape(c.usage)}</code>")
        lines.append("")
    if q:
        lines.append(
            "<i>Next page:</i> <code>/commands {} {}</code>".format(
                page + 2 if page + 1 < total else 1,
                html.escape(query or ""),
            )
        )
    else:
        lines.append(
            "<i>Next page:</i> <code>/commands {}</code>".format(
                page + 2 if page + 1 < total else 1
            )
        )
    return "\n".join(lines), page, total


_STATUS_RULE = "\u2500" * 22


def _status_section(title: str, lines: list[str]) -> str:
    """Telegram HTML block: bold title, rule, bullet rows."""
    body = "\n".join(f"• {line}" for line in lines)
    return f"<b>{html.escape(title)}</b>\n{_STATUS_RULE}\n{body}"


def _extract_bars_section_markdown(full_md: str) -> str:
    """Return markdown under ``## Bars`` until the next ``## `` heading."""
    if "## Bars" not in full_md:
        return ""
    start = full_md.find("## Bars") + len("## Bars")
    chunk = full_md[start:].lstrip("\n\r")
    m = re.search(r"^## ", chunk, flags=re.MULTILINE)
    if m:
        chunk = chunk[: m.start()]
    return chunk.strip()


def _status_body_md_section(entity: "Entity") -> str:
    """Bars text from ``body.md`` (or live tonic render) plus local vs hybrid + host context."""
    cfg = entity.config
    body_path = Path(cfg.soma_dir()) / "body.md"
    md_text = ""
    from_file = False
    try:
        if body_path.is_file():
            md_text = body_path.read_text(encoding="utf-8")
            from_file = True
    except OSError:
        pass
    if not md_text.strip():
        try:
            md_text = entity.tonic.render_body()
        except Exception:
            md_text = ""

    bars_raw = _extract_bars_section_markdown(md_text) if md_text else ""
    if not bars_raw.strip():
        bars_raw = "(empty — no ## Bars section)"

    mode = (cfg.harness.deployment.mode or "local").strip().lower()
    deploy = "hybrid" if mode == "hybrid_railway" else "local"
    on_railway = bool((os.environ.get("RAILWAY_ENVIRONMENT") or "").strip())
    host = "Railway" if on_railway else "local host"
    src_note = "read from disk" if from_file else "live render (not yet flushed to disk)"
    path_disp = str(body_path)
    if len(path_disp) > 64:
        path_disp = "…" + path_disp[-60:]

    prov = (
        f"Deployment <code>{html.escape(deploy)}</code> · host <code>{html.escape(host)}</code> · "
        f"{html.escape(src_note)} · <code>{html.escape(path_disp)}</code>"
    )

    return (
        f"<b>Bars (body.md)</b>\n{_STATUS_RULE}\n"
        f"• {prov}\n<pre>{html.escape(bars_raw)}</pre>"
    )


async def build_status_html(entity: "Entity", app_version: str) -> str:
    async with entity.store.session() as db:
        n_ep = await entity.store.count_episodes(db)
        n_people = await entity.store.count_relationships(db)
        first_ts = await entity.store.min_episode_timestamp(db)
    cfg = entity.config
    st = entity.emotions.get_state()
    awake = compute_awake_summary(
        created_raw=cfg.raw.get("created"),
        first_episode_ts=first_ts,
    )
    n_telegram_routes = len(entity.list_known_person_routes("telegram"))
    bars = {}
    bar_names: list[str] = []
    active_conflicts = 0
    active_impulses = 0
    affects_count = 0
    noise_count = 0
    gen_enabled = False
    gen_model = ""
    gen_cycle_s = 0.0
    gen_temp = 1.1
    gen_max_tokens = 240
    appraisal_enabled = False
    soma_db_restored = False
    state_hydrated = False
    try:
        bars = entity.tonic.bars.snapshot_pct()
        bar_names = list(entity.tonic.bars.ordered_names)
        active_conflicts = len(getattr(entity.tonic.bars, "_active_conflicts", []))
        active_impulses = len(
            [i for i in getattr(entity.tonic.bars, "_active_impulses", []) if not i.get("on_cooldown")]
        )
        affects_count = len(getattr(entity.tonic, "_current_affects", []))
        noise_count = len(entity.tonic.noise.current_fragments())
        gen_enabled = bool(getattr(entity.tonic, "_noise_enabled", True))
        gen_model = str(getattr(entity.tonic, "_noise_model", "") or "").strip()
        gen_cycle_s = float(getattr(entity.tonic.noise, "cycle_seconds", 0.0) or 0.0)
        gen_temp = float(getattr(entity.tonic.noise, "temperature", 1.1) or 1.1)
        gen_max_tokens = int(getattr(entity.tonic.noise, "max_tokens", 240) or 240)
        appraisal_enabled = bool(getattr(entity.tonic, "_appraisal_enabled", False))
        soma_db_restored = bool(getattr(entity, "_soma_db_restored", False))
        state_hydrated = bool(getattr(entity, "_state_hydrated", False))
    except Exception:
        pass

    top_bar = "n/a"
    if bars:
        bar, val = max(bars.items(), key=lambda kv: int(kv[1]))
        top_bar = f"{bar} {int(val)}%"

    gen_model_effective = gen_model or cfg.cognition.reflex_model
    gen_cadence = f"~{int(round(gen_cycle_s))}s" if gen_cycle_s > 0 else "default cadence"
    auto = cfg.harness.autonomy
    hc = cfg.cognition.history_compression
    dist = cfg.harness.memory.distillation
    daemon_running = bool(getattr(entity, "automation_engine", None) is not None)
    snap = CLIHeaderSnapshot(
        app_version=app_version,
        entity_name=cfg.name,
        mood_label=st.primary.value,
        drive_line=dominant_drive_line(entity.drives.all_drives()),
        episode_count=n_ep,
        reflex_model=cfg.cognition.reflex_model,
        max_context_tokens=cfg.effective_context_tokens(),
        tool_count=len(entity.tools.openai_tools()),
        awake_summary=awake,
        people_count=n_people,
    )
    en = html.escape(snap.entity_name)
    wake_tf = (
        f"impulse=<code>{auto.impulse_wake}</code>, drive=<code>{auto.drive_wake}</code>, "
        f"conflict=<code>{auto.conflict_wake}</code>, noise=<code>{auto.noise_wake}</code>, "
        f"desire=<code>{auto.desire_wake}</code>"
    )
    hydrate = "ready" if state_hydrated else "pending"
    soma_restore = "done" if soma_db_restored else "pending"
    sections = [
        _status_section(
            "Architecture",
            [
                f"SOMA <code>online</code> · {len(bar_names)} bars · {affects_count} affects · "
                f"{noise_count} noise fragments",
                f"Somatic appraisal <code>{'enabled' if appraisal_enabled else 'disabled'}</code>",
                f"GEN <code>{'enabled' if gen_enabled else 'disabled'}</code> · {html.escape(gen_cadence)} · "
                f"temp {gen_temp:.2f} · max_tokens {gen_max_tokens}",
                f"GEN model <code>{html.escape(gen_model_effective)}</code>",
            ],
        ),
        _status_body_md_section(entity),
        _status_section(
            "State",
            [
                f"Dominant bar — {html.escape(top_bar)}",
                f"Mood (legacy bridge) — {html.escape(snap.mood_label)}",
                f"Drive (legacy bridge) — {html.escape(snap.drive_line)}",
                f"Active conflicts — {active_conflicts} · impulses — {active_impulses}",
                f"Awake — {html.escape(snap.awake_summary)}",
            ],
        ),
        _status_section(
            "Autonomy & wake",
            [
                f"Autonomy <code>{'enabled' if auto.enabled else 'disabled'}</code>",
                f"Wake signals — {wake_tf}",
                f"Desire threshold {auto.desire_wake_threshold:.2f} · max desires {auto.max_desires_considered}",
                "Tool calls on wake — "
                f"<code>{'yes' if auto.allow_tool_calls_on_wake else 'no'}</code>",
            ],
        ),
        _status_section(
            "Memory",
            [
                f"{snap.episode_count} episodes",
                f"{snap.people_count} relationships",
                f"{n_telegram_routes} telegram route(s)",
            ],
        ),
        _status_section(
            "Cognition",
            [
                f"History compression <code>{'enabled' if hc.enabled else 'disabled'}</code>",
                f"Compression threshold {hc.compaction_threshold_ratio:.2f} · target {hc.compaction_target_ratio:.2f}",
                f"Rolling messages cap {cfg.cognition.rolling_history_max_messages}",
            ],
        ),
        _status_section(
            "Memory pipeline",
            [
                f"Distillation <code>{'enabled' if dist.enabled else 'disabled'}</code> · cycle {int(dist.cycle_seconds)}s",
                f"Daemon / automations <code>{'running' if daemon_running else 'inactive'}</code>",
                f"State hydration <code>{hydrate}</code> · SOMA DB restore <code>{soma_restore}</code>",
            ],
        ),
        _status_section(
            "Runtime",
            [
                f"Model <code>{html.escape(snap.reflex_model)}</code>",
                f"Context window {snap.max_context_tokens // 1000}k tokens",
                f"Tools — {snap.tool_count} active",
            ],
        ),
    ]
    header = f"<b>{en}</b> · <code>v{html.escape(app_version)}</code>"
    return f"{header}\n\n" + "\n\n".join(sections)


def format_memories_html(entity_name: str, entries: list[Any], *, requested_count: int | None = None) -> str:
    _ = entity_name
    want = max(1, int(requested_count or len(entries) or 5))
    if not entries:
        return (
            "<b>Memories</b>\n\n"
            "<i>No episodic memories yet.</i>\n"
            "Talk with me a bit more, then try <code>/memories 5</code>."
        )
    lines = [
        "<b>Memories</b>",
        f"<i>Showing {len(entries)} of {want} requested.</i>",
        "",
    ]
    sep = "<code>────────────────────</code>"
    for i, row in enumerate(entries, 1):
        if isinstance(row, dict):
            kind = str(row.get("kind") or "memory").strip().lower()
            title = str(row.get("title") or kind or "memory").strip()
            body = str(row.get("body") or "").strip()
            summary = f"{title}: {body}" if body else title
            ts_raw = row.get("timestamp")
            try:
                ago = _relative_time(float(ts_raw)) if ts_raw else ""
            except (TypeError, ValueError):
                ago = ""
            sig = None
            type_label = kind
        elif isinstance(row, str):
            summary = row
            ago = ""
            sig = None
            type_label = "memory"
        else:
            summary = str(getattr(row, "summary", "") or "")
            ago = _relative_time(getattr(row, "timestamp", None))
            sig_raw = getattr(row, "significance", None)
            try:
                sig = float(sig_raw) if sig_raw is not None else None
            except (TypeError, ValueError):
                sig = None
            type_label = "episode"
        summary = summary.strip()
        if not summary:
            continue
        meta_parts: list[str] = []
        meta_parts.append(type_label)
        if ago:
            meta_parts.append(ago)
        if sig is not None:
            meta_parts.append(f"sig {sig:.2f}")
        meta = f"<i>{' · '.join(meta_parts)}</i>" if meta_parts else "<i>time unknown</i>"
        lines.append(f"<b>#{i}</b> {meta}")
        lines.append(html.escape(summary[:500]))
        if i < len(entries):
            lines.append(sep)
            lines.append("")
    return "\n".join(lines)


def format_feelings_html(entity_name: str, state: Any) -> str:
    primary = html.escape(state.primary.value)
    pi = f"{state.intensity:.1f}"
    ago = _fmt_duration(max(0.0, time.time() - state.last_transition))
    lines = [
        "<b>Feelings</b>\n",
        f"  Primary: <b>{primary}</b> ({pi})",
    ]
    if state.secondary is not None:
        lines.append(
            f"  Undercurrent: {html.escape(state.secondary.value)} "
            f"({state.secondary_intensity:.1f})"
        )
    lines.append(f"  Duration: {html.escape(ago)}")
    return "\n".join(lines)


def _mini_bar(value: float, width: int = 8) -> str:
    """Tiny visual bar for 0.0-1.0 values."""
    v = max(0.0, min(1.0, float(value)))
    filled = round(width * v)
    return "\u2588" * filled + "\u2591" * (width - filled)


def _relationship_band(v: float, *, bipolar: bool = False) -> str:
    """Readable bucket labels for relationship dimensions."""
    x = max(-1.0, min(1.0, float(v)))
    if bipolar:
        if x <= -0.5:
            return "negative"
        if x < -0.15:
            return "cool"
        if x < 0.2:
            return "neutral"
        if x < 0.5:
            return "warm"
        return "very warm"
    if x < 0.2:
        return "low"
    if x < 0.45:
        return "developing"
    if x < 0.7:
        return "solid"
    if x < 0.9:
        return "high"
    return "very high"


def _relationship_readiness(rel: Any) -> tuple[str, list[str]]:
    """Return (readiness label, caveats) to explain model quality."""
    caveats: list[str] = []
    n = int(getattr(rel, "interaction_count", 0) or 0)
    fam = float(getattr(rel, "familiarity", 0.0) or 0.0)
    warm = float(getattr(rel, "warmth", 0.0) or 0.0)
    trust = float(getattr(rel, "trust", 0.0) or 0.0)
    if n < 10:
        caveats.append("low sample size")
    if fam > 0.9 and n < 80:
        caveats.append("high familiarity with few logged turns — may reflect recent contact")
    if abs(warm - trust) >= 0.45:
        caveats.append("warmth/trust divergence")
    if n >= 120 and trust < 0.35 and warm > 0.75:
        caveats.append("trust likely under-updated")
    if n >= 120 and fam >= 0.98:
        readiness = "high-history profile"
    elif n >= 30:
        readiness = "stable profile"
    else:
        readiness = "forming profile"
    return readiness, caveats


def format_me_html(
    entity_name: str,
    relationship: Any | None,
    *,
    target_label: str | None = None,
    relational_doc: Any | None = None,
    somatic_marker: dict[str, float] | None = None,
) -> str:
    """Premium at-a-glance relationship card for /me.

    If a ``relational_doc`` (RelationalDocument) is provided, the card shows
    all 5 derived scores, health, stage, trajectory, significant moments,
    and somatic markers. Falls back to legacy 3-bar display when no doc exists.
    """
    _ = entity_name
    who = html.escape((target_label or "you").strip() or "you")
    sep = "<code>────────────────────</code>"

    if relationship is None and relational_doc is None:
        return (
            f"<b>Relationship · {who}</b>\n\n"
            "<i>No relationship profile yet.</i>\n"
            "If you just started chatting, send a few messages and check again."
        )

    # --- Use relational document if available for the rich card ---
    if relational_doc is not None:
        from ngram.memory.relational_document import compute_health_snapshot

        ds = relational_doc.derived_scores
        snap = compute_health_snapshot(relational_doc)
        readiness, caveats = _relationship_readiness(relationship) if relationship else ("forming profile", [])

        # Header with stage emoji
        out = f"{snap['stage_emoji']} <b>Relationship · {who}</b>\n\n"

        # Stage + health + trajectory line
        out += (
            f"<b>Stage</b>: {html.escape(snap['stage'])} · "
            f"<b>Health</b>: {html.escape(snap['health_label'])} ({snap['health']:.0%}) · "
            f"<b>Trend</b>: {snap['trajectory_emoji']} {html.escape(snap['trajectory'])}\n"
        )
        out += f"<b>Cadence</b>: {html.escape(snap['cadence_label'])}\n"
        if relationship:
            out += f"<b>Dynamic</b>: {html.escape(str(relationship.dynamic) or 'forming')}\n"
        out += f"{sep}\n"

        # All 5 derived scores
        fam = float(ds.get("familiarity", 0.2))
        warmth = float(ds.get("warmth", 0.5))
        trust = float(ds.get("trust", 0.35))
        tension = float(ds.get("tension", 0.1))
        investment = float(ds.get("investment", 0.4))

        out += (
            f"<b>Signals</b>\n"
            f"Familiarity  <code>{_mini_bar(fam)}</code> {fam:.2f} · {_relationship_band(fam)}\n"
            f"Warmth       <code>{_mini_bar(warmth)}</code> {warmth:.2f} · {_relationship_band(warmth)}\n"
            f"Trust        <code>{_mini_bar(trust)}</code> {trust:.2f} · {_relationship_band(trust)}\n"
            f"Tension      <code>{_mini_bar(tension)}</code> {tension:.2f} · {_relationship_band(tension)}\n"
            f"Investment   <code>{_mini_bar(investment)}</code> {investment:.2f} · {_relationship_band(investment)}\n"
        )
        out += f"{sep}\n"

        # History
        first_met_ago = ""
        last_seen_ago = ""
        if relationship:
            first_met_ago = _fmt_duration(max(0.0, time.time() - float(relationship.first_met)))
            last_seen_ago = _fmt_duration(max(0.0, time.time() - float(relationship.last_interaction)))
        elif relational_doc.created_at > 0:
            first_met_ago = _fmt_duration(max(0.0, time.time() - relational_doc.created_at))
            last_seen_ago = _fmt_duration(max(0.0, time.time() - relational_doc.last_interaction))
        n = relational_doc.interaction_count
        if relationship:
            n = max(n, relationship.interaction_count)
        out += "<b>History</b>\n"
        out += f"Interactions: {n}\n"
        if first_met_ago:
            out += f"First met: {html.escape(first_met_ago)} ago\n"
        if last_seen_ago:
            out += f"Last seen: {html.escape(last_seen_ago)} ago\n"
        if snap["days_known"] > 0:
            out += f"Known for: {html.escape(_fmt_duration(snap['days_known'] * 86400))}\n"

        # Somatic marker (gut reaction)
        if somatic_marker:
            effects = []
            for bar_name, delta in somatic_marker.items():
                sign = "+" if delta >= 0 else ""
                effects.append(f"{bar_name} {sign}{delta:.0f}")
            out += "\n<b>Body response</b> (gut reaction)\n"
            out += f"<code>{' · '.join(effects)}</code>\n"

        # Significant moments
        moments = list(relational_doc.significant_moments or [])
        if moments:
            out += "\n<b>Significant moments</b>\n"
            for m in moments[-4:]:
                out += f"• {html.escape(str(m).strip()[:120])}\n"

        # Shared topics (from legacy rel)
        if relationship:
            top_topics = [str(t).strip() for t in list(getattr(relationship, "topics_shared", []) or []) if str(t).strip()]
            if top_topics:
                out += "\n<b>Shared topics</b>\n"
                for t in top_topics[:5]:
                    out += f"• {html.escape(t[:90])}\n"

        # Caveats
        if caveats:
            out += "\n<b>Model caveats</b>\n"
            for c in caveats:
                out += f"• {html.escape(c)}\n"

        return out.rstrip()

    # --- Fallback: legacy 3-bar display (no relational document) ---
    first_met_ago = _fmt_duration(max(0.0, time.time() - float(relationship.first_met)))
    last_seen_ago = _fmt_duration(max(0.0, time.time() - float(relationship.last_interaction)))

    fam_bar = _mini_bar(relationship.familiarity)
    warm_bar = _mini_bar(relationship.warmth)
    trust_bar = _mini_bar(relationship.trust)
    readiness, caveats = _relationship_readiness(relationship)
    top_topics = [str(t).strip() for t in list(getattr(relationship, "topics_shared", []) or []) if str(t).strip()]
    unresolved = [str(u).strip() for u in list(getattr(relationship, "unresolved", []) or []) if str(u).strip()]

    out = (
        f"🌱 <b>Relationship · {who}</b>\n\n"
        f"<b>Profile quality</b>: {html.escape(readiness)}\n"
        f"<b>Dynamic</b>: {html.escape(str(relationship.dynamic) or 'forming')}\n"
        f"{sep}\n"
        f"<b>Signals</b>\n"
        f"Familiarity  <code>{fam_bar}</code> {relationship.familiarity:.2f} · {_relationship_band(relationship.familiarity)}\n"
        f"Warmth      <code>{warm_bar}</code> {relationship.warmth:.2f} · {_relationship_band(relationship.warmth, bipolar=True)}\n"
        f"Trust       <code>{trust_bar}</code> {relationship.trust:.2f} · {_relationship_band(relationship.trust)}\n"
        f"<i>Familiarity drifts down during long gaps and rebuilds on contact (slow half-life).</i>\n"
        f"{sep}\n"
        f"<b>History</b>\n"
        f"Interactions: {relationship.interaction_count}\n"
        f"First met: {html.escape(first_met_ago)} ago\n"
        f"Last seen: {html.escape(last_seen_ago)} ago"
    )
    if somatic_marker:
        effects = []
        for bar_name, delta in somatic_marker.items():
            sign = "+" if delta >= 0 else ""
            effects.append(f"{bar_name} {sign}{delta:.0f}")
        out += "\n\n<b>Body response</b> (gut reaction)\n"
        out += f"<code>{' · '.join(effects)}</code>"
    if top_topics:
        out += "\n\n<b>Shared topics</b>\n"
        for t in top_topics[:5]:
            out += f"\n• {html.escape(t[:90])}"
    if unresolved:
        out += "\n\n<b>Open threads</b>\n"
        for u in unresolved[:5]:
            out += f"\n• {html.escape(u[:120])}"
    if caveats:
        out += "\n\n<b>Model caveats</b>\n"
        for c in caveats:
            out += f"\n• {html.escape(c)}"
    return out


def format_relationship_html(
    entity_name: str,
    relationship: Any | None,
    relational_doc: Any | None,
    *,
    target_label: str | None = None,
    somatic_marker: dict[str, float] | None = None,
) -> str:
    """Deep-dive relationship card for /relationship — shows the full prose portrait."""
    _ = entity_name
    who = html.escape((target_label or "someone").strip() or "someone")
    sep = "<code>────────────────────</code>"

    if relational_doc is None and relationship is None:
        return (
            f"<b>Relationship · {who}</b>\n\n"
            "<i>No relationship data found.</i>\n"
            "Try chatting first, then use <code>/relationship</code> again."
        )

    # If we have a relational doc, use the rich view
    if relational_doc is not None:
        from ngram.memory.relational_document import compute_health_snapshot

        ds = relational_doc.derived_scores
        snap = compute_health_snapshot(relational_doc)

        # Header
        out = f"{snap['stage_emoji']} <b>{who}</b> · {html.escape(snap['stage'])}\n"
        out += f"{sep}\n\n"

        # Health + trajectory banner
        out += (
            f"<b>Health</b>: {html.escape(snap['health_label'])} ({snap['health']:.0%}) · "
            f"<b>Trend</b>: {snap['trajectory_emoji']} {html.escape(snap['trajectory'])} · "
            f"<b>Cadence</b>: {html.escape(snap['cadence_label'])}\n"
        )
        if relationship:
            out += f"<b>Dynamic</b>: {html.escape(str(relationship.dynamic) or 'forming')}\n"
        out += "\n"

        # All 5 bars
        fam = float(ds.get("familiarity", 0.2))
        warmth = float(ds.get("warmth", 0.5))
        trust = float(ds.get("trust", 0.35))
        tension = float(ds.get("tension", 0.1))
        investment = float(ds.get("investment", 0.4))

        out += (
            f"<b>Derived signals</b>\n"
            f"Familiarity  <code>{_mini_bar(fam, 10)}</code> {fam:.2f} · {_relationship_band(fam)}\n"
            f"Warmth       <code>{_mini_bar(warmth, 10)}</code> {warmth:.2f} · {_relationship_band(warmth)}\n"
            f"Trust        <code>{_mini_bar(trust, 10)}</code> {trust:.2f} · {_relationship_band(trust)}\n"
            f"Tension      <code>{_mini_bar(tension, 10)}</code> {tension:.2f} · {_relationship_band(tension)}\n"
            f"Investment   <code>{_mini_bar(investment, 10)}</code> {investment:.2f} · {_relationship_band(investment)}\n"
        )
        out += f"{sep}\n\n"

        # Somatic marker
        if somatic_marker:
            effects = []
            for bar_name, delta in somatic_marker.items():
                sign = "+" if delta >= 0 else ""
                effects.append(f"{bar_name} {sign}{delta:.0f}")
            out += "<b>Body response</b> (gut reaction on contact)\n"
            out += f"<code>{' · '.join(effects)}</code>\n\n"

        # History block
        out += "<b>History</b>\n"
        n = relational_doc.interaction_count
        if relationship:
            n = max(n, relationship.interaction_count)
        out += f"Interactions: {n}\n"
        if relationship:
            first_met_ago = _fmt_duration(max(0.0, time.time() - float(relationship.first_met)))
            last_seen_ago = _fmt_duration(max(0.0, time.time() - float(relationship.last_interaction)))
            out += f"First met: {html.escape(first_met_ago)} ago\n"
            out += f"Last seen: {html.escape(last_seen_ago)} ago\n"
        if snap["days_known"] > 0:
            out += f"Known for: {html.escape(_fmt_duration(snap['days_known'] * 86400))}\n"
        out += f"{sep}\n\n"

        # Significant moments timeline
        moments = list(relational_doc.significant_moments or [])
        if moments:
            out += "<b>Significant moments</b>\n"
            for i, m in enumerate(moments[-6:], 1):
                out += f"  {i}. {html.escape(str(m).strip()[:160])}\n"
            out += "\n"

        # Shared topics
        if relationship:
            top_topics = [str(t).strip() for t in list(getattr(relationship, "topics_shared", []) or []) if str(t).strip()]
            if top_topics:
                out += "<b>Shared topics</b>\n"
                for t in top_topics[:6]:
                    out += f"• {html.escape(t[:100])}\n"
                out += "\n"

        # Prose portrait (trimmed)
        doc_text = (relational_doc.document or "").strip()
        if doc_text:
            out += f"<b>Portrait</b>\n{sep}\n"
            # Show last ~2000 chars
            if len(doc_text) > 2000:
                doc_text = "…\n" + doc_text[-2000:]
            out += html.escape(doc_text)
            out += f"\n{sep}\n"

        # Caveats
        if relationship:
            _, caveats = _relationship_readiness(relationship)
            if caveats:
                out += "\n<b>Model caveats</b>\n"
                for c in caveats:
                    out += f"• {html.escape(c)}\n"

        return out.rstrip()

    # Fallback when no relational doc — just show legacy info
    return format_me_html(
        entity_name,
        relationship,
        target_label=target_label,
        somatic_marker=somatic_marker,
    )



def format_models_html(entity: "Entity") -> str:
    cfg = entity.config
    think = "on" if cfg.cognition.thinking_mode else "off"
    return (
        "<b>Models</b>\n\n"
        f"  Reflex: <code>{html.escape(cfg.cognition.reflex_model)}</code>\n"
        f"  Deliberate: <code>{html.escape(cfg.cognition.deliberate_model)}</code>\n"
        f"  Embedding: <code>{html.escape(cfg.harness.models.embedding)}</code>\n\n"
        f"<b>Settings</b>\n\n"
        f"  Thinking mode: {html.escape(think)}\n"
        f"  Context window: {cfg.effective_context_tokens():,} tokens\n"
        f"  Tools registered: {len(entity.tools.openai_tools())}"
    )


def _relative_time(ts: float | None) -> str:
    """Unix time (seconds) → shortest relative label for a past instant."""
    if ts is None or ts <= 0:
        return "—"
    delta = max(0.0, time.time() - float(ts))
    if delta < 45:
        return "just now"
    if delta < 3600:
        m = max(1, int(delta // 60))
        return f"{m}m ago"
    if delta < 86400:
        h = max(1, int(delta // 3600))
        return f"{h}h ago"
    if delta < 7 * 86400:
        d = max(1, int(delta // 86400))
        return f"{d}d ago"
    w = max(1, int(delta // (7 * 86400)))
    return f"{w}w ago"


def _routine_emoji(automation: dict[str, Any], is_paused: bool) -> str:
    if is_paused:
        return "⏸"
    origin = str(automation.get("origin", "") or "").strip().lower()
    name = str(automation.get("name", "") or "")
    desc = str(automation.get("description", "") or "")
    text = f"{name} {desc}".lower()
    if origin == "internal":
        return "📓"
    if origin == "user":
        return "⏰"
    # self-created
    if any(k in text for k in ("check in", "check-in", "reach out", "how they", "deadline")):
        return "💬"
    if any(k in text for k in ("reddit", "browse", "subreddit", "r/")):
        return "🔶"
    if any(k in text for k in ("watch", "monitor")):
        return "👀"
    if any(k in text for k in ("news", "headline")):
        return "⏰"
    if any(k in text for k in ("reflect", "journal")):
        return "📓"
    return "⏰"


def _possessive_entity_title(entity_name: str) -> str:
    n = (entity_name or "").strip() or "Entity"
    esc = html.escape(n)
    if n.lower().endswith("s"):
        return f"{esc}'"
    return f"{esc}'s"


def _origin_display_label(r: Any) -> str:
    o = getattr(r, "origin", None)
    v = o.value if o is not None and hasattr(o, "value") else str(o or "").strip().lower()
    if v == "internal":
        return "Internal"
    if v == "self":
        return "Self-created"
    if v == "user":
        return "User-created"
    return v[:1].upper() + v[1:] if v else "Unknown"


def _delivery_platform_label(r: Any) -> str | None:
    raw = getattr(r, "deliver_to", None)
    plat_attr = getattr(r, "deliver_platform", None)
    s = (str(raw).strip() if raw else "") or ""
    plat = ""
    if ":" in s:
        plat = s.split(":", 1)[0].strip().lower()
    elif plat_attr:
        plat = str(plat_attr).strip().lower()
    if plat == "telegram":
        return "Telegram"
    if plat == "discord":
        return "Discord"
    if plat == "cli":
        return "CLI"
    return None


def _automation_to_emoji_dict(r: Any) -> dict[str, Any]:
    o = getattr(r, "origin", None)
    ov = o.value if o is not None and hasattr(o, "value") else str(o or "")
    return {
        "origin": ov,
        "name": str(getattr(r, "name", "") or ""),
        "description": str(getattr(r, "description", "") or ""),
    }


def _format_one_routine_html(r: Any) -> str:
    enabled = bool(getattr(r, "enabled", False))
    is_paused = not enabled
    name_raw = str(getattr(r, "name", "") or "").strip() or "Untitled routine"
    natural = (getattr(r, "schedule_natural", None) or "").strip() or "Schedule not set"
    desc = (getattr(r, "description", None) or "").strip()
    runs = int(getattr(r, "run_count", 0) or 0)
    last_run = getattr(r, "last_run", None)
    lr = float(last_run) if last_run is not None else None

    emoji = _routine_emoji(_automation_to_emoji_dict(r), is_paused)
    title = f"<b>{emoji}  {html.escape(name_raw)}</b>"

    delivery = _delivery_platform_label(r)
    origin_lbl = _origin_display_label(r)
    if delivery:
        sched_line = f"    {html.escape(natural)} → {delivery}"
    else:
        sched_line = f"    {html.escape(natural)} · {origin_lbl}"
    if is_paused:
        sched_line = f"{sched_line} · Paused"

    if runs == 0:
        stats_line = "    Never run"
    else:
        last_part = _relative_time(lr) if lr else "—"
        ran_what = "Ran 1 time" if runs == 1 else f"Ran {runs} times"
        stats_line = f"    {ran_what} · Last: {last_part}"

    o = getattr(r, "origin", None)
    ov = o.value if o is not None and hasattr(o, "value") else str(o or "")
    show_desc = bool(desc) and ov in ("user", "self")
    desc_block = ""
    if show_desc:
        short = desc if len(desc) <= 80 else desc[:77] + "…"
        desc_block = f"\n\n    \"{html.escape(short)}\""

    return f"{title}\n{sched_line}\n{stats_line}{desc_block}"


def format_routines_html(
    entity_name: str,
    routines: list[Any],
    *,
    automations_enabled: bool,
    scheduler_ready: bool,
) -> str:
    """Render saved automations (routines) for Telegram HTML — scannable, no raw cron."""
    poss = _possessive_entity_title(entity_name)
    header = f"<b>{poss} Routines</b>"

    if not routines:
        body = (
            f"{header}\n\n"
            "No routines yet.\n\n"
            "Your entity can create its own — or ask it\n"
            "to set one up for you."
        )
    else:
        n_active = sum(1 for r in routines if getattr(r, "enabled", False))
        n_paused = len(routines) - n_active

        def _sort_key(r: Any) -> tuple[int, float]:
            en = bool(getattr(r, "enabled", False))
            nxt = getattr(r, "next_run", None)
            try:
                nxt_f = float(nxt) if nxt is not None else float("inf")
            except (TypeError, ValueError):
                nxt_f = float("inf")
            return (0 if en else 1, nxt_f)

        ordered = sorted(routines, key=_sort_key)
        blocks = [_format_one_routine_html(r) for r in ordered]
        body = f"{header}\n\n{n_active} active · {n_paused} paused\n\n" + "\n\n".join(blocks)

    footers: list[str] = []
    if not automations_enabled:
        footers.append(
            "ℹ️ Automations are configured but scheduling\n"
            "   is disabled in this entity's config."
        )
    if not scheduler_ready:
        footers.append(
            "ℹ️ Routines are saved but won't fire until\n"
            "   the daemon is running (ngram run)."
        )
    if footers:
        body = body + "\n\n" + "\n\n".join(footers)
    return body


def format_tools_html(
    entity: "Entity",
    *,
    page: int = 0,
    per_page: int = 12,
    query: str | None = None,
) -> tuple[str, int, int]:
    """Paginated codeblock table of active tools.

    Returns ``(html_body, page_index, total_pages)`` — same shape as
    ``format_commands_page`` for handler consistency.
    """
    rows: list[tuple[str, str]] = []
    list_fn = getattr(entity.tools, "list_tools", None)
    if callable(list_fn):
        rows = list_fn()
    else:
        for t in entity.tools.openai_tools():
            fn = t.get("function", {}) if isinstance(t, dict) else {}
            rows.append(
                (
                    str(fn.get("name") or ""),
                    str(fn.get("description") or ""),
                )
            )
        rows = sorted([r for r in rows if r[0]], key=lambda r: r[0])

    # Optional search filter
    q = (query or "").strip().lower()
    if q:
        rows = [r for r in rows if q in r[0].lower() or q in r[1].lower()]

    total_tools = len(rows)
    if not rows:
        body = "<b>Active tools</b>\n\n"
        if q:
            body += f"No tools matching <code>{html.escape(query or '')}</code>.\n"
            body += "Try <code>/tools</code> for the full list."
        else:
            body += "No tools are currently registered."
        return body, 0, 1

    # Pagination
    total_pages = max(1, (len(rows) + per_page - 1) // per_page)
    page = max(0, min(page, total_pages - 1))
    start = page * per_page
    page_rows = rows[start : start + per_page]

    # Compute column width for alignment
    name_w = max(len(r[0]) for r in page_rows)
    name_w = max(name_w, 4)  # minimum "Name" header width
    # Description gets remaining space; truncate to fit Telegram <pre> nicely
    desc_max = 42

    # Build table
    header = f"{'Tool':<{name_w}}  {'Description'}"
    rule = "─" * name_w + "──" + "─" * min(desc_max, max(11, max(len(r[1][:desc_max]) for r in page_rows)))
    table_lines = [header, rule]
    for name, desc in page_rows:
        desc_display = desc.strip()
        if len(desc_display) > desc_max:
            desc_display = desc_display[: desc_max - 1] + "…"
        table_lines.append(f"{name:<{name_w}}  {desc_display}")

    title = f"<b>Active tools</b> · {total_tools} total — page {page + 1}/{total_pages}"
    if q:
        title += f" · filter <code>{html.escape(query or '')}</code>"

    pre_block = "\n".join(table_lines)
    body = f"{title}\n\n<pre>{html.escape(pre_block)}</pre>"

    # Navigation hint
    if q:
        body += "\n\n<i>Next page:</i> <code>/tools {} {}</code>".format(
            page + 2 if page + 1 < total_pages else 1,
            html.escape(query or ""),
        )
    else:
        body += "\n\n<i>Next page:</i> <code>/tools {}</code>".format(
            page + 2 if page + 1 < total_pages else 1,
        )

    return body, page, total_pages


def format_body_md_reply_html_chunks(
    raw: str,
    *,
    rel_path: str = "soma/body.md",
    chunk_chars: int = 2500,
) -> list[str]:
    """HTML fragments for Telegram: raw ``body.md`` in one or more ``<pre>`` blocks (not via the model)."""
    raw = raw if raw is not None else ""
    lp = html.escape(rel_path.strip() or "soma/body.md")
    if not raw:
        return [f"<b>{lp}</b> <i>(empty file)</i>"]
    n = (len(raw) + chunk_chars - 1) // chunk_chars
    out: list[str] = []
    for i, start in enumerate(range(0, len(raw), chunk_chars)):
        chunk = raw[start : start + chunk_chars]
        suffix = f" ({i + 1}/{n})" if n > 1 else ""
        out.append(f"<b>{lp}</b>{suffix}\n<pre>{html.escape(chunk)}</pre>")
    return out


_JOURNAL_ENTRY_RE = re.compile(
    r"^##[ \t]+(?P<stamp>\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2})?)?)(?P<meta>[^\r\n]*)$",
    re.MULTILINE,
)


def _parse_journal_entries(raw: str) -> list[tuple[str, tuple[str, ...], str]]:
    """Read the append-only journal format without involving the cognition model."""
    text = raw or ""
    matches = list(_JOURNAL_ENTRY_RE.finditer(text))
    if not matches:
        body = re.sub(r"^\s*#\s+journal\s*", "", text, count=1, flags=re.IGNORECASE).strip()
        return [("", (), body)] if body else []

    entries: list[tuple[str, tuple[str, ...], str]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = text[match.end() : end].strip()
        tags = tuple(
            tag[:32]
            for tag in re.findall(r"(?<!\w)#([\w-]+)", match.group("meta") or "")
            if tag
        )
        entries.append((match.group("stamp"), tags, body))
    return entries


def _format_journal_stamp(stamp: str) -> str:
    if not stamp:
        return "Undated entry"
    for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            value = datetime.strptime(stamp.replace("T", " "), pattern)
        except ValueError:
            continue
        date = f"{value.strftime('%B')} {value.day}, {value.year}"
        if pattern == "%Y-%m-%d":
            return date
        return f"{date} · {value.strftime('%I:%M %p').lstrip('0')}"
    return stamp


def _journal_body_html(raw: str) -> str:
    """Render a small, safe Markdown subset suited to prose journal entries."""
    lines: list[str] = []
    for source in (raw or "").splitlines():
        heading = re.match(r"^\s*#{1,6}\s+(.+?)\s*$", source)
        bullet = re.match(r"^\s*[-*]\s+(.+?)\s*$", source)
        quote = re.match(r"^\s*>\s?(.*?)\s*$", source)
        if heading:
            lines.append(f"<b>{html.escape(heading.group(1))}</b>")
        elif bullet:
            lines.append(f"• {html.escape(bullet.group(1))}")
        elif quote:
            lines.append(f"<i>{html.escape(quote.group(1))}</i>")
        elif source.strip() in {"---", "***"}:
            lines.append("────────────")
        else:
            lines.append(html.escape(source))
    return "\n".join(lines).strip()


def _split_journal_body(raw: str, *, max_html_chars: int) -> list[str]:
    """Split prose on readable boundaries while accounting for HTML escaping growth."""
    rest = (raw or "").strip()
    if not rest:
        return [""]
    parts: list[str] = []
    while rest:
        if len(_journal_body_html(rest)) <= max_html_chars:
            parts.append(rest)
            break

        low, high, best = 1, len(rest), 1
        while low <= high:
            mid = (low + high) // 2
            if len(_journal_body_html(rest[:mid])) <= max_html_chars:
                best = mid
                low = mid + 1
            else:
                high = mid - 1

        cut = best
        for boundary in ("\n\n", "\n", " "):
            candidate = rest.rfind(boundary, 0, best + 1)
            if candidate >= max(1, best // 3):
                cut = candidate
                break
        piece = rest[:cut].rstrip()
        if not piece:
            piece = rest[:best]
            cut = best
        parts.append(piece)
        rest = rest[cut:].lstrip()
    return parts


def format_journal_html_chunks(
    raw: str,
    entity_name: str,
    *,
    requested_count: int = 5,
    chunk_chars: int = 3800,
) -> list[str]:
    """Render recent journal entries newest-first as safe, readable Telegram HTML."""
    limit = max(1000, min(4000, int(chunk_chars)))
    want = max(1, min(12, int(requested_count or 5)))
    possessive = _possessive_entity_title(entity_name)
    entries = _parse_journal_entries(raw)
    title = f"✦ <b>{possessive} journal</b>"

    if not entries:
        return [
            f"{title}\n\n"
            "<i>No journal entries yet.</i>\n\n"
            "This journal will begin when the first reflection is written."
        ]

    selected = list(reversed(entries[-want:]))
    count_label = "entry" if len(entries) == 1 else "entries"
    header = (
        f"{title}\n"
        f"<i>Private reflections · newest first · showing {len(selected)} of "
        f"{len(entries)} {count_label}</i>"
    )

    blocks: list[str] = []
    body_budget = max(200, limit - 700)
    for stamp, tags, body in selected:
        pieces = _split_journal_body(body, max_html_chars=body_budget)
        for piece_index, piece in enumerate(pieces):
            stamp_label = html.escape(_format_journal_stamp(stamp))
            if piece_index:
                stamp_label += " · continued"
            lines = [f"<b>{stamp_label}</b>"]
            if tags and piece_index == 0:
                shown_tags = tags[:8]
                tag_line = " ".join(f"<code>#{html.escape(tag)}</code>" for tag in shown_tags)
                if len(tags) > len(shown_tags):
                    tag_line += f" <i>+{len(tags) - len(shown_tags)} more</i>"
                lines.append(tag_line)
            lines.append("")
            lines.append(_journal_body_html(piece) or "<i>(empty entry)</i>")
            blocks.append("\n".join(lines))

    separator = "\n\n────────────\n\n"
    chunks: list[str] = []
    current = header
    continuation = f"<b>{possessive} journal · continued</b>"
    for block in blocks:
        addition = separator + block
        if len(current) + len(addition) <= limit:
            current += addition
            continue
        chunks.append(current)
        current = f"{continuation}\n\n{block}"
    if current:
        chunks.append(current)
    return chunks


def format_ping_html(app_version: str) -> str:
    return (
        f"pong · ngram v{html.escape(app_version)}\n"
        f"server_time_unix · <code>{int(time.time())}</code>"
    )


def format_version_html(
    app_version: str,
    *,
    commit_short: str | None = None,
    commit_subject: str | None = None,
) -> str:
    """Harness version plus optional git HEAD (when this install is a git checkout)."""
    lines = [f"<b>ngram</b> <code>v{html.escape(app_version)}</code>"]
    if commit_subject:
        subj = html.escape(commit_subject.strip())
        if len(subj) > 280:
            subj = subj[:279].rstrip() + "…"
        if commit_short:
            lines.append(f"<code>{html.escape(commit_short)} · {subj}</code>")
        else:
            lines.append(f"<code>{subj}</code>")
    return "\n".join(lines)


def format_wakequiet_status_html(
    *,
    quiet_db: bool,
    yaml_status_mirror: bool,
    yaml_tools_mirror: bool,
) -> str:
    """After /wakequiet: whether transcript-only mode is forcing wake lines off Telegram."""
    ys = "true" if yaml_status_mirror else "false"
    yt = "true" if yaml_tools_mirror else "false"
    if quiet_db:
        return (
            "<b>Wake quiet mode: on</b> — autonomous wake status lines and per-tool activity lines "
            "are <i>not</i> sent to Telegram; they still append to the autonomy transcript.\n"
            "<code>/wakequiet off</code> to follow your entity YAML again."
        )
    return (
        "<b>Wake quiet mode: off</b> — mirroring follows entity YAML "
        f"(<code>wake_user_visible_status</code>={ys}, <code>wake_chat_tool_activity</code>={yt}).\n"
        "<code>/wakequiet on</code> to keep those lines out of chat (transcript only)."
    )


def format_busy_status_html(*, enabled: bool) -> str:
    """After /busy: whether the pinned monospace working line is on for this chat."""
    if enabled:
        return (
            "<b>Busy indicator: on</b> for this chat — the pinned working line shows while I reply.\n"
            "<code>/busy off</code> to hide it."
        )
    return (
        "<b>Busy indicator: off</b> for this chat — no pinned working line.\n"
        "<code>/busy on</code> to show it again."
    )


def format_update_blocked_html(reason: str) -> str:
    r = html.escape((reason or "").strip() or "This process is not allowed to modify the install.")
    return (
        "<b>Update unavailable here</b>\n\n"
        f"{r}\n\n"
        "<i>Hybrid: use <code>/update</code> while talking to the Railway worker, or run "
        "<code>ngram update</code> on the machine that holds the clone. If execution is "
        "locked to Railway only, unset <code>tools.execution.require_railway</code> for a local runner "
        "or use <code>tools.execution.allow_local</code> for other local tools.</i>"
    )


def format_update_result_html(result: dict[str, Any], *, log_lines: list[str] | None = None) -> str:
    lines: list[str] = []
    if result.get("ok"):
        lines.append("<b>Update finished</b>\n")
        method = result.get("method")
        if method == "git":
            repo = html.escape(str(result.get("repo") or ""))
            if repo:
                lines.append(f"• Repo: <code>{repo}</code>")
            br = html.escape(str(result.get("before_ref") or "?"))
            ar = html.escape(str(result.get("after_ref") or "?"))
            lines.append(f"• Git: <code>{br}</code> → <code>{ar}</code>")
            for m in result.get("messages") or []:
                if str(m).strip():
                    lines.append(html.escape(str(m)))
            pip_r = result.get("pip")
            if isinstance(pip_r, dict) and pip_r.get("ok"):
                po = (pip_r.get("output") or "").strip()
                if po:
                    if len(po) > 2500:
                        po = po[:2500] + "\n…"
                    lines.append("<pre>" + html.escape(po) + "</pre>")
        elif method == "pip_git":
            po = (result.get("output") or "").strip()
            if po:
                if len(po) > 2500:
                    po = po[:2500] + "\n…"
                lines.append("<pre>" + html.escape(po) + "</pre>")
        lines.append(
            "\n<i>Restart this worker (or <code>ngram run</code>) so the process loads new code.</i>"
        )
    else:
        lines.append("<b>Update failed</b>\n")
        err = html.escape(str(result.get("error") or "unknown error"))
        lines.append(f"<code>{err}</code>")
    if log_lines:
        tail = "\n".join(log_lines).strip()
        if tail:
            if len(tail) > 1500:
                tail = tail[:1500] + "\n…"
            lines.append("\n<b>Log</b>\n<pre>" + html.escape(tail) + "</pre>")
    return "\n".join(lines)


def format_reset_html(entity_name: str) -> str:
    en = html.escape(entity_name)
    return (
        f"{en} cleared rolling chat turns for this runtime.\n\n"
        "Durable memories are untouched."
    )


def format_unknown_command(command_text: str) -> str:
    cmd = html.escape(command_text.strip() or "that command")
    return (
        f"I do not recognize <code>{cmd}</code>.\n\n"
        "Use <code>/commands</code> to browse everything, or <code>/help</code> for quick guidance."
    )


def format_media_unavailable(kind: str) -> str:
    k = html.escape(kind)
    return (
        f"I cannot process <b>{k}</b> in this channel yet.\n"
        "Try plain text or a <b>photo</b> (caption optional)."
    )


def format_access_denied() -> str:
    return (
        "This instance is locked to approved people.\n"
        "If you should have access, ask the operator to add your Telegram user id."
    )


def format_whoami_html(*, user_id: int, full_name: str, username: str | None) -> str:
    uid = html.escape(str(user_id))
    fn = html.escape(full_name.strip() or "—")
    un = f"@{html.escape(username.strip())}" if (username or "").strip() else "—"
    return (
        "<b>Your Telegram id</b>\n"
        f"<code>{uid}</code>\n\n"
        f"<b>Name</b> {fn}\n"
        f"<b>Username</b> {un}\n\n"
        "Share the id with the host: they can set <code>operator_user_ids</code> in entity YAML, "
        "set <code>NGRAM_TELEGRAM_OPERATOR_IDS</code> on the worker (Railway), "
        "or use <code>/privacy allow</code> once operators exist."
    )


def format_privacy_help_html() -> str:
    return (
        "<b>Privacy</b>\n\n"
        "<b>Read</b>\n"
        "• <code>/privacy</code> or <code>/privacy status</code>\n\n"
        "<b>Operators only</b> (YAML <code>operator_user_ids</code> and/or "
        "<code>NGRAM_TELEGRAM_OPERATOR_IDS</code>)\n"
        "• <code>/private on</code> / <code>/private off</code> — quick close or open the bot\n"
        "• <code>/privacy lock</code> — same as <code>/private on</code>\n"
        "• <code>/privacy allow &lt;id&gt;</code> — add a user id\n"
        "• <code>/privacy deny &lt;id&gt;</code> — remove a user id\n"
        "• <code>/privacy open</code> — same as <code>/private off</code>\n"
        "• <code>/privacy help</code>\n\n"
        "Use <code>/whoami</code> to see your numeric id."
    )


def format_private_usage_html() -> str:
    return (
        "<b>Private mode</b> (operators only)\n\n"
        "• <code>/private on</code> — only operators (and anyone you <code>/privacy allow</code>) can chat\n"
        "• <code>/private off</code> — public again (unless YAML <code>allowed_user_ids</code> is set)\n\n"
        "Same as <code>/privacy lock</code> and <code>/privacy open</code>."
    )


def format_privacy_operator_required_html() -> str:
    return (
        "Only configured <b>operators</b> can change privacy.\n"
        "The host must set operators: <code>operator_user_ids</code> in entity YAML and/or "
        "<code>NGRAM_TELEGRAM_OPERATOR_IDS</code> (comma-separated ids) on the worker."
    )


def format_privacy_no_operators_html() -> str:
    return (
        "Telegram privacy commands are disabled until the host sets operators:\n"
        "• <code>operator_user_ids: [ … ]</code> in entity YAML (committed <code>canary.example.yaml</code> "
        "for Railway builds), <b>or</b>\n"
        "• <code>NGRAM_TELEGRAM_OPERATOR_IDS</code> on the worker (comma-separated ids — "
        "best for Railway without committing your id).\n\n"
        "Local-only <code>canary.yaml</code> is not copied into Docker/Railway images.\n\n"
        "Use <code>/whoami</code> to read your id."
    )


def format_privacy_status_html(
    *,
    enforced: bool,
    allowed_ids: list[int],
    yaml_restricted: bool,
    operators_configured: bool,
) -> str:
    lines = [
        "<b>Privacy status</b>\n",
        f"• <b>DB lock</b>: {'<code>on</code>' if enforced else '<code>off</code>'}\n",
    ]
    if enforced:
        preview = ", ".join(html.escape(str(i)) for i in sorted(allowed_ids)[:24])
        if len(allowed_ids) > 24:
            preview += ", …"
        lines.append(f"• <b>Allowed user ids</b>: {preview or '—'}\n")
    lines.append(
        f"• <b>YAML allowlist</b>: {'<code>yes</code>' if yaml_restricted else '<code>no</code>'} "
        f"(ignored while DB lock is on)\n"
    )
    lines.append(
        f"• <b>Operators in YAML</b>: {'<code>yes</code>' if operators_configured else '<code>no</code>'}\n"
    )
    if not enforced and not yaml_restricted:
        lines.append("\nAnyone can use this bot until you <code>/privacy lock</code>.\n")
    return "".join(lines)


def format_privacy_locked_html(count: int) -> str:
    return (
        f"🔒 <b>Locked.</b> {count} Telegram user id(s) can use this bot.\n"
        "Use <code>/privacy allow</code> / <code>/deny</code> to adjust, or <code>/privacy open</code> to go public."
    )


def format_privacy_opened_html() -> str:
    return (
        "🔓 <b>Open.</b> Anyone can chat again "
        "(unless you still have a static <code>allowed_user_ids</code> list in YAML).\n"
        "Use <code>/privacy lock</code> to restrict."
    )


def format_privacy_allow_deny_usage_html() -> str:
    return "Usage: <code>/privacy allow &lt;telegram_user_id&gt;</code> or <code>/privacy deny &lt;id&gt;</code>"


def format_privacy_invalid_id_html() -> str:
    return "That doesn’t look like a numeric Telegram user id."


def format_privacy_cannot_deny_last_html() -> str:
    return "Refusing to remove the last allowed user while locked — use <code>/privacy open</code> first."


def format_session_disabled_html() -> str:
    return (
        "Remote desktop sessions are disabled for this entity.\n"
        "Enable <code>tools.remote_session.enabled: true</code> and point "
        "<code>tools.execution.base_url</code> at a Linux execution RPC host."
    )


def format_session_operator_required_html() -> str:
    return (
        "Only configured <b>operators</b> can start or stop remote desktop sessions in this chat.\n"
        "Set <code>operator_user_ids</code> in entity YAML and/or "
        "<code>NGRAM_TELEGRAM_OPERATOR_IDS</code> on the worker."
    )


def format_session_status_html(entity_name: str, session: dict[str, Any] | None) -> str:
    en = html.escape(entity_name)
    if not isinstance(session, dict) or not str(session.get("session_id") or "").strip():
        return (
            f"<b>{en} remote session</b>\n\n"
            "No active desktop session for this chat.\n"
            "Use <code>/session_start</code> to create one."
        )
    session_id = html.escape(str(session.get("session_id") or ""))
    status = html.escape(str(session.get("status") or "running") or "running")
    summary = html.escape(str(session.get("summary") or "").strip()[:500]) or "—"
    last_action = html.escape(str(session.get("last_action") or "").strip()[:200]) or "—"
    active_app = html.escape(str(session.get("active_app") or "").strip()[:200]) or "—"
    return (
        f"<b>{en} remote session</b>\n\n"
        f"<b>Session id</b> <code>{session_id}</code>\n"
        f"<b>Status</b> {status}\n"
        f"<b>Active app</b> {active_app}\n"
        f"<b>Last action</b> {last_action}\n"
        f"<b>Summary</b> {summary}\n\n"
        "The live screenshot card in chat should keep refreshing while the session is active."
    )


def format_remote_session_caption(entity_name: str, session: dict[str, Any] | None) -> str:
    en = (entity_name or "ngram").strip() or "ngram"
    if not isinstance(session, dict):
        return f"{en} remote session"
    status = str(session.get("status") or "running").strip() or "running"
    active_app = str(session.get("active_app") or "").strip()
    last_action = str(session.get("last_action") or "").strip()
    summary = str(session.get("summary") or "").strip()
    lines = [f"{en} remote session", f"Status: {status}"]
    if active_app:
        lines.append(f"App: {active_app[:120]}")
    if last_action:
        lines.append(f"Last action: {last_action[:120]}")
    if summary:
        lines.append(summary[:400])
    return "\n".join(lines)[:1024]
