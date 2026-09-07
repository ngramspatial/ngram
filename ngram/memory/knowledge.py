"""Curated per-entity knowledge from ~/.ngram/entities/<name>/knowledge.md (optional)."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from ngram.memory.store import cosine_sim

if TYPE_CHECKING:
    from ngram.config import EntityConfig
    from ngram.inference.protocol import InferenceProvider

log = structlog.get_logger("ngram.knowledge")


def knowledge_file_path(entity: EntityConfig) -> Path:
    return Path(entity.knowledge_path()).expanduser().resolve()


_KNOWLEDGE_TEMPLATE = """\
## [locked] about yourself
{entity_name} is an entity running on the ngram harness.

## notes
(empty — the entity will fill this in as it learns)
"""


def seed_knowledge_if_missing(entity: EntityConfig) -> bool:
    """Write a minimal knowledge.md template if none exists. Returns True when a file was created."""
    p = knowledge_file_path(entity)
    if p.is_file():
        return False
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            _KNOWLEDGE_TEMPLATE.format(entity_name=entity.name),
            encoding="utf-8",
        )
        log.info("knowledge_seeded", entity=entity.name, path=str(p))
        return True
    except OSError as exc:
        log.warning("knowledge_seed_failed", entity=entity.name, error=str(exc))
        return False


def _is_h2_header(line: str) -> bool:
    s = line.strip()
    if not s.startswith("##"):
        return False
    return not s.startswith("###")


def parse_knowledge_sections(raw: str) -> list[tuple[str, str]]:
    """Split markdown into (header_title, body) pairs; title is the line after ``## `` (H2 only)."""
    sections: list[tuple[str, str]] = []
    current_title: str | None = None
    body_lines: list[str] = []
    for line in raw.splitlines():
        if _is_h2_header(line):
            if current_title is not None:
                sections.append((current_title, "\n".join(body_lines).strip()))
            current_title = line.strip()[2:].strip()
            body_lines = []
        elif current_title is not None:
            body_lines.append(line)
    if current_title is not None:
        sections.append((current_title, "\n".join(body_lines).strip()))
    return sections


def section_markdown(title: str, body: str) -> str:
    return f"## {title}\n{body}".strip()


def is_locked_title(title: str) -> bool:
    return title.strip().casefold().startswith("[locked]")


def _norm_key(title: str) -> str:
    return title.strip().casefold()


def append_knowledge_sections(
    entity: EntityConfig,
    sections: list[tuple[str, str]],
) -> int:
    """Write new H2 sections to knowledge.md, skipping locked titles and duplicates.

    Used by the compaction memory flush. Returns the number of sections actually written.
    """
    if not sections:
        return 0
    p = knowledge_file_path(entity)
    try:
        existing_raw = p.read_text(encoding="utf-8") if p.is_file() else ""
    except OSError:
        existing_raw = ""
    existing_titles = {_norm_key(t) for t, _ in parse_knowledge_sections(existing_raw)}

    added = 0
    buf = existing_raw.rstrip()
    for title, body in sections:
        if is_locked_title(title):
            continue
        if _norm_key(title) in existing_titles:
            continue
        buf += f"\n\n## {title}\n{body}"
        existing_titles.add(_norm_key(title))
        added += 1

    if added == 0:
        return 0
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(buf.lstrip("\n") + "\n", encoding="utf-8")
        log.info("knowledge_sections_appended", entity=entity.name, added=added)
    except OSError as exc:
        log.warning("knowledge_append_failed", entity=entity.name, error=str(exc))
        return 0
    return added


class KnowledgeStore:
    """In-memory index of knowledge.md sections with embeddings (refreshed when mtime changes)."""

    def __init__(self, entity: EntityConfig, client: InferenceProvider) -> None:
        self.entity = entity
        self.client = client
        self._path = knowledge_file_path(entity)
        self._indexed_mtime: float | None = None
        self._chunks: list[tuple[str, str, list[float]]] = []
        # title, full_section_text, embedding

    def _path_exists(self) -> bool:
        return self._path.is_file()

    async def _embed_section(self, title: str, body: str) -> list[float]:
        model = self.entity.harness.models.embedding
        text = f"{title}\n{body}"[:8000]
        try:
            vec = await self.client.embed(model, text)
            return vec if vec else []
        except Exception:
            return []

    async def _load_and_embed(self) -> None:
        self._chunks = []
        if not self._path_exists():
            self._indexed_mtime = 0.0
            return
        try:
            raw = self._path.read_text(encoding="utf-8")
            mtime = self._path.stat().st_mtime
        except OSError:
            self._indexed_mtime = 0.0
            return

        sections = parse_knowledge_sections(raw)
        if len(sections) > 50 or len(raw) > 20_000:
            log.warning(
                "knowledge_file_large",
                module="knowledge",
                entity=self.entity.name,
                sections=len(sections),
                chars=len(raw),
                hint="consider trimming knowledge.md for maintainability",
            )

        for title, body in sections:
            emb = await self._embed_section(title, body)
            if not emb:
                continue
            full = section_markdown(title, body)
            self._chunks.append((title, full, emb))

        self._indexed_mtime = mtime

    async def _ensure_current(self) -> None:
        if not self._path_exists():
            self._chunks = []
            self._indexed_mtime = 0.0
            return
        try:
            mtime = self._path.stat().st_mtime
        except OSError:
            return
        if self._indexed_mtime is None or mtime != self._indexed_mtime:
            await self._load_and_embed()

    async def query(
        self,
        context: str,
        top_k: int = 3,
        min_similarity: float = 0.3,
    ) -> list[str]:
        await self._ensure_current()
        if not self._chunks:
            return []
        model = self.entity.harness.models.embedding
        ctx = (context or "").strip()[:4000]
        if not ctx:
            return []
        try:
            qvec = await self.client.embed(model, ctx)
        except Exception:
            return []
        if not qvec:
            return []
        scored: list[tuple[float, str]] = []
        for _title, full, emb in self._chunks:
            sim = cosine_sim(qvec, emb)
            if sim >= min_similarity:
                scored.append((sim, full))
        scored.sort(key=lambda x: -x[0])
        return [full for _sim, full in scored[:top_k]]

    async def refresh_after_edit(self) -> None:
        """Call after knowledge.md was modified on disk (e.g. update_knowledge tool)."""
        self._indexed_mtime = None
        await self._ensure_current()
