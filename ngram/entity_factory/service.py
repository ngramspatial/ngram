"""Canonical, model-free entity lifecycle operations.

The factory treats an entity YAML as a declaration. It never initializes an
inference provider, connects to a platform, or reads secret values. Deployment
plans name the environment variables an operator must provide without copying
their contents into generated artifacts.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from ngram.config import (
    HarnessConfig,
    entity_from_dict,
    load_harness_config,
    project_configs_dir,
)
from ngram.container.restore import runtime_entity_key
from ngram.genesis.creator import TEMPLATES
from ngram.genesis.schema import dump_entity

FACTORY_SCHEMA_VERSION = "ngram.entity-factory/v1"
_ARCHETYPES = ("curious", "gentle", "guardian", "sardonic")
_ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")
_SECRET_KEY = re.compile(r"(?:^|_)(?:api_?key|password|secret|token|private_?key)(?:$|_)")


class EntityFactoryError(RuntimeError):
    """Raised when an entity declaration cannot safely enter the lifecycle."""


@dataclass(frozen=True)
class EntityValidationReport:
    path: Path
    ok: bool
    entity_key: str
    display_name: str
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "ok": self.ok,
            "entity_key": self.entity_key,
            "display_name": self.display_name,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
        }


def resolve_entity_path(entity: str | Path) -> Path:
    """Resolve a YAML path or an entity key in ``configs/entities``."""
    candidate = Path(str(entity)).expanduser()
    if candidate.is_file():
        return candidate.resolve()
    key = runtime_entity_key(str(entity))
    path = project_configs_dir() / "entities" / f"{key}.yaml"
    if not path.is_file():
        raise EntityFactoryError(f"entity declaration not found: {path}")
    return path.resolve()


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise EntityFactoryError(f"invalid entity YAML {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise EntityFactoryError(f"entity YAML must contain an object: {path}")
    return value


def _archetype_data(archetype: str) -> dict[str, Any]:
    key = archetype.strip().lower()
    if key not in _ARCHETYPES:
        raise EntityFactoryError(
            f"unknown archetype {archetype!r}; choose one of {', '.join(_ARCHETYPES)}"
        )
    path = TEMPLATES / f"{key}.yaml"
    value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(value, dict):
        raise EntityFactoryError(f"archetype is not an object: {path}")
    return value


def _platform_declaration(surface: str, entity_key: str, display_name: str) -> dict[str, Any]:
    surface = surface.strip().lower()
    if surface == "cli":
        return {"type": "cli"}
    if surface == "telegram":
        return {
            "type": "telegram",
            "token_env": f"{entity_key.upper().replace('-', '_')}_TELEGRAM_TOKEN",
            "bot_name": display_name,
            "bot_short_description": f"{display_name}, a persistent entity built on ngram.",
        }
    if surface == "discord":
        return {
            "type": "discord",
            "token_env": f"{entity_key.upper().replace('-', '_')}_DISCORD_TOKEN",
        }
    raise EntityFactoryError("surface must be cli, telegram, or discord")


def create_entity_config(
    name: str,
    destination: Path,
    *,
    archetype: str = "curious",
    model: str | None = None,
    surface: str = "cli",
    harness: HarnessConfig | None = None,
    force: bool = False,
) -> Path:
    """Create one safe entity declaration without loading a model."""
    display_name = name.strip()
    if not display_name:
        raise EntityFactoryError("entity name is required")
    entity_key = runtime_entity_key(display_name)
    target = destination.expanduser()
    if (target.exists() and target.is_dir()) or target.suffix.lower() not in (
        ".yaml",
        ".yml",
    ):
        target = target / f"{entity_key}.yaml"
    if target.exists() and not force:
        raise EntityFactoryError(f"entity declaration already exists: {target}")

    active_harness = harness or load_harness_config()
    data = _archetype_data(archetype)
    selected_model = (model or active_harness.models.deliberate).strip()
    data = {
        **data,
        "name": display_name,
        "continuity": {
            "entity_id": "ng1:" + hashlib.sha256(secrets.token_bytes(32)).hexdigest()[:40],
            "created_at": datetime.now(UTC).isoformat(),
        },
        "cognition": {
            "reflex_model": selected_model,
            "deliberate_model": selected_model,
            "thinking_mode": active_harness.cognition.thinking_mode,
            "temperature": active_harness.cognition.temperature,
            "max_context_tokens": active_harness.cognition.max_context_tokens,
            "thinking_budget": active_harness.cognition.thinking_budget,
        },
        "autonomy": {"enabled": False},
        "automations": {"enabled": False},
        "presence": {
            "platforms": [_platform_declaration(surface, entity_key, display_name)],
            "daemon": {
                "heartbeat_interval": active_harness.presence.heartbeat_interval,
                "memory_consolidation": active_harness.memory.consolidation_interval,
            },
        },
    }
    entity_from_dict(active_harness, data)
    target.parent.mkdir(parents=True, exist_ok=True)
    dump_entity(data, target)
    return target.resolve()


def _literal_secret_paths(value: Any, path: str = "") -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for raw_key, item in value.items():
            key = str(raw_key)
            child = f"{path}.{key}" if path else key
            low = key.casefold()
            if low.endswith("_env"):
                if item and (not isinstance(item, str) or not _ENV_NAME.fullmatch(item)):
                    found.append(child)
                continue
            if _SECRET_KEY.search(low) and item not in (None, "", "<redacted>"):
                found.append(child)
                continue
            found.extend(_literal_secret_paths(item, child))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(_literal_secret_paths(item, f"{path}[{index}]"))
    return found


def validate_entity(
    entity: str | Path,
    *,
    harness: HarnessConfig | None = None,
) -> EntityValidationReport:
    """Validate schema, naming, surfaces, and secret hygiene without inference."""
    try:
        path = resolve_entity_path(entity)
        raw = _load_yaml(path)
    except EntityFactoryError as exc:
        return EntityValidationReport(Path(str(entity)), False, "", "", (str(exc),), ())

    errors: list[str] = []
    warnings: list[str] = []
    display_name = str(raw.get("name") or "").strip()
    entity_key = runtime_entity_key(display_name or path.stem)
    continuity = raw.get("continuity") if isinstance(raw.get("continuity"), dict) else {}
    entity_id = str(continuity.get("entity_id") or "")
    if entity_id and not re.fullmatch(r"ng1:[0-9a-f]{40}", entity_id):
        errors.append("continuity.entity_id must use the ng1:<40 lowercase hex> form")
    try:
        entity_from_dict(harness or load_harness_config(), raw)
    except (TypeError, ValueError, KeyError) as exc:
        errors.append(str(exc))

    secret_paths = _literal_secret_paths(raw)
    if secret_paths:
        errors.append(
            "literal credentials are forbidden in entity declarations: " + ", ".join(secret_paths)
        )
    if runtime_entity_key(path.stem) != entity_key:
        warnings.append(
            f"filename key {runtime_entity_key(path.stem)!r} differs from "
            f"display-name key {entity_key!r}"
        )
    presence = raw.get("presence") if isinstance(raw.get("presence"), dict) else {}
    platforms = presence.get("platforms") if isinstance(presence.get("platforms"), list) else []
    if not platforms:
        warnings.append("entity has no presence surface")
    types = [str(row.get("type") or "").lower() for row in platforms if isinstance(row, dict)]
    duplicates = sorted({name for name in types if name and types.count(name) > 1})
    if duplicates:
        errors.append("duplicate presence surfaces: " + ", ".join(duplicates))
    return EntityValidationReport(
        path=path,
        ok=not errors,
        entity_key=entity_key,
        display_name=display_name,
        errors=tuple(errors),
        warnings=tuple(warnings),
    )


def _effective_capabilities(raw: dict[str, Any], harness: HarnessConfig) -> dict[str, bool]:
    base = harness.tools if isinstance(harness.tools, dict) else {}
    override = raw.get("tools") if isinstance(raw.get("tools"), dict) else {}
    names = sorted(set(base) | set(override))
    out: dict[str, bool] = {}
    for name in names:
        base_row = base.get(name) if isinstance(base.get(name), dict) else {}
        over_row = override.get(name) if isinstance(override.get(name), dict) else {}
        enabled = over_row.get("enabled", base_row.get("enabled", True))
        out[name] = bool(enabled)
    return out


def build_entity_manifest(
    entity: str | Path,
    *,
    harness: HarnessConfig | None = None,
) -> dict[str, Any]:
    """Return the secret-free canonical declaration consumed by providers."""
    active_harness = harness or load_harness_config()
    report = validate_entity(entity, harness=active_harness)
    if not report.ok:
        raise EntityFactoryError("; ".join(report.errors))
    raw = _load_yaml(report.path)
    config = entity_from_dict(active_harness, raw)
    continuity = raw.get("continuity") if isinstance(raw.get("continuity"), dict) else {}
    platforms = []
    secret_env: set[str] = {
        "NGRAM_INFERENCE_GATEWAY_TOKEN",
        "NGRAM_CF_ACCESS_CLIENT_ID",
        "NGRAM_CF_ACCESS_CLIENT_SECRET",
    }
    for row in config.presence.platforms:
        if not isinstance(row, dict):
            continue
        item = {"type": str(row.get("type") or "").lower()}
        for key in ("bot_name", "bot_short_description", "bot_description"):
            if row.get(key):
                item[key] = str(row[key])
        token_env = str(row.get("token_env") or "").strip()
        if token_env:
            item["token_env"] = token_env
            secret_env.add(token_env)
        platforms.append(item)
    payload = report.path.read_bytes()
    return {
        "schema_version": FACTORY_SCHEMA_VERSION,
        "entity": {
            "key": report.entity_key,
            "display_name": report.display_name,
            "entity_id": str(continuity.get("entity_id") or "") or None,
            "created_at": str(continuity.get("created_at") or "") or None,
            "declaration": str(report.path),
            "declaration_sha256": hashlib.sha256(payload).hexdigest(),
        },
        "model": {
            "reflex": config.cognition.reflex_model,
            "deliberate": config.cognition.deliberate_model,
            "context_tokens": config.cognition.max_context_tokens,
        },
        "surfaces": platforms,
        "capabilities": {
            "tools": _effective_capabilities(raw, active_harness),
            "autonomy": bool(config.harness.autonomy.enabled),
            "automations": bool(config.automations.enabled),
        },
        "continuity": {
            "portable_format": "portable-local-v1",
            "database_target": "postgres",
            "workspace_mount": "/app/data",
            "single_writer_required": True,
        },
        "required_secret_env": sorted(secret_env),
    }


def build_deployment_plan(
    entity: str | Path,
    *,
    provider: str = "railway",
    harness: HarnessConfig | None = None,
) -> dict[str, Any]:
    """Build an inspectable provider plan. This function never mutates provider state."""
    if provider.strip().lower() != "railway":
        raise EntityFactoryError("only the railway planning provider is implemented")
    manifest = build_entity_manifest(entity, harness=harness)
    key = manifest["entity"]["key"]
    project = f"ngram-{key}"
    worker = f"{key}-worker"
    api = f"{key}-api"
    shared_gateway_secrets = {
        "NGRAM_INFERENCE_GATEWAY_TOKEN",
        "NGRAM_CF_ACCESS_CLIENT_ID",
        "NGRAM_CF_ACCESS_CLIENT_SECRET",
    }
    secret_bindings = {
        name: [worker, api] if name in shared_gateway_secrets else [worker]
        for name in manifest["required_secret_env"]
    }
    entity_id = str(manifest["entity"].get("entity_id") or "")
    common_settings = {
        "NGRAM_ENTITY": key,
        "NGRAM_DEPLOYMENT_MODE": "hybrid_railway",
        "NGRAM_INFERENCE_PROVIDER": "remote_gateway",
        **({"NGRAM_ENTITY_ID": entity_id} if entity_id else {}),
    }
    return {
        "schema_version": FACTORY_SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "provider": "railway",
        "entity": manifest["entity"],
        "project": {"name": project, "isolation_boundary": True},
        "resources": [
            {"kind": "service", "name": worker, "role": "authoritative-worker"},
            {"kind": "service", "name": api, "role": "health-api"},
            {"kind": "postgres", "name": "Postgres", "role": "durable-memory"},
            {
                "kind": "volume",
                "name": f"{key}-worker-volume",
                "service": worker,
                "mount": "/app/data",
            },
        ],
        "settings": {
            worker: {
                **common_settings,
                "NGRAM_CANONICAL_LEASE_REQUIRED": "true",
                "NGRAM_EXECUTION_REQUIRE_RAILWAY": "true",
                "NGRAM_EXECUTION_WORKSPACE_DIR": "/app/data",
                "DATABASE_URL": "${{Postgres.DATABASE_URL}}",
            },
            api: {
                **common_settings,
                "NGRAM_CANONICAL_LEASE_REQUIRED": "false",
                "DATABASE_URL": "${{Postgres.DATABASE_URL}}",
            },
        },
        "operator_inputs": {
            "required_values": {"NGRAM_INFERENCE_BASE_URL": [worker, api]},
            "required_secrets": secret_bindings,
        },
        "checks": [
            "entity declaration validates",
            "worker and API use the same Postgres service",
            "worker owns one persistent /app/data volume",
            "inference bearer is present but never embedded in this plan",
            "messaging tokens exist on the worker only",
            "public /ready reports both database and inference healthy",
            "no other entity project or volume is referenced",
        ],
    }


def entity_status(
    entity: str | Path,
    *,
    harness: HarnessConfig | None = None,
) -> dict[str, Any]:
    """Report declaration/container readiness without model or provider access."""
    active_harness = harness or load_harness_config()
    report = validate_entity(entity, harness=active_harness)
    result: dict[str, Any] = {"validation": report.as_dict(), "model_loaded": False}
    if not report.ok:
        return result
    manifest = build_entity_manifest(report.path, harness=active_harness)
    result["manifest"] = manifest
    result["lifecycle"] = {
        "declaration": "ready",
        "deployment_plan": "ready",
        "portable_snapshot": "not_created",
        "provider_state": "not_queried",
    }
    return result


def dumps(value: dict[str, Any]) -> str:
    """Stable human-inspectable JSON for CLI and Studio surfaces."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)
