"""Migration from the current runtime layout into a portable ngram directory."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import shutil
import sqlite3
import tempfile
import uuid
from collections.abc import Iterable
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ngram.container.archive import pack_archive
from ngram.container.format import (
    FORMAT_PROFILE,
    REQUIRED_DIRECTORIES,
    SPEC_VERSION,
    ContainerError,
    canonical_json_bytes,
    finalize_manifest,
    load_manifest,
    verify_container,
    write_json,
)

AUTOBIOGRAPHY_TABLES = {
    "episodes",
    "relationships",
    "relational_documents",
    "beliefs",
    "imprints",
    "narrative",
    "inner_voice",
    "evolution_log",
    "turn_outcomes",
}
SOMA_TABLES = {"entity_state", "seed_log"}
AGENCY_TABLES = {"reminders", "automations", "automation_runs"}

_SECRET_KEY = re.compile(
    r"(?:^|_)(?:api_?key|token|secret|password|credential)(?:$|_)",
    re.IGNORECASE,
)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _safe_name(value: str, fallback: str = "record") -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "-", (value or "").strip()).strip(".-")
    return (text[:120] or fallback).lower()


def _json_safe(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"$base64": base64.b64encode(value).decode("ascii")}
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _redact_secrets(value: Any, *, parent_key: str = "") -> Any:
    """Remove credentials while retaining human-readable binding structure."""
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            name = str(key)
            low = name.lower()
            is_env_name = low.endswith("_env") or low in {"token_env", "api_key_env"}
            if (not is_env_name and _SECRET_KEY.search(low)) or parent_key.lower() == "env":
                out[name] = "<redacted>" if item not in (None, "") else item
            else:
                out[name] = _redact_secrets(item, parent_key=name)
        return out
    if isinstance(value, list):
        return [_redact_secrets(item, parent_key=parent_key) for item in value]
    return _json_safe(value)


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = text.replace("\r\n", "\n")
    if payload and not payload.endswith("\n"):
        payload += "\n"
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(payload, encoding="utf-8", newline="\n")
    temp.replace(path)


def _copy_readable_tree(source: Path, destination: Path) -> int:
    if not source.is_dir():
        return 0
    copied = 0
    for path in sorted(source.rglob("*"), key=lambda p: p.as_posix()):
        if path.is_symlink():
            raise ContainerError(f"refusing to migrate symbolic link: {path}")
        rel = path.relative_to(source)
        target = destination / rel
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif path.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            copied += 1
    return copied


def _state_root(config: Any) -> Path:
    workspace = str(config.execution_workspace_dir() or "").strip()
    if workspace:
        return Path(workspace).expanduser()
    return Path(config.db_path()).expanduser().parent


def _governance_root(config: Any) -> Path:
    from ngram.governance.change_protocol import change_protocol_root

    return change_protocol_root(config)


def _load_or_create_identity_metadata(config: Any) -> dict[str, str]:
    """Persist a stable pre-key entity id beside legacy state for repeat exports."""
    root = _state_root(config)
    root.mkdir(parents=True, exist_ok=True)
    path = root / "container-identity.json"
    raw = getattr(config, "raw", {})
    continuity = raw.get("continuity") if isinstance(raw, dict) else None
    continuity = continuity if isinstance(continuity, dict) else {}
    declared_id = str(continuity.get("entity_id") or "")
    declared_created = str(continuity.get("created_at") or "")
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ContainerError(f"invalid legacy identity metadata: {path}") from exc
        entity_id = str(data.get("entity_id") or "")
        created_at = str(data.get("created_at") or "")
        if not entity_id.startswith("ng1:") or not created_at:
            raise ContainerError(f"invalid legacy identity metadata: {path}")
        if declared_id and declared_id != entity_id:
            raise ContainerError(
                "entity declaration continuity id does not match persisted identity metadata"
            )
        if declared_created and declared_created != created_at:
            raise ContainerError(
                "entity declaration creation time does not match persisted identity metadata"
            )
        return {"entity_id": entity_id, "created_at": created_at}
    if declared_id or declared_created:
        if not re.fullmatch(r"ng1:[0-9a-f]{40}", declared_id) or not declared_created:
            raise ContainerError("invalid continuity identity in entity declaration")
        data = {
            "entity_id": declared_id,
            "created_at": declared_created,
            "profile": FORMAT_PROFILE,
        }
        try:
            with path.open("x", encoding="utf-8", newline="\n") as handle:
                json.dump(data, handle, ensure_ascii=False, sort_keys=True, indent=2)
                handle.write("\n")
        except FileExistsError:
            return _load_or_create_identity_metadata(config)
        return {"entity_id": declared_id, "created_at": declared_created}
    data = {
        "entity_id": "ng1:" + hashlib.sha256(secrets.token_bytes(32)).hexdigest()[:40],
        "created_at": _utc_now(),
        "profile": FORMAT_PROFILE,
    }
    try:
        with path.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(data, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
    except FileExistsError:
        return _load_or_create_identity_metadata(config)
    return {"entity_id": data["entity_id"], "created_at": data["created_at"]}


def resolve_entity_identity(config: Any) -> dict[str, str]:
    """Return the entity's stable continuity identity, creating metadata once if needed."""
    return _load_or_create_identity_metadata(config)


