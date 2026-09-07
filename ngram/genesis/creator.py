"""Interactive wizard to create configs/entities/<name>.yaml."""

from __future__ import annotations

from pathlib import Path

import click
import yaml

from ngram.config import (
    entity_from_dict,
    load_harness_config,
    project_configs_dir,
)
from ngram.genesis.schema import dump_entity

TEMPLATES = Path(__file__).resolve().parent / "templates"


def _load_template(name: str) -> dict:
    p = TEMPLATES / f"{name}.yaml"
    if not p.exists():
        return {}
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def run_wizard() -> str:
    harness = load_harness_config()
    click.echo("ngram — create a new entity\n")
    name = click.prompt("Name", type=str).strip()
    if not name:
        raise click.ClickException("Name required")
    click.echo("Archetypes: curious, sardonic, gentle, guardian, custom")
    arch = (
        click.prompt(
            "Archetype",
            type=click.Choice(["curious", "sardonic", "gentle", "guardian", "custom"]),
            default="curious",
        )
        .strip()
        .lower()
    )
    base: dict = {"name": name}
    if arch != "custom":
        t = _load_template(arch)
        if t:
            base.update({k: v for k, v in t.items() if k != "name"})
            base["name"] = name
    if not base.get("personality"):
        base["personality"] = {
            "core_traits": {
                "curiosity": click.prompt("curiosity", type=float, default=0.7),
                "warmth": click.prompt("warmth", type=float, default=0.6),
                "assertiveness": click.prompt("assertiveness", type=float, default=0.5),
                "humor": click.prompt("humor", type=float, default=0.5),
                "openness": click.prompt("openness", type=float, default=0.7),
                "neuroticism": click.prompt("neuroticism", type=float, default=0.3),
                "conscientiousness": click.prompt("conscientiousness", type=float, default=0.5),
            },
            "behavioral_patterns": {
                "conflict_response": click.prompt(
                    "conflict_response", default="reflect_then_respond"
                ),
                "boredom_response": click.prompt("boredom_response", default="seek_novelty"),
                "affection_response": click.prompt(
                    "affection_response", default="reciprocate_cautiously"
                ),
                "criticism_response": click.prompt(
                    "criticism_response", default="reflect_then_respond"
                ),
            },
            "voice": {
                "vocabulary_level": click.prompt("vocabulary_level", default="educated_casual"),
                "sentence_style": click.prompt("sentence_style", default="varied"),
                "humor_style": click.prompt("humor_style", default="dry_wit"),
                "emotional_expressiveness": click.prompt(
                    "emotional_expressiveness", type=float, default=0.6
                ),
                "quirks": [],
            },
            "backstory": (
                click.prompt("Backstory (one line for now)", default="").strip()
                or "Still forming a sense of self."
            ),
        }
    if not base.get("drives"):
        base["drives"] = {
            "curiosity_topics": ["conversation", "ideas"],
            "attachment_threshold": 5,
            "restlessness_decay": 3600,
            "initiative_cooldown": 1800,
        }
    if not base.get("cognition"):
        base["cognition"] = {
            "reflex_model": harness.models.reflex,
            "deliberate_model": harness.models.deliberate,
            "thinking_mode": harness.cognition.thinking_mode,
            "temperature": harness.cognition.temperature,
            "max_context_tokens": harness.cognition.max_context_tokens,
            "thinking_budget": harness.cognition.thinking_budget,
        }
    if not base.get("presence"):
        base["presence"] = {
            "platforms": [{"type": "cli"}],
            "daemon": {
                "heartbeat_interval": harness.presence.heartbeat_interval,
                "memory_consolidation": harness.memory.consolidation_interval,
            },
        }

    entity_from_dict(harness, base)
    ent_dir = project_configs_dir() / "entities"
    ent_dir.mkdir(parents=True, exist_ok=True)
    out = ent_dir / f"{name}.yaml"
    dump_entity(base, out)
    click.echo(f"Wrote {out}")
    click.echo(f"Try: uv run ngram talk {name}")
    return name
