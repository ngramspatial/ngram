"""Transactional PostgreSQL snapshots for portable ngram continuity.

Snapshots use a database-neutral column description plus JSON-safe rows. They
are read inside one read-only repeatable-read transaction, so every table in a
portable artifact represents the same logical point in time.
"""

from __future__ import annotations

import base64
from collections.abc import Awaitable, Callable, Mapping
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from ngram.container.format import ContainerError, canonical_json_bytes

ConnectFactory = Callable[[str], Awaitable[Any]]

_TABLES_SQL = """
SELECT table_name
FROM information_schema.tables
WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
ORDER BY table_name
"""

_COLUMNS_SQL = """
SELECT column_name, data_type, udt_name, is_nullable
FROM information_schema.columns
WHERE table_schema = 'public' AND table_name = $1
ORDER BY ordinal_position
"""

_PRIMARY_KEY_SQL = """
SELECT attribute.attname AS column_name
FROM pg_index AS index
JOIN pg_class AS relation ON relation.oid = index.indrelid
JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
JOIN unnest(index.indkey) WITH ORDINALITY AS key(attnum, ordering) ON TRUE
JOIN pg_attribute AS attribute
  ON attribute.attrelid = relation.oid AND attribute.attnum = key.attnum
WHERE namespace.nspname = 'public'
  AND relation.relname = $1
  AND index.indisprimary
ORDER BY key.ordering
"""


def quote_identifier(name: str) -> str:
    if not name or "\x00" in name:
        raise ContainerError(f"invalid PostgreSQL identifier: {name!r}")
    return '"' + name.replace('"', '""') + '"'


def json_safe(value: Any) -> Any:
    if isinstance(value, bytes | bytearray | memoryview):
        return {"$base64": base64.b64encode(bytes(value)).decode("ascii")}
    if isinstance(value, datetime):
        return {"$datetime": value.isoformat()}
    if isinstance(value, date):
        return {"$date": value.isoformat()}
    if isinstance(value, Decimal):
        return {"$decimal": str(value)}
    if isinstance(value, UUID):
        return {"$uuid": str(value)}
    if isinstance(value, Mapping):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [json_safe(item) for item in value]
    if value is None or isinstance(value, str | int | float | bool):
        return value
    return str(value)


