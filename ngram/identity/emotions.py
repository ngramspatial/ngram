"""Emotional state FSM with probabilistic transitions + imprint nudges from recall."""

from __future__ import annotations

import random
import time
from typing import Any

from ngram.config import EntityConfig
from ngram.models import EmotionCategory, EmotionalState, ImprintRecord


def _baseline_from_traits(entity: EntityConfig) -> EmotionCategory:
    t = entity.personality.core_traits
    if t.get("neuroticism", 0.3) > 0.65:
        return EmotionCategory.ANXIOUS
    if t.get("warmth", 0.5) > 0.65:
        return EmotionCategory.CONTENT
    if t.get("curiosity", 0.5) > 0.7:
        return EmotionCategory.CURIOUS
    return EmotionCategory.NEUTRAL


class EmotionEngine:
    def __init__(self, entity: EntityConfig) -> None:
        self.entity = entity
        self._state = EmotionalState(
            primary=EmotionCategory.NEUTRAL,
            intensity=0.4,
            stability=0.85 - 0.35 * entity.personality.core_traits.get("neuroticism", 0.3),
        )
        self._baseline = _baseline_from_traits(entity)

    def get_state(self) -> EmotionalState:
        return self._state

    def reset_to_initial(self) -> None:
        """Fresh mood from current YAML traits (e.g. after a full memory wipe)."""
        self._baseline = _baseline_from_traits(self.entity)
        self._state = EmotionalState(
            primary=EmotionCategory.NEUTRAL,
            intensity=0.4,
            stability=0.85 - 0.35 * self.entity.personality.core_traits.get("neuroticism", 0.3),
        )

    def apply_recall_imprints(
        self,
        imprint_episode_times: list[tuple[ImprintRecord, float]],
        now: float,
    ) -> None:
        """Shift mood from remembered emotional residue; strength decays with episode age."""
        half_life = self.entity.harness.memory.imprint_decay_half_life_seconds
        for im, ep_ts in imprint_episode_times:
            age = max(0.0, now - ep_ts)
            decay = 0.5 ** (age / half_life) if half_life > 0 else 1.0
            w = min(0.55, float(im.intensity) * decay * 0.22)
            cat = self._category_from_imprint_emotion(im.emotion)
            if cat is None:
                continue
            if w < 0.04:
                continue
            if random.random() < 0.15 + w:
                self._state.primary = cat
            self._state.intensity = min(1.0, self._state.intensity + w * 0.45)
            self._state.last_transition = time.time()

    def _category_from_imprint_emotion(self, emo: str) -> EmotionCategory | None:
        key = emo.lower().strip()
        try:
            return EmotionCategory(key)
        except ValueError:
            aliases = {
                "sad": EmotionCategory.MELANCHOLY,
                "happy": EmotionCategory.CONTENT,
                "joy": EmotionCategory.EXCITED,
                "worried": EmotionCategory.ANXIOUS,
            }
            return aliases.get(key)

    async def process_stimulus(
        self,
        stimulus_type: str,
        intensity: float,
        context: dict[str, Any],
    ) -> EmotionalState:
        n = self.entity.personality.core_traits.get("neuroticism", 0.3)
        warmth_rel = float(context.get("relationship_warmth", 0.0))
        momentum = min(1.0, (time.time() - self._state.last_transition) / 3600.0)
        resist = self._state.stability * (0.5 + 0.5 * momentum)

        delta = intensity * (0.4 + 0.6 * n)
        if random.random() > resist:
            if stimulus_type == "positive" and delta > 0.2:
                self._shift(EmotionCategory.CONTENT, min(1.0, 0.4 + delta + warmth_rel * 0.2))
            elif stimulus_type == "negative" and delta > 0.25:
                self._shift(
                    EmotionCategory.CONCERNED if n < 0.5 else EmotionCategory.ANXIOUS,
                    min(1.0, 0.45 + delta),
                )
            elif stimulus_type == "playful":
                self._shift(EmotionCategory.AMUSED, min(1.0, 0.5 + delta * 0.5))
            elif stimulus_type == "deep":
                self._shift(EmotionCategory.CURIOUS, min(1.0, 0.55 + delta * 0.3))
        return self._state

    def _shift(self, primary: EmotionCategory, intensity: float) -> None:
        self._state.primary = primary
        self._state.intensity = max(0.15, min(1.0, intensity))
        self._state.last_transition = time.time()

    async def tick(self) -> EmotionalState:
        rate = self.entity.harness.identity.emotion_decay_rate
        self._state.decay_toward_baseline(self._baseline, rate)
        return self._state
