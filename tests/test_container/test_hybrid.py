from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest
from click.testing import CliRunner

import ngram.main as main_mod
from ngram.container import (
    ContainerError,
    export_hybrid_entity,
    extract_archive,
    load_manifest,
    restore_artifact,
    restore_hybrid_artifact,
    verify_artifact,
)
from ngram.main import cli
from tests.test_container.test_migration import _legacy_entity


class _Transaction:
    def __init__(self, owner, options):
        self.owner = owner
        self.options = options

    async def __aenter__(self):
        self.owner.transactions.append(self.options)

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _PostgresSnapshot:
    def __init__(self, attachment_ref: str) -> None:
        self.attachment_ref = attachment_ref
        self.transactions = []
        self.closed = False

    def transaction(self, **options):
        return _Transaction(self, options)

    async def fetch(self, sql, *args):
        if "information_schema.tables" in sql:
            return [
                {"table_name": "episodes"},
                {"table_name": "ngram_entity_leases"},
            ]
        if "information_schema.columns" in sql:
            assert args == ("episodes",)
            return [
                {
                    "column_name": "id",
                    "data_type": "text",
                    "udt_name": "text",
                    "is_nullable": "NO",
                },
                {
                    "column_name": "summary",
                    "data_type": "text",
                    "udt_name": "text",
                    "is_nullable": "YES",
                },
            ]
        if "index.indisprimary" in sql:
            return [{"column_name": "id"}]
        if sql == 'SELECT * FROM "episodes"':
            return [
                {
                    "id": "hybrid-episode",
                    "summary": f"An attached image. [attachment_storage: {self.attachment_ref}]",
                }
            ]
        raise AssertionError(sql)

    async def close(self):
        self.closed = True


class _AttachmentStore:
    def __init__(self, reference: str, payload: bytes) -> None:
        self.reference = reference
        self.payload = payload

    async def get(self, key):
        return self.payload if key == self.reference else None

    async def put(self, key, data, content_type):
        assert data == self.payload
        assert content_type == "application/octet-stream"
        return f"s3://target-bucket/{key}"


class _TargetDatabase:
    def __init__(self) -> None:
        self.now = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
        self.table_names = set()
        self.lease = None
        self.inserts = []
        self.transactions = []

    async def connect(self, _dsn):
        return _TargetConnection(self)


class _TargetConnection:
    def __init__(self, target):
        self.target = target

    def transaction(self, **options):
        return _Transaction(self, options)

    @property
    def transactions(self):
        return self.target.transactions

    async def fetchval(self, sql, *args):
        assert sql == "SELECT CURRENT_TIMESTAMP"
        assert not args
        return self.target.now

    async def fetchrow(self, sql, *args):
        if "FROM ngram_entity_leases" in sql:
            return dict(self.target.lease) if self.target.lease else None
        raise AssertionError(sql)

    async def fetch(self, sql, *args):
        if "information_schema.tables" in sql:
            return [{"table_name": name} for name in sorted(self.target.table_names)]
        raise AssertionError(sql)

    async def execute(self, sql, *args):
        if "CREATE TABLE IF NOT EXISTS ngram_entity_leases" in sql:
            self.target.table_names.add("ngram_entity_leases")
            return
        if "INSERT INTO ngram_entity_leases" in sql:
            self.target.lease = {
                "entity_id": args[0],
                "holder": args[1],
                "token_hash": args[2],
                "generation": args[3],
                "acquired_at": args[4],
                "expires_at": args[5],
            }
            return
        if sql.startswith('CREATE TABLE "episodes"'):
            self.target.table_names.add("episodes")
            return
        if sql.startswith('INSERT INTO "episodes"'):
            self.target.inserts.append(args)
            return
        if sql.startswith("DELETE FROM ngram_entity_leases"):
            self.target.lease = None
            return
        raise AssertionError(sql)

    async def close(self):
        return None


