"""Complete hybrid snapshot orchestration for canonical .ngram artifacts."""

from __future__ import annotations

import shutil
import tempfile
import uuid
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from ngram.container.archive import extract_archive, pack_archive
from ngram.container.format import ContainerError, load_manifest, verify_container
from ngram.container.lease import CanonicalLease, PostgresLeaseManager
from ngram.container.migration import migrate_legacy_entity
from ngram.container.object_inventory import (
    export_attachment_inventory,
    load_attachment_inventory,
    materialize_attachments,
)
from ngram.container.postgres_snapshot import (
    ConnectFactory,
    restore_postgres_snapshot,
    snapshot_postgres,
)
from ngram.container.restore import (
    hydrate_runtime_state,
    portable_database_tables,
    runtime_config_from_container,
    runtime_entity_key,
)


@dataclass(frozen=True)
class HybridRestoreResult:
    entity_key: str
    entity_id: str
    entity_yaml: Path
    state_root: Path
    lease: CanonicalLease


async def export_hybrid_entity(
    config: Any,
    entity_yaml_path: Path,
    output_path: Path,
    *,
    runtime_version: str,
    lease_record: dict[str, Any],
    postgres_connect: ConnectFactory | None = None,
    attachment_store: Any | None = None,
) -> Path:
    """Snapshot Postgres + attachments, stage a container, verify, and archive it.

    ``lease_record`` must be a public record from the canonical source writer.
    The resulting artifact is dormant and requires a new lease before activation.
    """
    database_url = str(config.database_url() or "").strip()
    if not database_url.startswith(("postgresql://", "postgres://")):
        raise ContainerError("hybrid export requires a PostgreSQL database URL")
    if not isinstance(lease_record, dict) or not lease_record.get("generation"):
        raise ContainerError("hybrid export requires an inspected canonical writer lease")
    if str(lease_record.get("state") or "") != "active":
        raise ContainerError("hybrid export requires a current canonical writer lease")
    identity = str(lease_record.get("entity_id") or "")
    if not identity.startswith("ng1:"):
        raise ContainerError("hybrid export lease has no valid entity identity")
    public_lease = {
        "type": "postgres-canonical-writer-v1",
        "state": "dormant-export",
        "entity_id": str(lease_record.get("entity_id") or ""),
        "source_holder": str(lease_record.get("holder") or ""),
        "source_generation": int(lease_record["generation"]),
        "source_expires_at": str(lease_record.get("expires_at") or ""),
        "activation_required": True,
        "token_included": False,
    }
    tables = await snapshot_postgres(database_url, connect=postgres_connect)
    with tempfile.TemporaryDirectory(prefix="ngram-hybrid-export-") as temp_dir:
        temp = Path(temp_dir)
        attachment_snapshot: Path | None = None
        backend = str(getattr(config.harness.attachments, "backend", "local_disk") or "")
        if backend.strip().lower() == "object_s3_compat":
            if attachment_store is None:
                from ngram.storage.attachments import build_attachment_store

                attachment_store = build_attachment_store(config)
            attachment_snapshot = temp / "attachments"
            await export_attachment_inventory(attachment_store, tables, attachment_snapshot)
        directory = temp / "canonical.ngram"
        migrate_legacy_entity(
            config,
            entity_yaml_path,
            directory,
            runtime_version=runtime_version,
            database_tables=tables,
            attachment_snapshot=attachment_snapshot,
            lease_record=public_lease,
        )
        return pack_archive(directory, output_path)


async def restore_hybrid_artifact(
    artifact: Path,
    entity_yaml_path: Path,
    state_root: Path,
    *,
    database_url: str,
    lease_holder: str,
    postgres_connect: ConnectFactory | None = None,
    attachment_store: Any | None = None,
    lease_ttl_seconds: int = 120,
) -> HybridRestoreResult:
    """Activate a dormant portable snapshot on fresh Postgres and workspace targets."""
    artifact = artifact.expanduser().resolve()
    entity_yaml_path = entity_yaml_path.expanduser().resolve()
    state_root = state_root.expanduser().resolve()
    if entity_yaml_path.exists():
        raise ContainerError(f"entity config already exists: {entity_yaml_path}")
    if state_root.exists():
        raise ContainerError(f"entity state destination already exists: {state_root}")

    with tempfile.TemporaryDirectory(prefix="ngram-hybrid-import-") as temp_dir:
        if artifact.is_dir():
            root = artifact
        else:
            root = Path(temp_dir) / "opened.ngram"
            extract_archive(artifact, root)
        report = verify_container(root)
        if not report.ok:
            raise ContainerError("refusing to restore invalid container: " + "; ".join(report.errors))
        manifest = load_manifest(root)
        entity_id = str(manifest.get("entity_id") or "")
        lease_record = manifest.get("lease")
        if not isinstance(lease_record, dict) or not lease_record.get("activation_required"):
            raise ContainerError("hybrid activation requires a dormant canonical lease record")
        if str(lease_record.get("entity_id") or "") != entity_id:
            raise ContainerError("portable lease entity does not match the manifest")
        tables = portable_database_tables(root)
        inventory = load_attachment_inventory(root)
        if inventory["objects"]:
            if attachment_store is None:
                raise ContainerError("hybrid restore requires an object store for portable attachments")
            replacements = await materialize_attachments(root, object_store=attachment_store)
        else:
            replacements = {}

        stage = state_root.parent / f".{state_root.name}.{uuid.uuid4().hex}.tmp"
        yaml_temp = entity_yaml_path.with_name(
            f".{entity_yaml_path.name}.{uuid.uuid4().hex}.tmp"
        )
        stage_published = False
        yaml_published = False
        manager = PostgresLeaseManager(database_url, connect=postgres_connect)
        lease: CanonicalLease | None = None
        try:
            lease = await manager.acquire(
                entity_id,
                lease_holder,
                ttl_seconds=lease_ttl_seconds,
            )
            hydrate_runtime_state(root, stage)
            (stage / "memory.db").unlink(missing_ok=True)
            runtime_config = runtime_config_from_container(root, manifest)
            entity_yaml_path.parent.mkdir(parents=True, exist_ok=True)
            yaml_temp.write_text(
                yaml.safe_dump(runtime_config, sort_keys=False, allow_unicode=True),
                encoding="utf-8",
                newline="\n",
            )
            stage.replace(state_root)
            stage_published = True
            yaml_temp.replace(entity_yaml_path)
            yaml_published = True
            await restore_postgres_snapshot(
                database_url,
                tables,
                connect=postgres_connect,
                replacements=replacements,
                allowed_existing_tables={"ngram_entity_leases"},
            )
        except BaseException:
            yaml_temp.unlink(missing_ok=True)
            if yaml_published:
                entity_yaml_path.unlink(missing_ok=True)
            if stage_published:
                shutil.rmtree(state_root, ignore_errors=True)
            else:
                shutil.rmtree(stage, ignore_errors=True)
            if lease is not None:
                with suppress(Exception):
                    await manager.release(lease)
            raise
        assert lease is not None
        return HybridRestoreResult(
            entity_key=runtime_entity_key(str(manifest.get("display_name") or "entity")),
            entity_id=entity_id,
            entity_yaml=entity_yaml_path,
            state_root=state_root,
            lease=lease,
        )
