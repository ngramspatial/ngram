"""Wall-clock helpers, entity-subjective spans, and session timing."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

# US Eastern (EST/EDT) when no explicit zone is set on common cloud hosts.
_DEFAULT_CLOUD_WALL_ZONE = "America/New_York"


def _running_on_cloud_default_wall_zone() -> bool:
    if (os.environ.get("RAILWAY_ENVIRONMENT") or "").strip():
        return True
    dm = (os.environ.get("NGRAM_DEPLOYMENT_MODE") or "").strip().lower()
    return dm == "hybrid_railway"


@dataclass
class EntityClock:
    """Tracks subjective session time alongside wall time."""

    session_start: float = field(default_factory=time.time)
    subjective_offset: float = 0.0

    def now(self) -> float:
        return time.time()

    def subjective_elapsed(self) -> float:
        return (time.time() - self.session_start) + self.subjective_offset


def parse_entity_created_timestamp(raw: object) -> float | None:
    """Parse optional entity YAML ``created:`` ISO string to epoch seconds."""
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


def display_timezone() -> ZoneInfo | None:
    """Resolve IANA zone: ``NGRAM_TIMEZONE``, else valid ``TZ``, else US Eastern on cloud, else ``None``."""
    for key in ("NGRAM_TIMEZONE", "TZ"):
        raw = (os.environ.get(key) or "").strip()
        if not raw or raw.startswith(":"):
            continue
        try:
            return ZoneInfo(raw)
        except Exception:
            continue
    if _running_on_cloud_default_wall_zone():
        return ZoneInfo(_DEFAULT_CLOUD_WALL_ZONE)
    return None


def wall_now_for_display() -> datetime:
    """Wall clock for prompts and tools: configured or cloud-default zone, else naive host local."""
    z = display_timezone()
    if z is not None:
        return datetime.now(timezone.utc).astimezone(z)
    return datetime.now()


def format_local_now_casual_lower() -> str:
    """e.g. right now it's friday, april 4, 2026, 4:30 pm (edt)."""
    now = wall_now_for_display()
    day = now.strftime("%A").lower()
    month = now.strftime("%B").lower()
    d = now.day
    y = now.year
    h12 = now.hour % 12 or 12
    mi = now.strftime("%M")
    ampm = now.strftime("%p").lower()
    tz_bit = ""
    if now.tzinfo is not None:
        abbr = (now.strftime("%Z") or "").strip().lower()
        if abbr:
            tz_bit = f" ({abbr})"
    return f"right now it's {day}, {month} {d}, {y}, {h12}:{mi} {ampm}{tz_bit}."


def format_wall_clock_tool_line() -> str:
    """Single line for ``get_current_time`` (optionally with zone abbreviation)."""
    now = wall_now_for_display()
    line = now.strftime("%A, %B %d, %Y at %I:%M %p")
    if now.tzinfo is not None:
        abbr = (now.strftime("%Z") or "").strip()
        if abbr:
            line = f"{line} ({abbr})"
    return line


def time_since_last_interaction(last_interaction: float, *, now: float | None = None) -> str:
    """Human duration like ``about 3 hours`` or ``2 days`` (no trailing \"ago\")."""
    t = now if now is not None else time.time()
    sec = max(0.0, t - last_interaction)
    if sec < 45:
        return "a moment"
    if sec < 3600:
        m = int(sec // 60)
        if m <= 1:
            return "about a minute"
        return f"about {m} minutes"
    if sec < 86400:
        h = int(sec // 3600)
        m = int((sec % 3600) // 60)
        if h >= 1 and m >= 5:
            return f"about {h} hour{'s' if h != 1 else ''} and {m} minutes"
        if h == 1:
            return "about an hour"
        return f"about {h} hours"
    d = int(sec // 86400)
    if d == 1:
        return "about a day"
    return f"about {d} days"


def entity_uptime(created_at: float, *, now: float | None = None) -> str:
    """Span like ``3 days, 14 hours`` or ``about an hour`` since ``created_at``."""
    t = now if now is not None else time.time()
    sec = max(0.0, t - created_at)
    if sec < 60:
        return "less than a minute"
    if sec < 3600:
        m = int(sec // 60)
        if m == 1:
            return "about a minute"
        return f"about {m} minutes"
    if sec < 86400:
        h = int(sec // 3600)
        m = int((sec % 3600) // 60)
        if m >= 5:
            return f"{h} hour{'s' if h != 1 else ''}, {m} minutes"
        if h == 1:
            return "about an hour"
        return f"about {h} hours"
    days = int(sec // 86400)
    rem = sec - days * 86400
    h = int(rem // 3600)
    if h > 0:
        return f"{days} day{'s' if days != 1 else ''}, {h} hour{'s' if h != 1 else ''}"
    return f"{days} day{'s' if days != 1 else ''}"


def time_context_block(
    *,
    last_completed_turn_at: float | None,
    last_interlocutor_name: str,
    entity_created_at: float | None,
    now: float | None = None,
) -> str:
    """Three fixed lines: wall now, alive span, last chat (for system prompt top)."""
    line1 = format_local_now_casual_lower()
    if entity_created_at is not None:
        line2 = f"you've been alive for {entity_uptime(entity_created_at, now=now)}."
    else:
        line2 = "you've been alive for who knows how long — no `created` field in your yaml."
    if last_completed_turn_at is not None and last_completed_turn_at > 0:
        nm = (last_interlocutor_name or "").strip() or "them"
        gap = time_since_last_interaction(last_completed_turn_at, now=now)
        line3 = f"you last talked to {nm} about {gap} ago."
    else:
        line3 = "you don't have a prior completed chat turn on record yet."
    return "\n".join([line1, line2, line3])