@pytest.mark.asyncio
async def test_hybrid_export_is_complete_verified_and_locally_importable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(main_mod, "_load_repo_dotenv", lambda: None)
    config, entity_yaml = _legacy_entity(tmp_path)
    entity_id = "ng1:" + "c" * 40
    config.raw["continuity"] = {
        "entity_id": entity_id,
        "created_at": "2026-09-01T10:00:00+00:00",
    }
    config.database_url = lambda: "postgresql://fixture"
    config.harness.attachments.backend = "object_s3_compat"
    attachment_ref = "s3://source-bucket/object-a"
    pg = _PostgresSnapshot(attachment_ref)

    async def connect(_dsn):
        return pg

    output = tmp_path / "hybrid-export.ngram"
    result = await export_hybrid_entity(
        config,
        entity_yaml,
        output,
        runtime_version="test",
        lease_record={
            "type": "postgres-canonical-writer-v1",
            "state": "active",
            "entity_id": entity_id,
            "holder": "railway/worker",
            "generation": 4,
            "expires_at": datetime(2026, 9, 1, 11, 0, tzinfo=UTC).isoformat(),
            "token_included": False,
        },
        postgres_connect=connect,
        attachment_store=_AttachmentStore(attachment_ref, b"image-bytes"),
    )
    assert result == output.resolve()
    assert verify_artifact(output).ok is True
    assert pg.transactions == [
        {"isolation": "repeatable_read", "readonly": True, "deferrable": True}
    ]

    opened = tmp_path / "opened.ngram"
    extract_archive(output, opened)
    manifest = load_manifest(opened)
    assert manifest["entity_id"] == entity_id
    assert manifest["migration"]["database"] == "postgres-repeatable-read"
    assert manifest["lease"]["state"] == "dormant-export"
    assert manifest["lease"]["source_generation"] == 4
    assert manifest["lease"]["token_included"] is False
    inventory = json.loads(
        (opened / "autobiography" / "attachments" / "inventory.json").read_text(
            encoding="utf-8"
        )
    )
    assert inventory["objects"][0]["source_ref"] == attachment_ref

    restored_yaml = tmp_path / "restored" / "hybrid.yaml"
    restored_state = tmp_path / "restored" / "state"
    restore_artifact(output, restored_yaml, restored_state)
    conn = sqlite3.connect(restored_state / "memory.db")
    try:
        summary = conn.execute("SELECT summary FROM episodes").fetchone()[0]
    finally:
        conn.close()
    assert attachment_ref not in summary
    assert str(restored_state / "attachments") in summary
    assert next((restored_state / "attachments").glob("*.blob")).read_bytes() == b"image-bytes"

    target = _TargetDatabase()
    hybrid_yaml = tmp_path / "activated" / "hybrid.yaml"
    hybrid_state = tmp_path / "activated" / "state"
    activated = await restore_hybrid_artifact(
        output,
        hybrid_yaml,
        hybrid_state,
        database_url="postgresql://target",
        lease_holder="railway/new-worker",
        postgres_connect=target.connect,
        attachment_store=_AttachmentStore(attachment_ref, b"image-bytes"),
    )
    assert activated.entity_id == entity_id
    assert activated.lease.holder == "railway/new-worker"
    assert hybrid_yaml.is_file()
    assert hybrid_state.is_dir()
    assert not (hybrid_state / "memory.db").exists()
    assert target.table_names == {"episodes", "ngram_entity_leases"}
    assert attachment_ref not in target.inserts[0][1]
    assert target.inserts[0][1].startswith("An attached image. [attachment_storage: s3://target-bucket/")

    with pytest.raises(ContainerError, match="active canonical writer"):
        await restore_hybrid_artifact(
            output,
            tmp_path / "competing" / "hybrid.yaml",
            tmp_path / "competing" / "state",
            database_url="postgresql://target",
            lease_holder="local/competing-worker",
            postgres_connect=target.connect,
            attachment_store=_AttachmentStore(attachment_ref, b"image-bytes"),
        )

    missing_state = CliRunner().invoke(
        cli,
        [
            "import",
            str(output),
            "--activate-hybrid",
            "--database-url",
            "postgresql://target",
            "--lease-holder",
            "railway/new-worker",
        ],
    )
    assert missing_state.exit_code != 0
    assert "requires a fresh durable --state-root" in missing_state.output
