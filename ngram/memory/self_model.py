"""File-backed self-model snapshot for introspection and action shaping."""

from __future__ import annotations

import asyncio
import json
import time
from collections import Counter
from pathlib import Path
from typing import Any


class SelfModelStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _read_sync(self) -> dict[str, Any]:
        if not self.path.is_file():
            return {
                "updated_at": 0.0,
                "tool_usage": {},
                "tool_failures": {},
                "recent_failures": [],
                "open_project_count": 0,
                "skill_count": 0,
                "notes": [],
            }
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, json.JSONDecodeError):
            raw = {}
        return raw if isinstance(raw, dict) else {}

    def _write_sync(self, data: dict[str, Any]) -> None:
        self.path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    async def read(self) -> dict[str, Any]:
        return await asyncio.get_running_loop().run_in_executor(None, self._read_sync)

    async def record_tool_result(self, tool_name: str, ok: bool, detail: str = "") -> None:
        data = await self.read()
        usage = Counter({str(k): int(v) for k, v in (data.get("tool_usage") or {}).items()})
        failures = Counter({str(k): int(v) for k, v in (data.get("tool_failures") or {}).items()})
        usage[tool_name] += 1
        if not ok:
            failures[tool_name] += 1
            rf = list(data.get("recent_failures") or [])
            rf.append(
                {
                    "tool": tool_name,
                    "detail": (detail or "").strip()[:220],
                    "at": time.time(),
                }
            )
            data["recent_failures"] = rf[-12:]
        data["tool_usage"] = dict(usage)
        data["tool_failures"] = dict(failures)
        data["updated_at"] = time.time()
        await asyncio.get_running_loop().run_in_executor(None, self._write_sync, data)

    async def refresh_snapshot(
        self,
        *,
        open_project_count: int,
        skill_count: int,
        note: str = "",
    ) -> None:
        data = await self.read()
        data["open_project_count"] = int(open_project_count)
        data["skill_count"] = int(skill_count)
        notes = list(data.get("notes") or [])
        if note.strip():
            notes.append({"at": time.time(), "text": note.strip()[:220]})
        data["notes"] = notes[-12:]
        data["updated_at"] = time.time()
        await asyncio.get_running_loop().run_in_executor(None, self._write_sync, data)

    async def summary(self) -> str:
        data = await self.read()
        usage = Counter({str(k): int(v) for k, v in (data.get("tool_usage") or {}).items()})
        failures = Counter({str(k): int(v) for k, v in (data.get("tool_failures") or {}).items()})
        top_tools = ", ".join(f"{name}({count})" for name, count in usage.most_common(4)) or "none yet"
        top_fails = ", ".join(f"{name}({count})" for name, count in failures.most_common(3)) or "none"
        return (
            f"tool usage: {top_tools}; tool failures: {top_fails}; "
            f"open projects: {int(data.get('open_project_count') or 0)}; "
            f"skills: {int(data.get('skill_count') or 0)}"
        )

    async def tool_effectiveness_hints(self, *, min_uses: int = 3) -> str:
        """Generate system-prompt hints about tool reliability.

        Warns about tools with high failure rates and boosts tools that
        consistently succeed. Only considers tools with >= ``min_uses`` calls
        to avoid noisy signals from single-use tools.
        """
        data = await self.read()
        usage = {str(k): int(v) for k, v in (data.get("tool_usage") or {}).items()}
        failures = {str(k): int(v) for k, v in (data.get("tool_failures") or {}).items()}

        warnings: list[str] = []
        boosts: list[str] = []

        for tool_name, uses in usage.items():
            if uses < min_uses:
                continue
            fails = failures.get(tool_name, 0)
            fail_rate = fails / uses if uses > 0 else 0.0

            if fail_rate >= 0.5 and fails >= 2:
                warnings.append(
                    f"⚠ {tool_name} has failed {fails}/{uses} times recently "
                    f"({fail_rate:.0%}). Consider alternatives or verify preconditions."
                )
            elif fail_rate == 0.0 and uses >= 5:
                boosts.append(f"✓ {tool_name} ({uses} uses, 100% success)")

        if not warnings and not boosts:
            return ""

        parts: list[str] = []
        if warnings:
            parts.append("Tool warnings (from experience):\n" + "\n".join(warnings))
        if boosts:
            parts.append("Reliable tools: " + "; ".join(boosts))

        return "\n".join(parts)