def _identity_markdown(config: Any) -> str:
    raw_personality = config.raw.get("personality") or {}
    traits = raw_personality.get("core_traits") or {}
    patterns = raw_personality.get("behavioral_patterns") or {}
    values = config.raw.get("values") or []
    boundaries = config.raw.get("boundaries") or []
    lines = [f"# {config.name}", "", "## Origin and self-conception", ""]
    backstory = str(raw_personality.get("backstory") or "").strip()
    lines.append(backstory or "Not yet written.")
    lines.extend(["", "## Stable traits", ""])
    if isinstance(traits, dict) and traits:
        lines.extend(f"- {key}: {value}" for key, value in sorted(traits.items()))
    else:
        lines.append("- Not yet specified.")
    lines.extend(["", "## Behavioral patterns", ""])
    if isinstance(patterns, dict):
        lines.extend(f"- {key}: {value}" for key, value in sorted(patterns.items()))
    elif isinstance(patterns, list):
        lines.extend(f"- {item}" for item in patterns)
    else:
        lines.append("- Not yet specified.")
    lines.extend(["", "## Values", ""])
    lines.extend(f"- {item}" for item in values) if values else lines.append("- Not yet specified.")
    lines.extend(["", "## Boundaries", ""])
    lines.extend(f"- {item}" for item in boundaries) if boundaries else lines.append(
        "- Not yet specified."
    )
    return "\n".join(lines) + "\n"


