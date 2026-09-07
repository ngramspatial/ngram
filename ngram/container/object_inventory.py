"""Portable, verified attachment inventory for local and object-backed entities."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path
from typing import Any, Protocol

from ngram.container.format import ContainerError, write_json

_S3_REF = re.compile(r"s3://[A-Za-z0-9._-]+/[^\s\])>,;]+")


class AttachmentStore(Protocol):
    async def get(self, key: str) -> bytes | None: ...

    async def put(self, key: str, data: bytes, content_type: str | None) -> str: ...


def discover_attachment_refs(value: Any) -> list[str]:
    """Find stable object references anywhere in portable database records."""
    found: set[str] = set()

    def visit(item: Any) -> None:
        if isinstance(item, str):
            found.update(_S3_REF.findall(item))
        elif isinstance(item, dict):
            for child in item.values():
                visit(child)
        elif isinstance(item, list | tuple):
            for child in item:
                visit(child)

    visit(value)
    return sorted(found)


async def export_attachment_inventory(
    store: AttachmentStore,
    database_tables: dict[str, dict[str, Any]],
    destination: Path,
) -> dict[str, Any]:
    """Download every referenced blob, hash it, and write an inventory."""
    root = destination.expanduser().resolve()
    objects = root / "objects"
    objects.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    for reference in discover_attachment_refs(database_tables):
        try:
            data = await store.get(reference)
        except Exception as exc:
            raise ContainerError(f"could not read attachment {reference}: {exc}") from exc
        if data is None:
            raise ContainerError(f"referenced attachment is missing: {reference}")
        digest = hashlib.sha256(data).hexdigest()
        relative = f"objects/{digest}.blob"
        target = root / relative
        if target.is_file():
            if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
                raise ContainerError(f"attachment digest collision at {target}")
        else:
            target.write_bytes(data)
        rows.append(
            {
                "source_ref": reference,
                "sha256": digest,
                "bytes": len(data),
                "content_type": "application/octet-stream",
                "path": relative,
            }
        )
    inventory = {
        "version": 1,
        "credentials_included": False,
        "objects": rows,
    }
    write_json(root / "inventory.json", inventory)
    return inventory


def load_attachment_inventory(container_root: Path) -> dict[str, Any]:
    """Read and independently verify every inventoried blob."""
    attachment_root = container_root.expanduser().resolve() / "autobiography" / "attachments"
    path = attachment_root / "inventory.json"
    if not path.is_file():
        return {"version": 1, "credentials_included": False, "objects": []}
    try:
        inventory = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContainerError(f"invalid attachment inventory: {exc}") from exc
    if not isinstance(inventory, dict) or inventory.get("version") != 1:
        raise ContainerError("unsupported attachment inventory")
    if inventory.get("credentials_included") is not False:
        raise ContainerError("attachment inventory must not contain credentials")
    rows = inventory.get("objects")
    if not isinstance(rows, list):
        raise ContainerError("attachment inventory objects must be a list")
    seen_refs: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ContainerError("attachment inventory row must be an object")
        reference = str(row.get("source_ref") or "")
        relative = str(row.get("path") or "")
        digest = str(row.get("sha256") or "")
        if not reference or reference in seen_refs:
            raise ContainerError("attachment inventory has a missing or duplicate source reference")
        seen_refs.add(reference)
        target = (attachment_root / relative).resolve()
        try:
            target.relative_to(attachment_root)
        except ValueError as exc:
            raise ContainerError("attachment inventory path escapes its domain") from exc
        if not target.is_file():
            raise ContainerError(f"attachment inventory blob is missing: {relative}")
        data = target.read_bytes()
        if len(data) != int(row.get("bytes") or -1):
            raise ContainerError(f"attachment size mismatch: {relative}")
        if hashlib.sha256(data).hexdigest() != digest:
            raise ContainerError(f"attachment digest mismatch: {relative}")
    return inventory


async def materialize_attachments(
    container_root: Path,
    *,
    local_destination: Path | None = None,
    object_store: AttachmentStore | None = None,
) -> dict[str, str]:
    """Restore blobs and return old-reference to new-reference replacements."""
    if (local_destination is None) == (object_store is None):
        raise ContainerError("choose exactly one attachment restore target")
    root = container_root.expanduser().resolve()
    attachment_root = root / "autobiography" / "attachments"
    inventory = load_attachment_inventory(root)
    replacements: dict[str, str] = {}
    destination = local_destination.expanduser().resolve() if local_destination else None
    if destination:
        destination.mkdir(parents=True, exist_ok=True)
    for row in inventory["objects"]:
        source = (attachment_root / str(row["path"])).resolve()
        digest = str(row["sha256"])
        if destination is not None:
            target = destination / f"{digest}.blob"
            if target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest() != digest:
                raise ContainerError(f"existing restored attachment has the wrong digest: {target}")
            if not target.exists():
                shutil.copy2(source, target)
            replacement = str(target)
        else:
            assert object_store is not None
            replacement = await object_store.put(
                f"portable:{digest}",
                source.read_bytes(),
                str(row.get("content_type") or "application/octet-stream"),
            )
        replacements[str(row["source_ref"])] = replacement
    return replacements


def materialize_attachments_local(
    container_root: Path,
    local_destination: Path,
    *,
    reference_destination: Path | None = None,
) -> dict[str, str]:
    """Synchronous local restore used by the existing import pipeline."""
    root = container_root.expanduser().resolve()
    attachment_root = root / "autobiography" / "attachments"
    inventory = load_attachment_inventory(root)
    destination = local_destination.expanduser().resolve()
    reference_root = (
        reference_destination.expanduser().resolve() if reference_destination else destination
    )
    destination.mkdir(parents=True, exist_ok=True)
    replacements: dict[str, str] = {}
    for row in inventory["objects"]:
        source = (attachment_root / str(row["path"])).resolve()
        digest = str(row["sha256"])
        target = destination / f"{digest}.blob"
        if target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest() != digest:
            raise ContainerError(f"existing restored attachment has the wrong digest: {target}")
        if not target.exists():
            shutil.copy2(source, target)
        replacements[str(row["source_ref"])] = str(reference_root / target.name)
    return replacements
