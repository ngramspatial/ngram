from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from ngram.container import ContainerError
from ngram.container.postgres_snapshot import (
    postgres_create_table_sql,
    restore_postgres_snapshot,
    snapshot_postgres,
)


class _Transaction:
    def __init__(self, owner, options):
        self.owner = owner
        self.options = options

    async def __aenter__(self):
        self.owner.transactions.append(self.options)
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self.owner.transaction_errors.append(exc_type)


class _SnapshotConnection:
    def __init__(self) -> None:
        self.transactions = []
        self.transaction_errors = []
        self.closed = False

    def transaction(self, **options):
        return _Transaction(self, options)

    async def fetch(self, sql, *args):
        if "information_schema.tables" in sql:
            return [{"table_name": "episodes"}]
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
                    "column_name": "created_at",
                    "data_type": "timestamp with time zone",
                    "udt_name": "timestamptz",
                    "is_nullable": "NO",
                },
                {
                    "column_name": "weight",
                    "data_type": "numeric",
                    "udt_name": "numeric",
                    "is_nullable": "YES",
                },
            ]
        if "index.indisprimary" in sql:
            return [{"column_name": "id"}]
        if sql == 'SELECT * FROM "episodes"':
            return [
                {
                    "id": "second",
                    "created_at": datetime(2026, 9, 1, 10, 0, tzinfo=UTC),
                    "weight": Decimal("0.75"),
                },
                {
                    "id": "first",
                    "created_at": datetime(2026, 9, 1, 9, 0, tzinfo=UTC),
                    "weight": None,
                },
            ]
        raise AssertionError(sql)

    async def close(self):
        self.closed = True


class _RestoreConnection:
    def __init__(self, *, existing=False) -> None:
        self.existing = existing
        self.transactions = []
        self.transaction_errors = []
        self.executed = []
        self.closed = False

    def transaction(self, **options):
        return _Transaction(self, options)

    async def fetch(self, sql, *args):
        assert "information_schema.tables" in sql
        assert not args
        return [{"table_name": "already_here"}] if self.existing else []

    async def execute(self, sql, *args):
        self.executed.append((sql, args))

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_postgres_snapshot_is_repeatable_read_json_safe_and_deterministic() -> None:
    connection = _SnapshotConnection()

    async def connect(_dsn):
        return connection

    tables = await snapshot_postgres("postgresql://fixture", connect=connect)
    assert connection.transactions == [
        {"isolation": "repeatable_read", "readonly": True, "deferrable": True}
    ]
    assert connection.closed is True
    table = tables["episodes"]
    assert table["portable_schema"]["primary_key"] == ["id"]
    assert [row["id"] for row in table["rows"]] == ["first", "second"]
    assert table["rows"][1]["created_at"] == {"$datetime": "2026-09-01T10:00:00+00:00"}
    assert table["rows"][1]["weight"] == {"$decimal": "0.75"}


@pytest.mark.asyncio
async def test_postgres_restore_requires_empty_target_and_uses_serializable_transaction() -> None:
    schema = {
        "source": "postgresql",
        "columns": [
            {"name": "id", "udt_name": "text", "data_type": "text", "nullable": False},
            {"name": "note", "udt_name": "text", "data_type": "text", "nullable": True},
        ],
        "primary_key": ["id"],
    }
    tables = {
        "episodes": {
            "portable_schema": schema,
            "rows": [{"id": "ep-1", "note": "s3://old/object"}],
        }
    }
    connection = _RestoreConnection()

    async def connect(_dsn):
        return connection

    await restore_postgres_snapshot(
        "postgresql://fixture",
        tables,
        connect=connect,
        replacements={"s3://old/object": "s3://new/object"},
    )
    assert connection.transactions == [{"isolation": "serializable"}]
    assert connection.closed is True
    assert connection.executed[0][0] == (
        'CREATE TABLE "episodes" ("id" TEXT NOT NULL, "note" TEXT, PRIMARY KEY ("id"))'
    )
    assert connection.executed[1][1] == ("ep-1", "s3://new/object")

    occupied = _RestoreConnection(existing=True)

    async def connect_occupied(_dsn):
        return occupied

    with pytest.raises(ContainerError, match="not empty"):
        await restore_postgres_snapshot(
            "postgresql://fixture",
            tables,
            connect=connect_occupied,
        )
    assert occupied.closed is True


def test_postgres_portable_schema_rejects_unknown_types() -> None:
    with pytest.raises(ContainerError, match="unsupported portable PostgreSQL type"):
        postgres_create_table_sql(
            "unsafe",
            {
                "columns": [
                    {
                        "name": "payload",
                        "udt_name": "custom_extension_type",
                        "data_type": "USER-DEFINED",
                        "nullable": True,
                    }
                ],
                "primary_key": [],
            },
        )