def _quote_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _dump_sqlite(path: Path) -> dict[str, dict[str, Any]]:
    if not path.is_file():
        return {}
    try:
        conn = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        raise ContainerError(f"could not open SQLite memory at {path}: {exc}") from exc
    conn.row_factory = sqlite3.Row
    try:
        definitions = conn.execute(
            "SELECT name, sql FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        result: dict[str, dict[str, Any]] = {}
        for definition in definitions:
            name = str(definition["name"])
            rows = conn.execute(f"SELECT * FROM {_quote_identifier(name)}").fetchall()
            result[name] = {
                "schema": str(definition["sql"] or ""),
                "rows": [
                    {key: _json_safe(value) for key, value in dict(row).items()} for row in rows
                ],
            }
        return result
    except sqlite3.Error as exc:
        raise ContainerError(f"could not export SQLite memory at {path}: {exc}") from exc
    finally:
        conn.close()


def _partition_tables(tables: dict[str, dict[str, Any]]) -> tuple[dict, dict, dict, dict]:
    autobiography: dict[str, Any] = {}
    soma: dict[str, Any] = {}
    agency: dict[str, Any] = {}
    extensions: dict[str, Any] = {}
    for name, value in tables.items():
        if name in AUTOBIOGRAPHY_TABLES:
            autobiography[name] = value
        elif name in SOMA_TABLES:
            soma[name] = value
        elif name in AGENCY_TABLES:
            agency[name] = value
        else:
            extensions[name] = value
    return autobiography, soma, agency, extensions


def _table_rows(tables: dict[str, dict[str, Any]], name: str) -> list[dict[str, Any]]:
    table = tables.get(name) or {}
    rows = table.get("rows") if isinstance(table, dict) else []
    return list(rows) if isinstance(rows, list) else []


def _write_episode_views(root: Path, autobiography: dict[str, Any]) -> None:
    episodes_dir = root / "autobiography" / "episodes"
    episodes_dir.mkdir(parents=True, exist_ok=True)
    for index, row in enumerate(_table_rows(autobiography, "episodes")):
        episode_id = str(row.get("id") or f"episode-{index + 1}")
        summary = str(row.get("summary") or "").strip()
        reflection = str(row.get("self_reflection") or "").strip()
        readable = {key: value for key, value in row.items() if key != "embedding"}
        text = (
            f"# Episode {episode_id}\n\n"
            f"## Summary\n\n{summary or 'No summary recorded.'}\n\n"
            f"## Self-reflection\n\n{reflection or 'No reflection recorded.'}\n\n"
            "## Portable record\n\n```json\n"
            + json.dumps(readable, ensure_ascii=False, sort_keys=True, indent=2)
            + "\n```\n"
        )
        _write_text(episodes_dir / f"{_safe_name(episode_id, f'episode-{index + 1}')}.md", text)


def _write_people_views(root: Path, autobiography: dict[str, Any], existing_dir: Path) -> None:
    people_dir = root / "autobiography" / "people"
    people_dir.mkdir(parents=True, exist_ok=True)
    _copy_readable_tree(existing_dir, people_dir)
    docs = _table_rows(autobiography, "relational_documents")
    if docs:
        for index, row in enumerate(docs):
            person_id = str(row.get("person_id") or f"person-{index + 1}")
            target = people_dir / f"{_safe_name(person_id, f'person-{index + 1}')}.md"
            if target.exists():
                continue
            document = str(row.get("document") or "").strip()
            payload = {key: value for key, value in row.items() if key != "document"}
            _write_text(
                target,
                f"# {row.get('person_name') or person_id}\n\n{document}\n\n"
                "## Portable record\n\n```json\n"
                + json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2)
                + "\n```\n",
            )
        return
    for index, row in enumerate(_table_rows(autobiography, "relationships")):
        person_id = str(row.get("person_id") or f"person-{index + 1}")
        target = people_dir / f"{_safe_name(person_id, f'person-{index + 1}')}.md"
        if target.exists():
            continue
        _write_text(
            target,
            f"# {row.get('name') or person_id}\n\n```json\n"
            + json.dumps(row, ensure_ascii=False, sort_keys=True, indent=2)
            + "\n```\n",
        )


def _write_beliefs_and_biography(root: Path, autobiography: dict[str, Any], fallback: str) -> None:
    beliefs = _table_rows(autobiography, "beliefs")
    lines = ["# Beliefs", ""]
    if beliefs:
        for row in beliefs:
            content = str(row.get("content") or "").strip()
            confidence = row.get("confidence")
            source = row.get("source") or "unknown"
            lines.extend([f"## {content or row.get('id') or 'Belief'}", ""])
            lines.append(f"- Confidence: {confidence}")
            lines.append(f"- Source: {source}")
            lines.append("")
    else:
        lines.extend(["No beliefs recorded.", ""])
    _write_text(root / "autobiography" / "beliefs.md", "\n".join(lines))

    narratives = _table_rows(autobiography, "narrative")
    narratives.sort(key=lambda row: float(row.get("timestamp") or 0), reverse=True)
    story = str(narratives[0].get("self_story") or "").strip() if narratives else ""
    _write_text(
        root / "autobiography" / "biography.md",
        f"# Biography\n\n{story or fallback or 'Not yet written.'}\n",
    )


