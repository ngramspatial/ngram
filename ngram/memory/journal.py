"""Append-only markdown journal on disk (entity private reflections)."""

from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path


class Journal:
    def __init__(self, path: Path) -> None:
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    async def write_entry(self, content: str, tags: list[str] | None = None) -> None:
        raw = (content or "").strip()
        if not raw:
            return
        ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        tag_line = ""
        if tags:
            tag_line = " ".join(f"#{t.strip()}" for t in tags if t and str(t).strip())
        block = f"\n## {ts}"
        if tag_line:
            block += f" {tag_line}"
        block += f"\n\n{raw}\n"
        loop = asyncio.get_running_loop()

        def _append() -> None:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(block)

        await loop.run_in_executor(None, _append)

    async def read_recent(self, n: int = 5) -> list[str]:
        if n <= 0 or not self.path.is_file():
            return []
        loop = asyncio.get_running_loop()

        def _read() -> list[str]:
            text = self.path.read_text(encoding="utf-8", errors="replace")
            parts = re.split(r"\n## \d{4}-\d{2}-\d{2}", text)
            if len(parts) <= 1:
                chunks = [text.strip()] if text.strip() else []
            else:
                head = parts[0].strip()
                rest = [p.strip() for p in parts[1:] if p.strip()]
                chunks = ([head] if head else []) + rest
            return [c for c in chunks if c][-n:]

        return await loop.run_in_executor(None, _read)

    async def search(self, query: str) -> list[str]:
        q = (query or "").strip().lower()
        if not q or not self.path.is_file():
            return []
        loop = asyncio.get_running_loop()

        def _scan() -> list[str]:
            text = self.path.read_text(encoding="utf-8", errors="replace")
            parts = re.split(r"(?=\n## \d{4}-\d{2}-\d{2})", text)
            hits = [p.strip() for p in parts if q in p.lower() and p.strip()]
            return hits[-20:]

        return await loop.run_in_executor(None, _scan)
