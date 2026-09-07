"""Entity YAML schema validation (delegates to config.entity_from_dict)."""

from __future__ import annotations

from pathlib import Path

import yaml

from ngram.config import HarnessConfig, entity_from_dict


def validate_entity_file(path: Path, harness: HarnessConfig) -> None:
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    entity_from_dict(harness, data)


def dump_entity(data: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True)
