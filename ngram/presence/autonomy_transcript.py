"""Append-only markdown transcript for autonomous wake sessions.

Keeps detailed tool activity and wake status lines on disk (beside journal / workspace)
so Telegram stays readable when ``wake_chat_tool_activity`` / status flags are off.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger("ngram.presence.autonomy_transcript")

_LOCKS: dict[str, asyncio.Lock] = {}

_TRANSCRIPT_HEADER = """# Autonomy transcript

Full **autonomous** session detail: wake status lines, per-tool activity, and optional notes.
Reactive chat (normal user messages) is unchanged — this file is for wake / ``platform=autonomous`` work only.

Configure in ``autonomy:`` (``transcript_enabled``, ``wake_chat_tool_activity``, ``wake_user_visible_status``).
Telegram: ``/wakequiet on`` persists transcript-only for wake status/tool lines (overrides YAML until ``/wakequiet off``).

---

"""


def _lock_for(path: str) -> asyncio.Lock:
    key = str(Path(path).resolve())
    if key not in _LOCKS:
        _LOCKS[key] = asyncio.Lock()
    return _LOCKS[key]


async def append_autonomy_transcript(
    path: str,
    lines: list[str],
    *,
    heading: str | None = None,
) -> None:
    """Append a timestamped section to the transcript file (async-safe per path)."""
    p = Path(path).expanduser()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        log.debug("autonomy_transcript_mkdir_failed", path=str(p), error=str(e))
        return

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    block_lines: list[str] = []
    if heading:
        block_lines.append(f"\n## {ts} — {heading}\n\n")
    else:
        block_lines.append(f"\n## {ts}\n\n")
    for line in lines:
        block_lines.append(f"{line}\n")
    text = "".join(block_lines)

    async with _lock_for(str(p)):

        def _write() -> None:
            is_new = not p.is_file() or p.stat().st_size == 0
            with p.open("a", encoding="utf-8", errors="replace") as f:
                if is_new:
                    f.write(_TRANSCRIPT_HEADER)
                f.write(text)

        await asyncio.get_event_loop().run_in_executor(None, _write)


async def append_wake_lines_if_enabled(entity: Any, lines: list[str], *, heading: str) -> None:
    """Write wake status / card lines to the autonomy transcript when enabled."""
    cfg = getattr(entity, "config", None)
    if cfg is None or not lines:
        return
    auto = cfg.harness.autonomy
    if not bool(getattr(auto, "transcript_enabled", True)):
        return
    try:
        path = cfg.autonomy_transcript_path()
    except Exception:
        return
    await append_autonomy_transcript(path, lines, heading=heading)
