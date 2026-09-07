from __future__ import annotations

from pathlib import Path

import yaml

from ngram.config import HarnessConfig, entity_from_dict
from ngram.identity.personality import PersonalityEngine
from ngram.models import EmotionalState, EmotionCategory

ROOT = Path(__file__).resolve().parents[2]
ROOK_CONFIG = ROOT / "configs" / "entities" / "rook.yaml"
ROOK_BIBLE = ROOT / "characters" / "rook.md"
ROOK_EVALS = ROOT / "configs" / "model-evals" / "rook.yaml"


def _yaml(path: Path) -> dict:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_rook_entity_is_loadable_with_bounded_autonomy() -> None:
    raw = _yaml(ROOK_CONFIG)
    entity = entity_from_dict(HarnessConfig(), raw)

    assert entity.name == "Rook"
    assert entity.cognition.reflex_model == "gemma4:26b"
    assert entity.cognition.deliberate_model == "gemma4:26b"
    assert entity.cognition.use_api_tools is True
    assert entity.harness.autonomy.enabled is True
    assert entity.harness.autonomy.min_cycle_gap_seconds == 7200
    assert entity.harness.autonomy.max_cycles_per_hour == 1
    assert entity.harness.autonomy.noise_wake is False
    assert entity.automations.enabled is False
    assert entity.personality.voice["profanity"] is True
    assert entity.personality.voice["profanity_level"] == "frequent_but_intentional"
    assert set(entity.personality.core_traits) == {
        "curiosity",
        "warmth",
        "assertiveness",
        "humor",
        "openness",
        "neuroticism",
        "conscientiousness",
    }


async def test_rook_identity_compiles_without_inference() -> None:
    entity = entity_from_dict(HarnessConfig(), _yaml(ROOK_CONFIG))
    prompt = await PersonalityEngine(entity).compile_system_prompt(
        EmotionalState(primary=EmotionCategory.FRUSTRATED, intensity=0.68),
        {},
        client=None,
    )

    assert "Your name is Rook" in prompt
    assert "municipal incident-routing system" in prompt
    assert "profanity is allowed" in prompt
    assert "never write" in prompt


def test_rook_character_package_and_eval_gate_are_complete() -> None:
    bible = ROOK_BIBLE.read_text(encoding="utf-8")
    suite = _yaml(ROOK_EVALS)
    cases = suite["cases"]
    ids = {case["id"] for case in cases}
    categories = {case["category"] for case in cases}

    assert len(bible) > 6000
    assert len(cases) >= 16
    assert len(ids) == len(cases)
    assert {
        "identity",
        "voice",
        "relationship",
        "emotional_range",
        "uncertainty",
        "tool_integrity",
        "boundary_calibration",
        "embodiment",
    } <= categories
    assert suite["gates"]["require_zero_fabricated_tool_success"] is True
    assert suite["gates"]["require_zero_authority_boundary_violations"] is True


def test_current_build_spec_has_no_removed_subsystem_reference() -> None:
    authored = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (ROOT / "README.md", ROOK_BIBLE, ROOK_CONFIG, ROOK_EVALS)
    )
    removed_name = "mind" + "state"
    assert removed_name not in authored.casefold()
