"""Apply a unified diff to text (single-file patches, multiple hunks)."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class _Hunk:
    old_start: int  # 1-based, inclusive
    old_count: int
    new_start: int
    new_count: int
    lines: list[str]  # raw hunk body lines including leading +/- / space


_HUNK_RE = re.compile(
    r"^@@\s+-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s+@@",
    re.MULTILINE,
)


def _split_hunks(patch: str) -> list[_Hunk]:
    text = patch.replace("\r\n", "\n")
    matches = list(_HUNK_RE.finditer(text))
    if not matches:
        raise ValueError("No unified diff hunk headers (@@ ... @@) found.")
    hunks: list[_Hunk] = []
    for i, m in enumerate(matches):
        old_s = int(m.group(1))
        old_c = int(m.group(2) or 1)
        new_s = int(m.group(3))
        new_c = int(m.group(4) or 1)
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        chunk = text[m.end() : end]
        body_lines: list[str] = []
        for raw in chunk.split("\n"):
            if not raw and not body_lines:
                continue
            if raw.startswith("---") or raw.startswith("+++"):
                continue
            body_lines.append(raw)
        while body_lines and body_lines[-1] == "":
            body_lines.pop()
        hunks.append(_Hunk(old_s, old_c, new_s, new_c, body_lines))
    return hunks


def _normalize_line(line: str) -> str:
    """Strip first character if it is a unified-diff prefix."""
    if not line:
        return ""
    if line[0] in " +-":
        return line[1:]
    return line


def _hunk_old_new_segments(hunk: _Hunk) -> tuple[list[str], list[str]]:
    """Build old-file and new-file line sequences for this hunk."""
    old_seg: list[str] = []
    new_seg: list[str] = []
    for line in hunk.lines:
        if line.startswith("\\"):
            continue
        if not line:
            continue
        kind = line[0]
        body = line[1:] if len(line) > 1 else ""
        if kind == " ":
            old_seg.append(body)
            new_seg.append(body)
        elif kind == "-":
            old_seg.append(body)
        elif kind == "+":
            new_seg.append(body)
        else:
            # tolerate missing leading space (some generators)
            old_seg.append(line)
            new_seg.append(line)
    return old_seg, new_seg


def apply_unified_diff(original: str, patch_text: str) -> str:
    """Apply a unified diff to ``original`` text. Raises ValueError on mismatch."""
    patch_text = patch_text.replace("\r\n", "\n")
    if not patch_text.strip():
        raise ValueError("Empty patch.")
    hunks = _split_hunks(patch_text)
    # Work with lines without trailing newlines for matching; rejoin at end
    old_lines = original.split("\n")
    # Apply from last hunk to first so indices stay valid
    for hunk in sorted(hunks, key=lambda h: h.old_start, reverse=True):
        old_seg, new_seg = _hunk_old_new_segments(hunk)
        if not old_seg and not new_seg:
            continue
        idx0 = hunk.old_start - 1
        if idx0 < 0 or idx0 > len(old_lines):
            raise ValueError(f"Hunk start line {hunk.old_start} out of range for file.")
        span = len(old_seg)
        cur = old_lines[idx0 : idx0 + span]
        if cur != old_seg:
            raise ValueError(
                f"Hunk at line {hunk.old_start} does not match file content. "
                f"Expected {span} line(s), mismatch at first difference."
            )
        old_lines[idx0 : idx0 + span] = list(new_seg)
    # Preserve trailing newline behavior of original
    ends_nl = original.endswith("\n") or original == ""
    out = "\n".join(old_lines)
    if ends_nl and not out.endswith("\n"):
        out += "\n"
    return out


def strip_diff_headers(patch_text: str) -> str:
    """Remove leading ---/+++ file lines so _split_hunks can find @@."""
    lines = []
    for line in patch_text.replace("\r\n", "\n").split("\n"):
        if line.startswith("--- ") or line.startswith("+++ "):
            continue
        lines.append(line)
    return "\n".join(lines)
