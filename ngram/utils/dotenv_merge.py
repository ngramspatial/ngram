"""Merge key=value updates into a repo ``.env`` file (preserve unrelated lines)."""

from __future__ import annotations

from pathlib import Path


def merge_dotenv_keys(path: Path, updates: dict[str, str]) -> None:
    """Upsert ``updates`` into ``path``. Preserves comments and unknown keys.

    Keys in ``updates`` replace existing assignments or are appended at the end.
    """
    if not updates:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    keys_done: set[str] = set()
    out_lines: list[str] = []
    if path.is_file():
        for raw in path.read_text(encoding="utf-8-sig").splitlines():
            line = raw.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, _ = line.partition("=")
                k = k.strip()
                if k in updates:
                    out_lines.append(f"{k}={updates[k]}")
                    keys_done.add(k)
                    continue
            out_lines.append(raw)
    for k, v in updates.items():
        if k not in keys_done:
            out_lines.append(f"{k}={v}")
    path.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
