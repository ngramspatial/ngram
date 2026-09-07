"""Restore a portable local container into the current runtime layout."""

from __future__ import annotations

import base64
import json
import re
import shutil
import sqlite3
import tempfile
import uuid
from pathlib import Path
from typing import Any

import yaml

from ngram.container.archive import extract_archive
from ngram.container.format import ContainerError, load_manifest, verify_container
from ngram.container.object_inventory import materialize_attachments_local


def runtime_entity_key(display_name: str) -> str:
    """Return the conservative filename/CLI key used by the legacy runtime."""
    chars = [ch.lower() if ch.isascii() and ch.isalnum() else "-" for ch in display_name.strip()]
    key = "".join(chars)
    while "--" in key:
        key = key.replace("--", "-")
    return key.strip("-") or "entity"


def _read_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContainerError(f"invalid portable JSON file: {path}") from exc


def _without_redactions(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): cleaned
            for key, item in value.items()
            if (cleaned := _without_redactions(item)) not in ("<redacted>", None)
        }
    if isinstance(value, list):
        return [
            cleaned
            for item in value
            if (cleaned := _without_redactions(item)) not in ("<redacted>", None)
        ]
    if value == "<redacted>":
        return None
    return value


def runtime_config_from_container(root: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    profile = _read_json(root / "identity" / "profile.json", {})
    preferences = _read_json(root / "agency" / "preferences.json", {})
    policies = _read_json(root / "agency" / "policies.json", {})
    tools = _read_json(root / "agency" / "tools.json", {})
    schedule = _read_json(root / "agency" / "schedule.json", {})
    embodiment = _read_json(root / "embodiment" / "profile.json", {})
    personality = profile.get("personality") or {}
    if isinstance(personality, dict) and isinstance(personality.get("behavioral_patterns"), list):
        personality = dict(personality)
        personality["behavioral_patterns"] = {
            f"pattern_{index + 1}": str(pattern)
            for index, pattern in enumerate(personality["behavioral_patterns"])
        }
    data: dict[str, Any] = {
        "name": str(profile.get("name") or manifest.get("display_name") or "Entity"),
        "continuity": {
            "entity_id": str(manifest.get("entity_id") or ""),
            "created_at": str(manifest.get("created_at") or ""),
        },
        "personality": personality,
        "drives": preferences.get("drives") or {},
        "cognition": preferences.get("cognition") or {},
        "presence": _without_redactions(policies.get("presence") or {}),
        "autonomy": _without_redactions(policies.get("autonomy") or {}),
        "automations": schedule.get("settings") or {},
        "embodiment": embodiment,
        "tools": _without_redactions(tools.get("native") or {}),
    }
    mcp = _without_redactions(tools.get("mcp_servers") or {})
    if mcp:
        data["mcp_servers"] = mcp
    if profile.get("values"):
        data["values"] = profile["values"]
    if profile.get("boundaries"):
        data["boundaries"] = profile["boundaries"]
    return data


def _json_restore(value: Any) -> Any:
    if isinstance(value, dict):
        if set(value) == {"$base64"}:
            try:
                return base64.b64decode(str(value["$base64"]), validate=True)
            except (ValueError, TypeError) as exc:
                raise ContainerError("invalid base64 value in portable database records") from exc
        for tag in ("$date", "$datetime", "$decimal", "$uuid"):
            if set(value) == {tag}:
                return str(value[tag])
        return {key: _json_restore(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_restore(item) for item in value]
    return value


def _quote_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


_CREATE_TABLE = re.compile(
    r"^CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?"
    r'(?:"(?P<double>[^"]+)"|`(?P<backtick>[^`]+)`|\[(?P<bracket>[^]]+)\]|(?P<plain>[A-Za-z_][A-Za-z0-9_]*))'
    r"\s*\(",
    re.IGNORECASE | re.DOTALL,
)


def _validated_table_schema(name: str, schema: str) -> str:
    """Accept only a matching CREATE TABLE statement from an untrusted artifact."""
    match = _CREATE_TABLE.match(schema.strip())
    if match is None:
        raise ContainerError(f"unsafe or invalid portable database schema: {name}")
    declared = next(value for value in match.groupdict().values() if value is not None)
    if declared != name:
        raise ContainerError(
            f"portable database schema name mismatch: record {name!r}, schema {declared!r}"
        )
    return schema


def _sqlite_type(column: dict[str, Any]) -> str:
    udt = str(column.get("udt_name") or "").lower()
    if udt in {"int2", "int4", "int8", "bool"}:
        return "INTEGER"
    if udt in {"float4", "float8", "numeric"}:
        return "REAL"
    if udt == "bytea":
        return "BLOB"
    if udt in {
        "date",
        "json",
        "jsonb",
        "text",
        "timestamp",
        "timestamptz",
        "uuid",
        "varchar",
    }:
        return "TEXT"
    raise ContainerError(f"unsupported portable database type: {udt or '(missing)'}")


def _sqlite_schema_from_portable(name: str, portable: dict[str, Any]) -> str:
    columns = portable.get("columns")
    primary = portable.get("primary_key") or []
    if not isinstance(columns, list) or not columns:
        raise ContainerError(f"portable database table has no columns: {name}")
    if not isinstance(primary, list):
        raise ContainerError(f"portable database table has invalid primary key: {name}")
    definitions: list[str] = []
    column_names: set[str] = set()
    for raw in columns:
        if not isinstance(raw, dict):
            raise ContainerError(f"portable database table has an invalid column: {name}")
        column_name = str(raw.get("name") or "")
        if not column_name or column_name in column_names:
            raise ContainerError(f"portable database table has an invalid column name: {name}")
        column_names.add(column_name)
        nullable = "" if bool(raw.get("nullable")) else " NOT NULL"
        definitions.append(f"{_quote_identifier(column_name)} {_sqlite_type(raw)}{nullable}")
    if any(str(column) not in column_names for column in primary):
        raise ContainerError(f"portable database primary key references a missing column: {name}")
    if primary:
        definitions.append(
            "PRIMARY KEY (" + ", ".join(_quote_identifier(str(column)) for column in primary) + ")"
        )
    return f"CREATE TABLE {_quote_identifier(name)} (" + ", ".join(definitions) + ")"


def _portable_tables(root: Path) -> dict[str, dict[str, Any]]:
    tables: dict[str, dict[str, Any]] = {}
    for path in (
        root / "autobiography" / "records.json",
        root / "soma" / "records.json",
        root / "agency" / "records.json",
        root / "agency" / "runtime-extension-records.json",
    ):
        value = _read_json(path, {})
        section = value.get("tables") if isinstance(value, dict) else None
        if not isinstance(section, dict):
            continue
        for name, table in section.items():
            if name in tables:
                raise ContainerError(f"duplicate portable database table: {name}")
            if not isinstance(table, dict):
                raise ContainerError(f"invalid portable database table: {name}")
            tables[str(name)] = table
    return tables


def portable_database_tables(root: Path) -> dict[str, dict[str, Any]]:
    """Return verified domain-partitioned database records for restore providers."""
    return _portable_tables(root)


def _replace_string_refs(value: Any, replacements: dict[str, str]) -> Any:
    if isinstance(value, str):
        for old, new in replacements.items():
            value = value.replace(old, new)
        return value
    if isinstance(value, dict):
        return {key: _replace_string_refs(item, replacements) for key, item in value.items()}
    if isinstance(value, list):
        return [_replace_string_refs(item, replacements) for item in value]
    return value


def _restore_database(
    root: Path,
    destination: Path,
    soma_snapshot: dict[str, Any],
    replacements: dict[str, str] | None = None,
) -> None:
    tables = _portable_tables(root)
    conn = sqlite3.connect(destination)
    try:
        for name, table in tables.items():
            schema = str(table.get("schema") or "").strip()
            portable = table.get("portable_schema")
            if schema:
                create_sql = _validated_table_schema(name, schema)
            elif isinstance(portable, dict):
                create_sql = _sqlite_schema_from_portable(name, portable)
            else:
                raise ContainerError(f"portable database table has no schema: {name}")
            conn.execute(create_sql)
        for name, table in tables.items():
            rows = table.get("rows") or []
            if not isinstance(rows, list):
                raise ContainerError(f"portable database rows must be a list: {name}")
            for raw in rows:
                if not isinstance(raw, dict) or not raw:
                    continue
                row = {
                    str(key): _replace_string_refs(_json_restore(value), replacements or {})
                    for key, value in raw.items()
                }
                columns = list(row)
                placeholders = ",".join("?" for _ in columns)
                column_sql = ",".join(_quote_identifier(column) for column in columns)
                conn.execute(
                    f"INSERT INTO {_quote_identifier(name)} ({column_sql}) VALUES ({placeholders})",
                    [row[column] for column in columns],
                )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS entity_state (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT OR REPLACE INTO entity_state (key, value) VALUES (?, ?)",
            ("soma_state_v2", json.dumps(soma_snapshot, separators=(",", ":"))),
        )
        conn.commit()
    except (sqlite3.Error, ContainerError) as exc:
        conn.rollback()
        if isinstance(exc, ContainerError):
            raise
        raise ContainerError(f"could not restore portable SQLite records: {exc}") from exc
    finally:
        conn.close()


def _soma_snapshot(root: Path) -> dict[str, Any]:
    state = _read_json(root / "soma" / "state.json", {})
    state["initial"] = _read_json(root / "soma" / "baselines.json", {})
    state["somatic_markers"] = _read_json(root / "soma" / "markers.json", {})
    noise_path = root / "soma" / "noise.log"
    fragments = []
    if noise_path.is_file():
        for line in noise_path.read_text(encoding="utf-8").splitlines():
            text = line.strip()
            if not text:
                continue
            try:
                value = json.loads(text)
            except json.JSONDecodeError:
                value = {"text": text, "salience": "reflective", "trace": ""}
            if isinstance(value, dict) and str(value.get("text") or "").strip():
                fragments.append(value)
    state["noise_fragments"] = fragments
    return state


def _copy_if_present(source: Path, destination: Path) -> None:
    if source.is_file():
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def _copy_tree(source: Path, destination: Path) -> None:
    if not source.is_dir():
        return
    for path in sorted(source.rglob("*"), key=lambda item: item.as_posix()):
        if path.is_symlink():
            raise ContainerError(f"symbolic links are not allowed in containers: {path}")
        target = destination / path.relative_to(source)
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif path.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)


def _populate_runtime_state(
    container_root: Path,
    stage: Path,
    manifest: dict[str, Any],
    database_filename: str,
    final_state_root: Path,
) -> None:
    soma = _soma_snapshot(container_root)
    attachment_domain = container_root / "autobiography" / "attachments"
    inventory_path = attachment_domain / "inventory.json"
    if inventory_path.is_file():
        replacements = materialize_attachments_local(
            container_root,
            stage / "attachments",
            reference_destination=final_state_root / "attachments",
        )
    else:
        replacements = {}
        _copy_tree(attachment_domain, stage / "attachments")
    _restore_database(container_root, stage / database_filename, soma, replacements)
    (stage / "soma").mkdir(parents=True, exist_ok=True)
    (stage / "soma" / "soma-state.json").write_text(
        json.dumps(soma, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    identity_metadata = {
        "entity_id": manifest["entity_id"],
        "created_at": manifest["created_at"],
        "profile": manifest.get("format_profile"),
    }
    (stage / "container-identity.json").write_text(
        json.dumps(identity_metadata, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    for source, target in (
        (container_root / "autobiography" / "knowledge.md", stage / "knowledge.md"),
        (container_root / "autobiography" / "journal.md", stage / "journal.md"),
        (
            container_root / "autobiography" / "autonomy-transcript.md",
            stage / "autonomy_transcript.md",
        ),
        (container_root / "agency" / "goals.json", stage / "projects.json"),
    ):
        _copy_if_present(source, target)
    _copy_tree(container_root / "autobiography" / "people", stage / "relationships")
    _copy_tree(container_root / "agency" / "skills", stage / "skills")
    _copy_tree(container_root / "agency" / "governance", stage / "governance")


def hydrate_runtime_state(
    container_root: Path,
    state_root: Path,
    *,
    database_filename: str = "memory.db",
) -> Path:
    """Hydrate a fresh disposable runtime cache from a verified canonical container."""
    container_root = container_root.expanduser().resolve()
    state_root = state_root.expanduser().resolve()
    if Path(database_filename).name != database_filename or not database_filename:
        raise ContainerError(f"invalid runtime database filename: {database_filename!r}")
    report = verify_container(container_root)
    if not report.ok:
        raise ContainerError("refusing to hydrate invalid container: " + "; ".join(report.errors))
    if state_root.exists():
        raise ContainerError(f"runtime cache already exists: {state_root}")
    manifest = load_manifest(container_root)
    stage = state_root.parent / f".{state_root.name}.{uuid.uuid4().hex}.tmp"
    stage.mkdir(parents=True)
    try:
        _populate_runtime_state(
            container_root,
            stage,
            manifest,
            database_filename,
            state_root,
        )
        stage.replace(state_root)
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    return state_root


def restore_legacy_entity(
    container_root: Path,
    entity_yaml_path: Path,
    state_root: Path,
    *,
    database_filename: str = "memory.db",
) -> str:
    """Restore one verified container into fresh current-runtime config/state paths."""
    container_root = container_root.expanduser().resolve()
    entity_yaml_path = entity_yaml_path.expanduser().resolve()
    state_root = state_root.expanduser().resolve()
    if Path(database_filename).name != database_filename or not database_filename:
        raise ContainerError(f"invalid runtime database filename: {database_filename!r}")
    report = verify_container(container_root)
    if not report.ok:
        raise ContainerError("refusing to restore invalid container: " + "; ".join(report.errors))
    if entity_yaml_path.exists():
        raise ContainerError(f"entity config already exists: {entity_yaml_path}")
    if state_root.exists():
        raise ContainerError(f"entity state destination already exists: {state_root}")

    manifest = load_manifest(container_root)
    runtime_config = runtime_config_from_container(container_root, manifest)
    entity_key = entity_yaml_path.stem
    stage = state_root.parent / f".{state_root.name}.{uuid.uuid4().hex}.tmp"
    yaml_temp = entity_yaml_path.with_name(f".{entity_yaml_path.name}.{uuid.uuid4().hex}.tmp")
    stage.mkdir(parents=True)
    state_moved = False
    try:
        _populate_runtime_state(
            container_root,
            stage,
            manifest,
            database_filename,
            state_root,
        )

        entity_yaml_path.parent.mkdir(parents=True, exist_ok=True)
        yaml_temp.write_text(
            yaml.safe_dump(runtime_config, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
            newline="\n",
        )
        stage.replace(state_root)
        state_moved = True
        yaml_temp.replace(entity_yaml_path)
    except BaseException:
        yaml_temp.unlink(missing_ok=True)
        if state_moved:
            shutil.rmtree(state_root, ignore_errors=True)
        else:
            shutil.rmtree(stage, ignore_errors=True)
        raise
    return entity_key


def restore_artifact(
    artifact: Path,
    entity_yaml_path: Path,
    state_root: Path,
    *,
    database_filename: str = "memory.db",
) -> str:
    artifact = artifact.expanduser().resolve()
    if artifact.is_dir():
        return restore_legacy_entity(
            artifact,
            entity_yaml_path,
            state_root,
            database_filename=database_filename,
        )
    with tempfile.TemporaryDirectory(prefix="ngram-import-") as temp_dir:
        opened = Path(temp_dir) / "opened.ngram"
        extract_archive(artifact, opened)
        return restore_legacy_entity(
            opened,
            entity_yaml_path,
            state_root,
            database_filename=database_filename,
        )
