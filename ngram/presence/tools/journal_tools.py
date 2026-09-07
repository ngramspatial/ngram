"""Private journal read/write."""

from __future__ import annotations

import json
import mimetypes

from ngram.presence.tools.registry import tool
from ngram.presence.tools.runtime import require_tool_runtime

_MAX_JOURNAL_ATTACHMENT_BYTES = 20 * 1024 * 1024


@tool(
    name="read_journal",
    description="Read your own journal entries (private reflections from routines). "
    "For excerpts in chat. If the user wants the full journal.md as a file download, call "
    "send_journal_file (not read_file or list_directory).",
)
async def read_journal(n: int = 5) -> str:
    ctx = require_tool_runtime()
    ent = ctx.entity
    if not ent._tool_enabled("journal", True) or not ent.config.automations.journal.enabled:
        return json.dumps({"error": "journal disabled"})
    k = max(1, min(30, int(n or 5)))
    chunks = await ent.journal.read_recent(k)
    return json.dumps({"entries": chunks}, ensure_ascii=False)


@tool(
    name="write_journal",
    description="Write a private journal entry — reflection, processing, or a note to your future self.",
)
async def write_journal(content: str) -> str:
    ctx = require_tool_runtime()
    ent = ctx.entity
    if not ent._tool_enabled("journal", True) or not ent.config.automations.journal.enabled:
        return json.dumps({"ok": False, "error": "journal disabled"})
    raw = (content or "").strip()
    if not raw:
        return json.dumps({"ok": False, "error": "empty content"})
    await ent.journal.write_entry(raw, tags=["manual"])
    return json.dumps({"ok": True}, ensure_ascii=False)


@tool(
    name="send_journal_file",
    description=(
        "Send your journal.md to the user as a chat attachment (Telegram/Discord download). "
        "Use when they ask to send, attach, or upload journal.md or your journal file. "
        "Reads the real journal path on disk — use this instead of read_file, list_directory, "
        "or send_file when they want the journal as a file."
    ),
)
async def send_journal_file(message: str = "") -> str:
    """Upload journal.md from ``entity.journal.path`` (no workspace path guessing)."""
    ctx = require_tool_runtime()
    ent = ctx.entity
    if not ent._tool_enabled("journal", True) or not ent.config.automations.journal.enabled:
        return json.dumps({"error": "journal disabled"})

    platform = ctx.platform
    if platform is None:
        return json.dumps({"error": "no active platform to send files to"})
    inp = ctx.inp
    if inp is None:
        return json.dumps({"error": "no active conversation"})

    path = ent.journal.path
    if not path.is_file():
        return json.dumps(
            {
                "error": "journal.md not found on disk yet",
                "resolved_path": str(path),
            },
            ensure_ascii=False,
        )

    try:
        data = path.read_bytes()
    except OSError as e:
        return json.dumps({"error": f"could not read journal: {e}"})

    if len(data) > _MAX_JOURNAL_ATTACHMENT_BYTES:
        return json.dumps(
            {
                "error": f"journal exceeds max send size ({_MAX_JOURNAL_ATTACHMENT_BYTES} bytes)",
                "size_bytes": len(data),
            },
            ensure_ascii=False,
        )

    filename = path.name or "journal.md"
    content_type = mimetypes.guess_type(filename)[0] or "text/markdown"

    send_attach = getattr(platform, "send_attachment_bytes", None)
    if not callable(send_attach):
        return json.dumps({"error": f"platform {inp.platform} does not support file attachments"})

    try:
        await send_attach(
            inp.channel,
            data,
            content_type=content_type,
            filename=filename,
        )
    except Exception as e:
        return json.dumps({"error": f"failed to send: {e}"})

    if message:
        try:
            await platform.send_message(inp.channel, message)
        except Exception:
            pass

    return json.dumps({"ok": True, "sent": filename, "size_bytes": len(data)}, ensure_ascii=False)