def restore_value(value: Any) -> Any:
    if isinstance(value, dict):
        if set(value) == {"$base64"}:
            try:
                return base64.b64decode(str(value["$base64"]), validate=True)
            except (TypeError, ValueError) as exc:
                raise ContainerError("invalid base64 value in PostgreSQL snapshot") from exc
        if set(value) == {"$datetime"}:
            return datetime.fromisoformat(str(value["$datetime"]))
        if set(value) == {"$date"}:
            return date.fromisoformat(str(value["$date"]))
        if set(value) == {"$decimal"}:
            return Decimal(str(value["$decimal"]))
        if set(value) == {"$uuid"}:
            return UUID(str(value["$uuid"]))
        return {str(key): restore_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [restore_value(item) for item in value]
    return value


async def _default_connect(dsn: str) -> Any:
    try:
        import asyncpg
    except ImportError as exc:  # pragma: no cover - optional deployment dependency
        raise ContainerError("PostgreSQL continuity requires the railway extra (asyncpg)") from exc
    return await asyncpg.connect(dsn)


async def snapshot_postgres(
    dsn: str,
    *,
    connect: ConnectFactory | None = None,
    exclude_tables: set[str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Read all public tables at one repeatable database snapshot."""
    if not dsn.strip():
        raise ContainerError("PostgreSQL snapshot requires a database URL")
    connection = await (connect or _default_connect)(dsn)
    try:
        async with connection.transaction(
            isolation="repeatable_read",
            readonly=True,
            deferrable=True,
        ):
            table_rows = await connection.fetch(_TABLES_SQL)
            tables: dict[str, dict[str, Any]] = {}
            excluded = {"ngram_entity_leases", *(exclude_tables or set())}
            for table_row in table_rows:
                table_name = str(dict(table_row)["table_name"])
                if table_name in excluded:
                    continue
                columns_raw = await connection.fetch(_COLUMNS_SQL, table_name)
                primary_raw = await connection.fetch(_PRIMARY_KEY_SQL, table_name)
                columns = [
                    {
                        "name": str(dict(row)["column_name"]),
                        "data_type": str(dict(row)["data_type"]),
                        "udt_name": str(dict(row)["udt_name"]),
                        "nullable": str(dict(row)["is_nullable"]).upper() == "YES",
                    }
                    for row in columns_raw
                ]
                primary_key = [str(dict(row)["column_name"]) for row in primary_raw]
                raw_rows = await connection.fetch(
                    f"SELECT * FROM {quote_identifier(table_name)}"
                )
                rows = [json_safe(dict(row)) for row in raw_rows]
                rows.sort(key=canonical_json_bytes)
                tables[table_name] = {
                    "portable_schema": {
                        "source": "postgresql",
                        "columns": columns,
                        "primary_key": primary_key,
                    },
                    "rows": rows,
                }
            return tables
    except ContainerError:
        raise
    except Exception as exc:
        raise ContainerError(f"could not create PostgreSQL snapshot: {exc}") from exc
    finally:
        await connection.close()


def _postgres_type(column: dict[str, Any]) -> str:
    udt = str(column.get("udt_name") or "").lower()
    data_type = str(column.get("data_type") or "").lower()
    mapping = {
        "bool": "BOOLEAN",
        "bytea": "BYTEA",
        "date": "DATE",
        "float4": "REAL",
        "float8": "DOUBLE PRECISION",
        "int2": "SMALLINT",
        "int4": "INTEGER",
        "int8": "BIGINT",
        "json": "JSON",
        "jsonb": "JSONB",
        "numeric": "NUMERIC",
        "text": "TEXT",
        "timestamp": "TIMESTAMP WITHOUT TIME ZONE",
        "timestamptz": "TIMESTAMP WITH TIME ZONE",
        "uuid": "UUID",
        "varchar": "TEXT",
    }
    if udt in mapping:
        return mapping[udt]
    if data_type in {"character varying", "character", "text"}:
        return "TEXT"
    raise ContainerError(f"unsupported portable PostgreSQL type: {udt or data_type}")


def postgres_create_table_sql(name: str, portable_schema: dict[str, Any]) -> str:
    columns = portable_schema.get("columns")
    if not isinstance(columns, list) or not columns:
        raise ContainerError(f"portable table has no columns: {name}")
    primary = portable_schema.get("primary_key") or []
    if not isinstance(primary, list):
        raise ContainerError(f"portable table has invalid primary key: {name}")
    definitions: list[str] = []
    names: set[str] = set()
    for raw in columns:
        if not isinstance(raw, dict):
            raise ContainerError(f"portable table has invalid column: {name}")
        column_name = str(raw.get("name") or "")
        if column_name in names:
            raise ContainerError(f"portable table has duplicate column {column_name!r}: {name}")
        names.add(column_name)
        null_sql = "" if bool(raw.get("nullable")) else " NOT NULL"
        definitions.append(f"{quote_identifier(column_name)} {_postgres_type(raw)}{null_sql}")
    if any(str(item) not in names for item in primary):
        raise ContainerError(f"portable table primary key references a missing column: {name}")
    if primary:
        definitions.append(
            "PRIMARY KEY (" + ", ".join(quote_identifier(str(item)) for item in primary) + ")"
        )
    return f"CREATE TABLE {quote_identifier(name)} (" + ", ".join(definitions) + ")"


async def restore_postgres_snapshot(
    dsn: str,
    tables: dict[str, dict[str, Any]],
    *,
    connect: ConnectFactory | None = None,
    replacements: dict[str, str] | None = None,
    allowed_existing_tables: set[str] | None = None,
) -> None:
    """Restore into an empty public schema, atomically or not at all."""
    if not dsn.strip():
        raise ContainerError("PostgreSQL restore requires a database URL")
    connection = await (connect or _default_connect)(dsn)
    replace = replacements or {}
    try:
        async with connection.transaction(isolation="serializable"):
            existing = await connection.fetch(_TABLES_SQL)
            existing_names = {str(dict(row)["table_name"]) for row in existing}
            allowed = allowed_existing_tables or set()
            unexpected = sorted(existing_names - allowed)
            if unexpected:
                raise ContainerError(
                    "PostgreSQL restore target is not empty: " + ", ".join(unexpected)
                )
            for name in sorted(tables):
                if name in existing_names:
                    raise ContainerError(f"portable snapshot would replace an existing table: {name}")
                table = tables[name]
                schema = table.get("portable_schema") if isinstance(table, dict) else None
                if not isinstance(schema, dict):
                    raise ContainerError(f"table is not a PostgreSQL portable snapshot: {name}")
                await connection.execute(postgres_create_table_sql(name, schema))
            for name in sorted(tables):
                table = tables[name]
                schema = table["portable_schema"]
                columns = [str(row["name"]) for row in schema["columns"]]
                rows = table.get("rows") or []
                if not isinstance(rows, list):
                    raise ContainerError(f"portable table rows must be a list: {name}")
                for raw in rows:
                    if not isinstance(raw, dict):
                        raise ContainerError(f"portable table row must be an object: {name}")
                    values = []
                    for column in columns:
                        value = restore_value(raw.get(column))
                        if isinstance(value, str):
                            for old, new in replace.items():
                                value = value.replace(old, new)
                        values.append(value)
                    placeholders = ", ".join(f"${index}" for index in range(1, len(columns) + 1))
                    sql = (
                        f"INSERT INTO {quote_identifier(name)} ("
                        + ", ".join(quote_identifier(column) for column in columns)
                        + f") VALUES ({placeholders})"
                    )
                    await connection.execute(sql, *values)
    except ContainerError:
        raise
    except Exception as exc:
        raise ContainerError(f"could not restore PostgreSQL snapshot: {exc}") from exc
    finally:
        await connection.close()
