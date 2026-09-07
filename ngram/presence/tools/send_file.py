"""Send a workspace file to the user as a Telegram/Discord attachment."""

from __future__ import annotations

import json
import mimetypes
from pathlib import PurePosixPath

from ngram.presence.tools.registry import tool
from ngram.presence.tools.runtime import require_tool_runtime
from ngram.presence.tools.execution_rpc import (
    get_execution_client,
    read_only_workspace_fs_allowed,
    local_tool_block_message,
)

# Must match execution_rpc read_file ceiling (large logs e.g. autonomy_transcript.md).
_SEND_FILE_MAX_BYTES = 1_048_576

# Windows often maps .md → None → application/octet-stream, which we must not treat as binary.
_TEXT_EXT_TO_MIME: dict[str, str] = {
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".mdx": "text/markdown",
    ".txt": "text/plain",
    ".log": "text/plain",
    ".json": "application/json",
    ".yaml": "text/yaml",
    ".yml": "text/yaml",
    ".toml": "text/plain",
    ".csv": "text/csv",
    ".xml": "application/xml",
    ".html": "text/html",
    ".htm": "text/html",
    ".css": "text/css",
    ".js": "text/javascript",
    ".ts": "text/plain",
    ".tsx": "text/plain",
    ".jsx": "text/javascript",
    ".py": "text/x-python",
    ".rst": "text/plain",
    ".ini": "text/plain",
    ".cfg": "text/plain",
    ".conf": "text/plain",
    ".env": "text/plain",
}


def _content_type_for_filename(filename: str) -> str:
    name = (filename or "").strip() or "file.txt"
    ext = PurePosixPath(name).suffix.lower()
    if ext in _TEXT_EXT_TO_MIME:
        return _TEXT_EXT_TO_MIME[ext]
    guessed = mimetypes.guess_type(name)[0]
    return guessed or "application/octet-stream"


def _is_blocked_binary_payload(content_type: str, filename: str) -> bool:
    """Block true binaries; allow text/* and known text extensions."""
    name = (filename or "").strip() or "file.txt"
    ext = PurePosixPath(name).suffix.lower()
    if ext in _TEXT_EXT_TO_MIME:
        return False
    ct = (content_type or "").lower()
    if ct.startswith("image/") or ct.startswith("audio/") or ct.startswith("video/"):
        return True
    if ct in {"application/pdf", "application/zip", "application/x-zip-compressed"}:
        return True
    if ct == "application/octet-stream":
        return True
    return False


@tool(
    "send_file",
    "Send a file from your workspace to the user as an attachment in the current "
    "chat (Telegram/Discord download). Use this when they ask to send, attach, or upload a file "
    "(e.g. 'send me journal.md', 'give me knowledge.md', autonomy_transcript.md). "
    "Path is relative to workspace root "
    "(e.g. journal.md, knowledge.md, soma/soma-state.json). Do not use read_file alone for that—"
    "send_file delivers the file.",
)
async def send_file(path: str, message: str = "") -> str:
    ctx = require_tool_runtime()
    if not read_only_workspace_fs_allowed(ctx.entity):
        return json.dumps({"error": local_tool_block_message(ctx.entity)})

    platform = ctx.platform
    if platform is None:
        return json.dumps({"error": "no active platform to send files to"})

    inp = ctx.inp
    if inp is None:
        return json.dumps({"error": "no active conversation"})

    client = get_execution_client()
    res = await client.call(
        "read_file",
        {"path": path or ".", "max_bytes": _SEND_FILE_MAX_BYTES},
    )
    if not res.get("ok"):
        return json.dumps({"error": res.get("error") or "could not read file"})

    content = str(res.get("content") or "")
    if not content:
        return json.dumps({"error": "file is empty"})

    data = content.encode("utf-8")
    filename = PurePosixPath(path).name or "file.txt"
    content_type = _content_type_for_filename(filename)
    # read_file RPC returns text; refuse payloads we still classify as opaque binary.
    if _is_blocked_binary_payload(content_type, filename):
        return json.dumps(
            {
                "error": (
                    f"send_file cannot deliver binary payloads safely ({content_type}). "
                    "Use screenshot/image-specific tools (e.g. send_screenshot) or text files."
                )
            }
        )

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

    truncated = len(data) >= _SEND_FILE_MAX_BYTES
    out: dict[str, object] = {"ok": True, "sent": filename, "size_bytes": len(data)}
    if truncated:
        out["note"] = f"first {_SEND_FILE_MAX_BYTES} bytes only — file may be larger on disk"
    return json.dumps(out)
