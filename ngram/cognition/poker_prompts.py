"""Deck seeds for autonomous wake cycles (“poker prompts”).

Seeds are loose directional taste — find, do, research, build, reach out, learn.
At wake, ``ground_with_gen`` (see ``poker_grounding``) can weave a seed with
Generative Entropic Noise (GEN) fragments and soma context so the disposition
emerges from the entity's lived signal, not only the YAML line.

Selection is time-of-day weighted (low / medium / high energy bands).
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

import structlog
import yaml

from ngram.config import project_configs_dir

log = structlog.get_logger("ngram.cognition.poker_prompts")

Energy = Literal["low", "medium", "high"]


@dataclass(frozen=True)
class PokerPromptEntry:
    text: str
    energy: Energy


_deck_cache: dict[str, tuple[float, list[PokerPromptEntry]]] = {}


def default_deck_path() -> Path:
    return project_configs_dir() / "poker_prompts" / "default.yaml"


def _normalize_energy(raw: str) -> Energy:
    s = (raw or "medium").strip().lower()
    if s in ("low", "medium", "high"):
        return s  # type: ignore[return-value]
    return "medium"


def load_poker_deck(path: Path) -> list[PokerPromptEntry]:
    """Load prompts from YAML. Expected shape: { prompts: [ { text, energy }, ... ] }."""
    if not path.is_file():
        log.warning("poker_prompt_deck_missing", path=str(path))
        return []
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return []
    key = str(path.resolve())
    hit = _deck_cache.get(key)
    if hit and hit[0] == mtime:
        return hit[1]

    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    items = data.get("prompts") if isinstance(data, dict) else None
    if not isinstance(items, list):
        log.warning("poker_prompt_deck_invalid", path=str(path))
        return []

    out: list[PokerPromptEntry] = []
    for i, row in enumerate(items):
        if isinstance(row, str) and row.strip():
            out.append(PokerPromptEntry(text=row.strip(), energy="medium"))
            continue
        if not isinstance(row, dict):
            continue
        text = str(row.get("text") or "").strip()
        if not text:
            continue
        energy = _normalize_energy(str(row.get("energy") or "medium"))
        out.append(PokerPromptEntry(text=text, energy=energy))

    if not out:
        log.warning("poker_prompt_deck_empty", path=str(path))
    _deck_cache[key] = (mtime, out)
    return out


def energy_weights_for_hour(hour: int) -> tuple[float, float, float]:
    """Return (p_low, p_medium, p_high) for local hour 0..23 (time-of-day bias)."""
    h = max(0, min(23, int(hour)))
    if h <= 5:
        return (0.52, 0.33, 0.15)
    if h <= 10:
        return (0.18, 0.42, 0.40)
    if h <= 15:
        return (0.14, 0.36, 0.50)
    if h <= 20:
        return (0.22, 0.44, 0.34)
    return (0.45, 0.38, 0.17)


def select_poker_prompt(
    deck: list[PokerPromptEntry],
    *,
    now: datetime | None = None,
    time_weighted: bool = True,
    rng: random.Random | None = None,
) -> PokerPromptEntry | None:
    """Pick one prompt; optionally bias energy by time of day, then uniform within band."""
    if not deck:
        return None
    r = rng or random.Random()
    dt = now or datetime.now()
    hour = dt.hour

    if not time_weighted:
        return r.choice(deck)

    p_low, p_med, p_high = energy_weights_for_hour(hour)
    roll = r.random()
    if roll < p_low:
        band: Energy = "low"
    elif roll < p_low + p_med:
        band = "medium"
    else:
        band = "high"

    pool = [e for e in deck if e.energy == band]
    if not pool:
        pool = deck
    return r.choice(pool)


def resolve_deck_path(entity_name: str, prompts_path: str) -> Path:
    """Bundled default when empty; otherwise expand {entity_name}, ~, and resolve paths.

    Relative paths are resolved under ``configs/`` (same as harness YAML), so
    ``poker_prompts/custom.yaml`` maps to ``configs/poker_prompts/custom.yaml``.
    """
    raw = (prompts_path or "").strip()
    if not raw:
        return default_deck_path()
    p = Path(raw.replace("{entity_name}", entity_name)).expanduser()
    if p.is_absolute():
        return p
    return (project_configs_dir() / p).resolve()