def _load_soma_snapshot(config: Any, soma_tables: dict[str, Any]) -> dict[str, Any]:
    state_path = Path(config.soma_dir()).expanduser() / "soma-state.json"
    if state_path.is_file():
        try:
            value = json.loads(state_path.read_text(encoding="utf-8"))
            if isinstance(value, dict):
                return value
        except (OSError, json.JSONDecodeError) as exc:
            raise ContainerError(f"invalid soma state: {state_path}") from exc
    for row in _table_rows(soma_tables, "entity_state"):
        if row.get("key") not in {"soma_state_v2", "soma_bar_state_v1"}:
            continue
        try:
            value = json.loads(str(row.get("value") or "{}"))
        except json.JSONDecodeError as exc:
            raise ContainerError("invalid soma state in entity_state table") from exc
        if isinstance(value, dict):
            return value
    return {}


def _write_soma(root: Path, snapshot: dict[str, Any], soma_tables: dict[str, Any]) -> None:
    values = snapshot.get("values") if isinstance(snapshot.get("values"), dict) else {}
    history = snapshot.get("history") if isinstance(snapshot.get("history"), list) else []
    state = {
        "values": values,
        "history": history,
        "ordered_names": snapshot.get("ordered_names") or list(values),
        "saved_at": snapshot.get("saved_at"),
        "affects": snapshot.get("affects") or [],
        "impulses": snapshot.get("impulses") or [],
        "conflicts": snapshot.get("conflicts") or [],
        "noise_coherent_streak": int(snapshot.get("noise_coherent_streak") or 0),
    }
    write_json(root / "soma" / "state.json", state)
    write_json(root / "soma" / "baselines.json", snapshot.get("initial") or {})
    write_json(root / "soma" / "markers.json", snapshot.get("somatic_markers") or {})
    fragments = snapshot.get("noise_fragments") or []
    noise_lines: list[str] = []
    for fragment in fragments:
        if isinstance(fragment, dict):
            text = str(fragment.get("text") or "").replace("\n", " ").strip()
            if text:
                row = dict(fragment)
                row["text"] = text
                noise_lines.append(canonical_json_bytes(row).decode("utf-8"))
        elif str(fragment).strip():
            row = {
                "text": str(fragment).replace("\n", " ").strip(),
                "salience": "reflective",
                "trace": "",
            }
            noise_lines.append(canonical_json_bytes(row).decode("utf-8"))
    _write_text(root / "soma" / "noise.log", "\n".join(noise_lines))
    if soma_tables:
        write_json(root / "soma" / "records.json", {"tables": soma_tables})


def _lineage_entry(entity_id: str, created_at: str, source: dict[str, Any]) -> dict[str, Any]:
    unsigned = {
        "sequence": 0,
        "timestamp": _utc_now(),
        "type": "legacy_migration_genesis",
        "previous": None,
        "entity_id": entity_id,
        "payload": source,
        "signature": None,
        "signature_profile": "none",
    }
    unsigned["entry_hash"] = "sha256:" + hashlib.sha256(canonical_json_bytes(unsigned)).hexdigest()
    return unsigned


def _write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    _write_text(path, "\n".join(canonical_json_bytes(row).decode("utf-8") for row in rows))


def _clear_managed_directory(root: Path, relative: str) -> Path:
    """Clear one known generated domain directory without accepting computed escape paths."""
    target = (root / relative).resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError as exc:
        raise ContainerError(f"managed directory escapes container: {relative}") from exc
    if target == root.resolve():
        raise ContainerError("refusing to clear container root")
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    return target


def _sync_optional_file(source: Path, destination: Path) -> None:
    if source.is_file():
        destination.parent.mkdir(parents=True, exist_ok=True)
        temp = destination.with_name(destination.name + ".tmp")
        shutil.copy2(source, temp)
        temp.replace(destination)
    elif destination.exists():
        destination.unlink()


