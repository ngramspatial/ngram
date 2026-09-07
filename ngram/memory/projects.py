"""File-backed long-horizon projects ledger."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class ProjectRecord:
    id: str
    title: str
    summary: str
    status: str = "active"
    why_it_matters: str = ""
    next_steps: list[str] = field(default_factory=list)
    related_people: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    updated_at: float = 0.0
    last_activity: str = ""


class ProjectLedger:
    def __init__(self, path: Path) -> None:
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _read_sync(self) -> list[ProjectRecord]:
        if not self.path.is_file():
            return []
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, json.JSONDecodeError):
            return []
        rows = raw if isinstance(raw, list) else []
        out: list[ProjectRecord] = []
        for item in rows:
            if not isinstance(item, dict):
                continue
            try:
                out.append(
                    ProjectRecord(
                        id=str(item.get("id") or ""),
                        title=str(item.get("title") or ""),
                        summary=str(item.get("summary") or ""),
                        status=str(item.get("status") or "active"),
                        why_it_matters=str(item.get("why_it_matters") or ""),
                        next_steps=[str(x) for x in (item.get("next_steps") or []) if str(x).strip()],
                        related_people=[str(x) for x in (item.get("related_people") or []) if str(x).strip()],
                        tags=[str(x) for x in (item.get("tags") or []) if str(x).strip()],
                        updated_at=float(item.get("updated_at") or 0.0),
                        last_activity=str(item.get("last_activity") or ""),
                    )
                )
            except Exception:
                continue
        return out

    def _write_sync(self, rows: list[ProjectRecord]) -> None:
        payload = [asdict(r) for r in rows]
        self.path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    async def list_projects(self) -> list[ProjectRecord]:
        rows = await asyncio.get_running_loop().run_in_executor(None, self._read_sync)
        rows.sort(key=lambda r: r.updated_at, reverse=True)
        return rows

    async def create_project(
        self,
        title: str,
        summary: str,
        *,
        why_it_matters: str = "",
        next_steps: list[str] | None = None,
        related_people: list[str] | None = None,
        tags: list[str] | None = None,
    ) -> ProjectRecord:
        raw_title = (title or "").strip()
        raw_summary = (summary or "").strip()
        if not raw_title:
            raise RuntimeError("title is required")
        if not raw_summary:
            raise RuntimeError("summary is required")
        now = time.time()
        row = ProjectRecord(
            id=f"proj_{uuid.uuid4().hex[:10]}",
            title=raw_title,
            summary=raw_summary,
            why_it_matters=(why_it_matters or "").strip(),
            next_steps=[x.strip() for x in (next_steps or []) if str(x).strip()],
            related_people=[x.strip() for x in (related_people or []) if str(x).strip()],
            tags=[x.strip() for x in (tags or []) if str(x).strip()],
            updated_at=now,
            last_activity="created",
        )
        rows = await self.list_projects()
        rows.append(row)
        await asyncio.get_running_loop().run_in_executor(None, self._write_sync, rows)
        return row

    async def update_project(
        self,
        project_id: str,
        *,
        summary: str | None = None,
        status: str | None = None,
        why_it_matters: str | None = None,
        next_steps: list[str] | None = None,
        related_people: list[str] | None = None,
        tags: list[str] | None = None,
        last_activity: str | None = None,
    ) -> ProjectRecord:
        pid = (project_id or "").strip()
        rows = await self.list_projects()
        for idx, row in enumerate(rows):
            if row.id != pid:
                continue
            if summary is not None:
                row.summary = (summary or "").strip()
            if status is not None:
                row.status = (status or "").strip() or row.status
            if why_it_matters is not None:
                row.why_it_matters = (why_it_matters or "").strip()
            if next_steps is not None:
                row.next_steps = [x.strip() for x in next_steps if str(x).strip()]
            if related_people is not None:
                row.related_people = [x.strip() for x in related_people if str(x).strip()]
            if tags is not None:
                row.tags = [x.strip() for x in tags if str(x).strip()]
            if last_activity is not None:
                row.last_activity = (last_activity or "").strip()
            row.updated_at = time.time()
            rows[idx] = row
            await asyncio.get_running_loop().run_in_executor(None, self._write_sync, rows)
            return row
        raise RuntimeError(f"unknown project id: {pid}")

    async def summary_lines(self, limit: int = 5) -> list[str]:
        rows = await self.list_projects()
        out: list[str] = []
        for row in rows[: max(1, min(limit, 12))]:
            steps = "; ".join(row.next_steps[:2]) if row.next_steps else "no next steps saved"
            out.append(f"{row.title} [{row.status}] — {row.summary} | next: {steps}")
        return out
