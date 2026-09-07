from __future__ import annotations

from pathlib import Path

import pytest

from ngram.container import ContainerError
from ngram.container.object_inventory import (
    discover_attachment_refs,
    export_attachment_inventory,
    load_attachment_inventory,
    materialize_attachments,
)


class _Store:
    def __init__(self, objects=None) -> None:
        self.objects = dict(objects or {})
        self.puts = []

    async def get(self, key):
        return self.objects.get(key)

    async def put(self, key, data, content_type):
        self.puts.append((key, data, content_type))
        return f"s3://restored/{key}"


def test_attachment_reference_discovery_is_recursive_and_deduplicated() -> None:
    value = {
        "rows": [
            {"note": "[attachment_storage: s3://bucket/one; s3://bucket/two]"},
            {"direct": "s3://bucket/one"},
        ]
    }
    assert discover_attachment_refs(value) == ["s3://bucket/one", "s3://bucket/two"]


@pytest.mark.asyncio
async def test_attachment_inventory_exports_verifies_and_restores_both_targets(
    tmp_path: Path,
) -> None:
    source_ref = "s3://source/object"
    store = _Store({source_ref: b"durable-image"})
    attachment_root = tmp_path / "entity.ngram" / "autobiography" / "attachments"
    tables = {"episodes": {"rows": [{"summary": f"saved at {source_ref}"}]}}
    inventory = await export_attachment_inventory(store, tables, attachment_root)
    assert inventory["credentials_included"] is False
    assert inventory["objects"][0]["source_ref"] == source_ref

    verified = load_attachment_inventory(tmp_path / "entity.ngram")
    assert verified == inventory

    local = tmp_path / "restored"
    local_map = await materialize_attachments(
        tmp_path / "entity.ngram",
        local_destination=local,
    )
    assert Path(local_map[source_ref]).read_bytes() == b"durable-image"

    target_store = _Store()
    object_map = await materialize_attachments(
        tmp_path / "entity.ngram",
        object_store=target_store,
    )
    assert object_map[source_ref].startswith("s3://restored/portable:")
    assert target_store.puts[0][1] == b"durable-image"


@pytest.mark.asyncio
async def test_attachment_inventory_fails_closed_for_missing_or_modified_blobs(
    tmp_path: Path,
) -> None:
    root = tmp_path / "entity.ngram"
    attachment_root = root / "autobiography" / "attachments"
    with pytest.raises(ContainerError, match="missing"):
        await export_attachment_inventory(
            _Store(),
            {"episodes": {"rows": [{"summary": "s3://source/missing"}]}},
            attachment_root,
        )

    await export_attachment_inventory(
        _Store({"s3://source/ok": b"original"}),
        {"episodes": {"rows": [{"summary": "s3://source/ok"}]}},
        attachment_root,
    )
    blob = next((attachment_root / "objects").glob("*.blob"))
    blob.write_bytes(b"tampered")
    with pytest.raises(ContainerError, match="mismatch"):
        load_attachment_inventory(root)