def _append_lineage_transition(
    root: Path,
    *,
    entity_id: str,
    event_type: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    path = root / "lineage" / "chain.jsonl"
    rows: list[dict[str, Any]] = []
    if path.is_file():
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ContainerError(f"invalid lineage record at line {number}") from exc
            if not isinstance(row, dict):
                raise ContainerError(f"invalid lineage record at line {number}")
            unhashed = dict(row)
            recorded_hash = str(unhashed.pop("entry_hash", "") or "")
            expected_hash = "sha256:" + hashlib.sha256(canonical_json_bytes(unhashed)).hexdigest()
            if recorded_hash != expected_hash:
                raise ContainerError(f"lineage hash mismatch at line {number}")
            if rows and row.get("previous") != rows[-1].get("entry_hash"):
                raise ContainerError(f"broken lineage link at line {number}")
            if not rows and row.get("previous") is not None:
                raise ContainerError("lineage genesis must not have a previous entry")
            if row.get("sequence") != len(rows):
                raise ContainerError(f"invalid lineage sequence at line {number}")
            if row.get("entity_id") != entity_id:
                raise ContainerError(f"lineage entity mismatch at line {number}")
            rows.append(row)
    previous = str(rows[-1].get("entry_hash") or "") if rows else None
    entry: dict[str, Any] = {
        "sequence": int(rows[-1].get("sequence", -1)) + 1 if rows else 0,
        "timestamp": _utc_now(),
        "type": event_type,
        "previous": previous,
        "entity_id": entity_id,
        "payload": _json_safe(payload),
        "signature": None,
        "signature_profile": "none",
    }
    entry["entry_hash"] = "sha256:" + hashlib.sha256(canonical_json_bytes(entry)).hexdigest()
    rows.append(entry)
    _write_jsonl(path, rows)
    return entry


def checkpoint_runtime_state(
    config: Any,
    container_root: Path,
    *,
    event_type: str,
    event_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Project the derived runtime cache into canonical domains and advance lineage."""
    root = container_root.expanduser().resolve()
    manifest = load_manifest(root)
    if str(manifest.get("entity_id") or "") == "":
        raise ContainerError("container manifest has no entity_id")

    tables = _dump_sqlite(Path(config.db_path()).expanduser())
    autobiography, soma_tables, agency_tables, extension_tables = _partition_tables(tables)
    write_json(root / "autobiography" / "records.json", {"tables": autobiography})
    write_json(root / "soma" / "records.json", {"tables": soma_tables})
    write_json(root / "agency" / "records.json", {"tables": agency_tables})
    write_json(
        root / "agency" / "runtime-extension-records.json",
        {"tables": extension_tables},
    )

    _clear_managed_directory(root, "autobiography/episodes")
    _write_episode_views(root, autobiography)
    _clear_managed_directory(root, "autobiography/people")
    _write_people_views(root, autobiography, Path(config.relationships_dir()).expanduser())
    profile = _load_json_object(root / "identity" / "profile.json")
    personality = profile.get("personality")
    if not isinstance(personality, dict):
        personality = {}
        profile["personality"] = personality
    personality["core_traits"] = dict(config.personality.core_traits)
    personality["behavioral_patterns"] = dict(config.personality.behavioral_patterns)
    write_json(root / "identity" / "profile.json", profile)

    preferences = _load_json_object(root / "agency" / "preferences.json")
    drives = preferences.get("drives")
    if not isinstance(drives, dict):
        drives = {}
        preferences["drives"] = drives
    drives["curiosity_topics"] = list(config.drives.curiosity_topics)
    write_json(root / "agency" / "preferences.json", preferences)

    backstory = str(personality.get("backstory") or "").strip()
    _write_beliefs_and_biography(root, autobiography, backstory)
    for source_name, target_name in (
        (config.knowledge_path(), "knowledge.md"),
        (config.journal_path(), "journal.md"),
        (config.autonomy_transcript_path(), "autonomy-transcript.md"),
    ):
        _sync_optional_file(
            Path(source_name).expanduser(),
            root / "autobiography" / target_name,
        )

    _clear_managed_directory(root, "agency/skills")
    _copy_readable_tree(Path(config.skills_dir()).expanduser(), root / "agency" / "skills")
    _clear_managed_directory(root, "agency/governance")
    _copy_readable_tree(_governance_root(config), root / "agency" / "governance")
    _sync_optional_file(
        Path(config.projects_path()).expanduser(),
        root / "agency" / "goals.json",
    )
    _clear_managed_directory(root, "autobiography/attachments")
    attachment_dir = _runtime_attachment_dir(config)
    if attachment_dir is not None:
        _copy_readable_tree(attachment_dir, root / "autobiography" / "attachments")

    snapshot = _load_soma_snapshot(config, soma_tables)
    _write_soma(root, snapshot, soma_tables)

    entry = _append_lineage_transition(
        root,
        entity_id=str(manifest["entity_id"]),
        event_type=event_type,
        payload=event_payload or {},
    )
    manifest["last_transition_at"] = entry["timestamp"]
    security = manifest.get("security")
    if isinstance(security, dict):
        security["local_single_process_lock"] = True
        security["canonical_lease_enforced"] = False
    return finalize_manifest(root, manifest)


def commit_canonical_json_update(
    container_root: Path,
    relative_path: str,
    value: dict[str, Any],
    *,
    event_type: str,
    event_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Write one approved canonical JSON document and advance integrity-covered lineage.

    The caller must hold the container's exclusive local lock. This helper intentionally
    accepts only Studio's narrow settings surface rather than becoming an arbitrary file writer.
    """
    allowed = {
        "agency/preferences.json",
        "agency/policies.json",
        "agency/schedule.json",
        "embodiment/profile.json",
    }
    if relative_path not in allowed:
        raise ContainerError(f"canonical JSON path is not editable: {relative_path}")
    if not isinstance(value, dict):
        raise ContainerError("canonical settings document must be a JSON object")
    root = container_root.expanduser().resolve()
    report = verify_container(root)
    if not report.ok:
        raise ContainerError(
            "refusing to edit an invalid container: " + "; ".join(report.errors)
        )
    manifest = load_manifest(root)
    write_json(root / relative_path, value)
    entry = _append_lineage_transition(
        root,
        entity_id=str(manifest.get("entity_id") or ""),
        event_type=event_type,
        payload={"path": relative_path, **(event_payload or {})},
    )
    manifest["last_transition_at"] = entry["timestamp"]
    return finalize_manifest(root, manifest)


def _load_json_object(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContainerError(f"invalid JSON state file: {path}") from exc
    if not isinstance(value, dict):
        raise ContainerError(f"JSON state file must contain an object: {path}")
    return value


def _runtime_attachment_dir(config: Any) -> Path | None:
    override = str(getattr(config, "runtime_cache_dir", "") or "").strip()
    if override:
        return Path(override).expanduser() / "attachments"
    attachment_settings = getattr(config.harness, "attachments", None)
    if attachment_settings is None:
        return None
    if str(getattr(attachment_settings, "backend", "local_disk")).lower() != "local_disk":
        raise ContainerError("live containers currently require local_disk attachment storage")
    template = str(getattr(attachment_settings, "local_dir", "") or "")
    return Path(template.replace("{entity_name}", config.name)).expanduser() if template else None


def migrate_legacy_entity(
    config: Any,
    entity_yaml_path: Path,
    destination: Path,
    *,
    runtime_version: str,
    database_tables: dict[str, dict[str, Any]] | None = None,
    attachment_snapshot: Path | None = None,
    lease_record: dict[str, Any] | None = None,
) -> Path:
    """Create a canonical portable-local directory from one current-runtime entity."""
    destination = destination.expanduser().resolve()
    if destination.exists():
        raise ContainerError(f"destination already exists: {destination}")
    database_url = str(config.database_url() or "").strip()
    if database_url and database_tables is None:
        raise ContainerError(
            "portable Postgres export requires a repeatable-read snapshot; "
            "refusing to create an incomplete ngram"
        )
    if not entity_yaml_path.is_file():
        raise ContainerError(f"entity YAML not found: {entity_yaml_path}")

    identity_meta = _load_or_create_identity_metadata(config)
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = destination.parent / f".{destination.name}.{uuid.uuid4().hex}.tmp"
    stage.mkdir()
    try:
        for rel in REQUIRED_DIRECTORIES:
            (stage / rel).mkdir(parents=True, exist_ok=True)
        (stage / "autobiography" / "episodes").mkdir()
        (stage / "autobiography" / "people").mkdir()
        (stage / "agency" / "skills").mkdir()
        (stage / "world" / "places").mkdir()
        (stage / "lineage" / "attestations" / "probes").mkdir(parents=True)

        raw = deepcopy(config.raw)
        personality = raw.get("personality") or {}
        _write_text(stage / "identity" / "identity.md", _identity_markdown(config))
        write_json(stage / "identity" / "voice.json", personality.get("voice") or {})
        write_json(
            stage / "identity" / "profile.json",
            {
                "name": config.name,
                "personality": personality,
                "values": raw.get("values") or [],
                "boundaries": raw.get("boundaries") or [],
            },
        )

        merged_tools = (
            config._merged_tools() if hasattr(config, "_merged_tools") else raw.get("tools") or {}
        )
        write_json(
            stage / "agency" / "tools.json",
            _redact_secrets(
                {
                    "native": merged_tools,
                    "mcp_servers": raw.get("mcp_servers") or {},
                    "credentials_included": False,
                }
            ),
        )
        write_json(
            stage / "agency" / "policies.json",
            _redact_secrets(
                {
                    "presence": raw.get("presence") or {},
                    "autonomy": raw.get("autonomy") or {},
                }
            ),
        )
        write_json(
            stage / "agency" / "preferences.json",
            {
                "drives": raw.get("drives") or {},
                "cognition": {
                    key: value
                    for key, value in (raw.get("cognition") or {}).items()
                    if key not in {"reflex_model", "deliberate_model", "model"}
                },
            },
        )
        write_json(
            stage / "agency" / "schedule.json",
            {"settings": raw.get("automations") or {}},
        )
        _copy_readable_tree(Path(config.skills_dir()).expanduser(), stage / "agency" / "skills")
        _copy_readable_tree(_governance_root(config), stage / "agency" / "governance")
        projects = Path(config.projects_path()).expanduser()
        if projects.is_file():
            shutil.copy2(projects, stage / "agency" / "goals.json")

        attachment_settings = getattr(config.harness, "attachments", None)
        if attachment_settings is not None:
            attachment_backend = (
                str(getattr(attachment_settings, "backend", "local_disk") or "local_disk")
                .strip()
                .lower()
            )
            if attachment_backend == "object_s3_compat":
                if attachment_snapshot is None:
                    raise ContainerError(
                        "portable object-storage attachment export requires a verified snapshot"
                    )
                _copy_readable_tree(
                    attachment_snapshot,
                    stage / "autobiography" / "attachments",
                )
            else:
                attachment_template = str(getattr(attachment_settings, "local_dir", "") or "")
                if attachment_template:
                    attachment_dir = Path(
                        attachment_template.replace("{entity_name}", config.name)
                    ).expanduser()
                    _copy_readable_tree(
                        attachment_dir,
                        stage / "autobiography" / "attachments",
                    )

        db_path = Path(config.db_path()).expanduser()
        tables = database_tables if database_tables is not None else _dump_sqlite(db_path)
        autobiography, soma_tables, agency_tables, extension_tables = _partition_tables(tables)
        if autobiography:
            write_json(stage / "autobiography" / "records.json", {"tables": autobiography})
        if agency_tables:
            write_json(stage / "agency" / "records.json", {"tables": agency_tables})
        if extension_tables:
            write_json(
                stage / "agency" / "runtime-extension-records.json",
                {"tables": extension_tables},
            )
        _write_episode_views(stage, autobiography)
        _write_people_views(stage, autobiography, Path(config.relationships_dir()).expanduser())
        _write_beliefs_and_biography(
            stage,
            autobiography,
            str(personality.get("backstory") or "").strip(),
        )
        for source_name, target_name in (
            (config.knowledge_path(), "knowledge.md"),
            (config.journal_path(), "journal.md"),
            (config.autonomy_transcript_path(), "autonomy-transcript.md"),
        ):
            source = Path(source_name).expanduser()
            if source.is_file():
                shutil.copy2(source, stage / "autobiography" / target_name)

        snapshot = _load_soma_snapshot(config, soma_tables)
        _write_soma(stage, snapshot, soma_tables)
        raw_soma_dynamics = getattr(config.harness, "soma", {})
        soma_dynamics = raw_soma_dynamics if isinstance(raw_soma_dynamics, dict) else {}
        write_json(stage / "soma" / "dynamics.json", _json_safe(soma_dynamics))

        embodiment = raw.get("embodiment") or {}
        write_json(
            stage / "embodiment" / "profile.json",
            {
                "rig": embodiment.get("rig") or "unbound",
                "motion_tendencies": embodiment.get("motion_tendencies") or {},
                "gesture_vocabulary": embodiment.get("gesture_vocabulary") or [],
                "fallbacks": embodiment.get("fallbacks") or ["procedural-only"],
            },
        )
        _write_text(stage / "embodiment" / "kine.md", "# Motor narrative\n\nNot yet initialized.\n")
        write_json(stage / "world" / "anchors.json", {})

        source = {
            "kind": "ngram-runtime-hybrid" if database_tables is not None else "ngram-runtime-legacy",
            "entity_yaml": entity_yaml_path.name,
            "database": (
                "postgres-repeatable-read"
                if database_tables is not None
                else ("sqlite" if db_path.is_file() else "none")
            ),
            "migrated_at": _utc_now(),
        }
        _write_jsonl(
            stage / "lineage" / "chain.jsonl",
            [_lineage_entry(identity_meta["entity_id"], identity_meta["created_at"], source)],
        )

        model = str(config.effective_deliberate_model() or "")
        provider = str(getattr(config.harness.inference, "provider", "") or "local")
        manifest = {
            "spec_version": SPEC_VERSION,
            "format_profile": FORMAT_PROFILE,
            "entity_id": identity_meta["entity_id"],
            "display_name": config.name,
            "created_at": identity_meta["created_at"],
            "genesis": identity_meta["entity_id"],
            "parent": None,
            "conformance": "development-portable-local",
            "substrate_history": [
                {
                    "from": identity_meta["created_at"],
                    "model": model,
                    "provider": provider,
                    "runtime": f"ngram-runtime/{runtime_version}",
                }
            ],
            "lease": _json_safe(lease_record) if lease_record else None,
            "security": {
                "profile": "filesystem-local",
                "encrypted": False,
                "signed_lineage": False,
                "canonical_lease_enforced": bool(lease_record),
                "warning": "Integrity-checked; confidentiality depends on filesystem protection.",
            },
            "transport": {"archive": "tar", "compression": "none"},
            "migration": source,
        }
        finalize_manifest(stage, manifest)
        stage.replace(destination)
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    return destination


def export_legacy_entity(
    config: Any,
    entity_yaml_path: Path,
    output_path: Path,
    *,
    runtime_version: str,
) -> Path:
    """Migrate into a temporary canonical directory, verify it, then archive it."""
    with tempfile.TemporaryDirectory(prefix="ngram-export-") as temp_dir:
        directory = Path(temp_dir) / f"{_safe_name(config.name, 'entity')}.ngram"
        migrate_legacy_entity(
            config,
            entity_yaml_path,
            directory,
            runtime_version=runtime_version,
        )
        return pack_archive(directory, output_path)
