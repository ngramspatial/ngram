"""Deterministic natural-language → cron (5-field) parsing."""

from __future__ import annotations

import re
import dateparser


class ScheduleParseError(ValueError):
    """Raised when a schedule phrase cannot be mapped to cron."""


def _norm(s: str) -> str:
    t = (s or "").strip().lower()
    t = re.sub(r"\s+", " ", t)
    return t


_WD = {
    "monday": "1",
    "mon": "1",
    "tuesday": "2",
    "tue": "2",
    "tues": "2",
    "wednesday": "3",
    "wed": "3",
    "thursday": "4",
    "thu": "4",
    "thur": "4",
    "thurs": "4",
    "friday": "5",
    "fri": "5",
    "saturday": "6",
    "sat": "6",
    "sunday": "0",
    "sun": "0",
}


def _parse_time_token(raw: str) -> tuple[int, int] | None:
    """Return (hour, minute) 24h from fragments like '8am', '11 pm', '14:30'."""
    s = _norm(raw).replace(" ", "")
    if not s:
        return None
    m = re.match(r"^(\d{1,2})(?::(\d{2}))?(am|pm)?$", s)
    if not m:
        m2 = re.match(r"^(\d{1,2}):(\d{2})$", s)
        if not m2:
            return None
        h, mi = int(m2.group(1)), int(m2.group(2))
        if 0 <= h <= 23 and 0 <= mi <= 59:
            return h, mi
        return None
    h = int(m.group(1))
    mi = int(m.group(2) or 0)
    ap = m.group(3)
    if ap == "pm" and h < 12:
        h += 12
    if ap == "am" and h == 12:
        h = 0
    if not (0 <= h <= 23 and 0 <= mi <= 59):
        return None
    return h, mi


def _try_rules(natural: str) -> str | None:
    s = _norm(natural)

    m = re.match(r"^every\s+30\s+minutes?$", s)
    if m:
        return "*/30 * * * *"
    m = re.match(r"^every\s+15\s+minutes?$", s)
    if m:
        return "*/15 * * * *"
    m = re.match(r"^every\s+(\d+)\s+minutes?$", s)
    if m:
        n = int(m.group(1))
        if 1 <= n <= 59:
            return f"*/{n} * * * *"

    m = re.match(r"^every\s+(\d+)\s+hours?$", s)
    if m:
        n = int(m.group(1))
        if 1 <= n <= 23:
            return f"0 */{n} * * *"

    m = re.match(
        r"^every\s+(morning|day|night|evening)(?:\s+at\s+(.+))?$",
        s,
    )
    if m:
        slot = m.group(1)
        tm = m.group(2)
        default_h = {"morning": 8, "day": 9, "evening": 18, "night": 23}.get(slot, 9)
        h, mi = default_h, 0
        if tm:
            parsed = _parse_time_token(tm) or _parse_time_via_dateparser(tm)
            if parsed:
                h, mi = parsed
        return f"{mi} {h} * * *"

    m = re.match(
        r"^every\s+day\s+at\s+(.+)$",
        s,
    )
    if m:
        parsed = _parse_time_token(m.group(1)) or _parse_time_via_dateparser(m.group(1))
        if parsed:
            h, mi = parsed
            return f"{mi} {h} * * *"

    m = re.match(r"^twice\s+a\s+day$", s)
    if m:
        return "0 8,20 * * *"

    m = re.match(
        r"^every\s+weekday\s+at\s+(.+)$",
        s,
    )
    if m:
        parsed = _parse_time_token(m.group(1)) or _parse_time_via_dateparser(m.group(1))
        if parsed:
            h, mi = parsed
            return f"{mi} {h} * * 1-5"

    m = re.match(
        r"^once\s+a\s+week\s+on\s+(\w+)(?:\s+at\s+(.+))?$",
        s,
    )
    if m:
        wd = _WD.get(m.group(1).lower())
        if wd is not None:
            h, mi = 12, 0
            if m.group(2):
                p2 = _parse_time_token(m.group(2)) or _parse_time_via_dateparser(m.group(2))
                if p2:
                    h, mi = p2
            return f"{mi} {h} * * {wd}"

    m = re.match(
        r"^every\s+monday\s+and\s+thursday(?:\s+at\s+(.+))?$",
        s,
    )
    if m:
        h, mi = 9, 0
        if m.group(1):
            p3 = _parse_time_token(m.group(1)) or _parse_time_via_dateparser(m.group(1))
            if p3:
                h, mi = p3
        return f"{mi} {h} * * 1,4"

    m = re.match(
        r"^every\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday|mon|tue|tues|wed|thu|thurs|fri|sat|sun)\s+"
        r"and\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday|mon|tue|tues|wed|thu|thurs|fri|sat|sun)"
        r"(?:\s+at\s+(.+))?$",
        s,
    )
    if m:
        days: list[str] = []
        for g in (m.group(1), m.group(2)):
            d = _WD.get(g.lower())
            if d is not None:
                days.append(d)
        if len(days) >= 2:
            dom = ",".join(sorted(set(days), key=int))
            h, mi = 9, 0
            if m.group(3):
                p3 = _parse_time_token(m.group(3)) or _parse_time_via_dateparser(m.group(3))
                if p3:
                    h, mi = p3
            return f"{mi} {h} * * {dom}"

    m = re.match(r"^every\s+other\s+day$", s)
    if m:
        return "0 9 */2 * *"

    m = re.match(r"^first\s+of\s+every\s+month$", s)
    if m:
        return "0 9 1 * *"

    m = re.match(
        r"^every\s+(\w+)\s+evening$",
        s,
    )
    if m:
        wd = _WD.get(m.group(1).lower())
        if wd is not None:
            return f"0 18 * * {wd}"

    m = re.match(
        r"^every\s+(\w+)$",
        s,
    )
    if m:
        wd = _WD.get(m.group(1).lower())
        if wd is not None:
            return f"0 9 * * {wd}"

    return None


def _parse_time_via_dateparser(fragment: str) -> tuple[int, int] | None:
    dt = dateparser.parse(
        fragment.strip(),
        settings={
            "PREFER_DATES_FROM": "future",
            "RETURN_AS_TIMEZONE_AWARE": False,
        },
    )
    if dt is None:
        return None
    return dt.hour, dt.minute


def parse_schedule(natural: str) -> str:
    """
    Parse natural language into 5-field cron (minute hour day month dow).

    Examples:
        "every morning at 8am" → "0 8 * * *"
        "every 3 hours" → "0 */3 * * *"
    """
    s = _norm(natural)
    if not s:
        raise ScheduleParseError("empty schedule")

    direct = _try_rules(s)
    if direct:
        return direct

    # "at 8am" / "8pm daily"
    if s.startswith("at "):
        frag = s[3:].strip()
        parsed = _parse_time_token(frag) or _parse_time_via_dateparser(frag)
        if parsed:
            h, mi = parsed
            return f"{mi} {h} * * *"

    if " daily" in s or s.endswith("daily"):
        core = s.replace("daily", "").strip()
        core = re.sub(r"\s+at\s+", " ", core).strip()
        parsed = _parse_time_token(core) or _parse_time_via_dateparser(core)
        if parsed:
            h, mi = parsed
            return f"{mi} {h} * * *"

    # Whole-string time: "11pm", "noon" via dateparser
    parsed = _parse_time_via_dateparser(s)
    if parsed:
        h, mi = parsed
        return f"{mi} {h} * * *"

    raise ScheduleParseError(
        "i couldn't figure out that schedule — can you say it differently?"
    )
