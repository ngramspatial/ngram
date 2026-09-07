"""Entity tool: edit curated knowledge.md (deliberate path only)."""

from __future__ import annotations

import shutil
from typing import TYPE_CHECKING

from ngram.memory.knowledge import (
    is_locked_title,
    knowledge_file_path,
    parse_knowledge_sections,
    section_markdown,
)
from ngram.presence.tools.registry import ToolRegistry

if TYPE_CHECKING:
    from ngram.config import EntityConfig
    from ngram.memory.knowledge import KnowledgeStore

_UPDATE_KNOWLEDGE_DESCRIPTION = (
    "Add, update, or remove a section from your personal knowledge base. Use this when you learn "
    "something important you want to remember permanently — not fleeting conversation details, "
    "but lasting facts, opinions, or context that matter to you."
)


def _norm_key(title: str) -> str:
    return title.strip().casefold()


def _serialize(sections: list[tuple[str, str]]) -> str:
    blocks: list[str] = []
    for t, body in sections:
        blocks.append(section_markdown(t, body).rstrip() + "\n")
    text = "\n".join(blocks).strip()
    return text + ("\n" if text else "")


def _find_index(sections: list[tuple[str, str]], section: str) -> int:
    key = _norm_key(section)
    for i, (t, _) in enumerate(sections):
        if _norm_key(t) == key:
            return i
    return -1


def _make_update_knowledge_fn(entity: EntityConfig, store: KnowledgeStore):
    path = knowledge_file_path(entity)
    backup = path.parent / (path.name + ".bak")

    async def update_knowledge(action: str, section: str, content: str = "") -> str:
        act = (action or "").strip().lower()
        sec = (section or "").strip()
        body = (content or "").strip()

        if act not in ("add", "update", "remove"):
            return f'unknown action {action!r} — use "add", "update", or "remove".'
        if not sec:
            return "section name is required."
        if act == "add" and not body:
            return "content is required for add."
        if act == "update" and not body:
            return "content is required for update."

        path.parent.mkdir(parents=True, exist_ok=True)

        if path.is_file():
            try:
                shutil.copy2(path, backup)
            except OSError as e:
                return f"could not write backup: {e}"
            raw = path.read_text(encoding="utf-8")
        else:
            raw = ""

        sections = parse_knowledge_sections(raw) if raw.strip() else []

        if act == "add":
            if _find_index(sections, sec) >= 0:
                return f'a section named {sec!r} already exists — use "update" to change it.'
            sections.append((sec, body))
        elif act == "update":
            idx = _find_index(sections, sec)
            if idx < 0:
                return f'no section named {sec!r} — use "add" to create it.'
            title, _ = sections[idx]
            if is_locked_title(title):
                return "that section is locked — only your creator can edit it."
            sections[idx] = (title, body)
        else:
            idx = _find_index(sections, sec)
            if idx < 0:
                return f"no section named {sec!r} to remove."
            title, _ = sections[idx]
            if is_locked_title(title):
                return "that section is locked — only your creator can edit it."
            del sections[idx]

        try:
            path.write_text(_serialize(sections), encoding="utf-8")
        except OSError as e:
            return str(e)

        await store.refresh_after_edit()

        if act == "add":
            return f"added section: {sec}"
        if act == "update":
            return f"updated section: {sec}"
        return f"removed section: {sec}"

    return update_knowledge


def register_knowledge_tool(registry: ToolRegistry, entity: EntityConfig, store: KnowledgeStore) -> None:
    fn = _make_update_knowledge_fn(entity, store)
    registry.register_fn(
        "update_knowledge",
        _UPDATE_KNOWLEDGE_DESCRIPTION,
        fn,
        parameters_schema={
            "type": "object",
            "properties": {
                "action": {"type": "string"},
                "section": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["action", "section"],
        },
    )
