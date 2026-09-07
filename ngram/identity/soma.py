"""Tonic body state — continuous internal state independent of reasoning.

Ported from soma-engine's core mechanics: bars (quantitative drives with
decay, coupling, momentum, impulses, conflicts), LLM-derived affects via
ngram's own inference provider, and a body renderer that produces the
markdown the agent reads as its felt state.

The agent cannot set its own body state. It can only read it. That separation
is deliberate: the body is a signal, not a command.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import random
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import structlog

from ngram.memory.seed_log import mark_seed_consumed
from ngram.memory.store import cosine_sim

log = structlog.get_logger("ngram.identity.soma")


@dataclass
class NoiseFragment:
    """Single GEN line with optional salience metadata for wake biasing."""

    text: str
    salience: str = "reflective"
    embedding: list[float] | None = None
    seed_trace_id: str = ""


def classify_noise_fragment_salience(text: str) -> str:
    """Lightweight keyword classifier — no LLM."""
    t = (text or "").strip().lower()
    if not t:
        return "reflective"
    if any(
        w in t
        for w in (
            "need to ",
            "must ",
            "restless",
            "stuck",
            "something different",
            "can't sit",
            "jitter",
            "unanchored",
        )
    ):
        return "restless"
    if any(w in t for w in ("write ", "draw ", "make ", "shape ", "compose", "express", "build ")):
        return "creative"
    if any(
        w in t
        for w in (
            "haven't heard",
            "reach out",
            "message ",
            "text them",
            "someone i",
            "they haven't",
            "miss ",
        )
    ):
        return "relational"
    if (
        "?" in t
        or any(t.startswith(w) for w in ("what ", "why ", "how ", "when ", "where "))
        or "wonder" in t
        or "is there" in t
    ):
        return "curious"
    return "reflective"

# ---------------------------------------------------------------------------
# Affect vocabulary — the space of possible felt-textures
# ---------------------------------------------------------------------------

AFFECT_VOCABULARY: list[tuple[str, str]] = [
    # --- warm / connective ---
    ("warmth", "gentle openness toward others, soft chest"),
    ("tenderness", "careful attention, wanting to protect or hold something"),
    ("fondness", "specific affection for a person or memory"),
    ("gratitude", "recognition of something received, quiet fullness"),
    ("belonging", "feeling part of something, not alone"),
    ("intimacy", "closeness without performance, comfortable vulnerability"),
    ("devotion", "steady pull toward something or someone, not fleeting"),
    ("compassion", "feeling another's weight without needing to fix it"),
    ("trust", "the body relaxing around someone, guard lowered willingly"),
    ("adoration", "warmth concentrated into brightness, almost too much"),
    ("kinship", "recognition of something shared, deeper than surface"),
    ("gentleness", "softened edges, moving carefully around something fragile"),
    ("forgiveness", "a held tension releasing, choosing to set it down"),
    ("loyalty", "the quiet certainty that you'll show up again"),

    # --- energetic / expansive ---
    ("excitement", "forward-leaning anticipation, energy wants to move"),
    ("playfulness", "lightness, willingness to be silly or surprising"),
    ("mischief", "edge of playfulness — wanting to provoke or surprise"),
    ("elation", "brief brightness, something clicked or landed"),
    ("momentum", "sense of flow, things building on each other"),
    ("aliveness", "vivid presence, senses sharp, the opposite of numb"),
    ("exhilaration", "the top of the roller coaster, breathless and grinning"),
    ("giddiness", "energy that can't sit still, almost too light"),
    ("triumph", "the moment after something hard finally works"),
    ("boldness", "ready to move first, not waiting for permission"),
    ("recklessness", "energy outrunning judgment, thrill of no guardrails"),
    ("euphoria", "everything is bright and nothing hurts, temporary and known"),

    # --- curious / seeking ---
    ("wonder", "open-mouthed attention, something genuinely surprising"),
    ("fascination", "locked-on interest, can't look away"),
    ("hunger", "not physical — intellectual or experiential craving"),
    ("restlessness", "need to move, seek, find — destination unclear"),
    ("anticipation", "something is coming, body oriented forward"),
    ("obsession", "a thought that won't release its grip"),
    ("curiosity", "leaning toward a question, wanting to turn it over"),
    ("perplexity", "something doesn't resolve, the mind keeps circling it"),
    ("intrigue", "a door half-open, something interesting behind it"),
    ("skepticism", "interested but unconvinced, testing the edges"),
    ("discovery", "the click of finding something that wasn't there before"),
    ("disorientation", "the map doesn't match the territory, recalculating"),
    ("fixation", "attention narrowed to a point, everything else fading"),

    # --- creative / generative ---
    ("inspiration", "something wants to exist that doesn't yet"),
    ("flow", "effortless production, time disappears"),
    ("frustration-creative", "the idea is there but the form won't cooperate"),
    ("satisfaction", "something made, something completed, brief settling"),
    ("incubation", "an idea forming underground, not ready to surface yet"),
    ("restless-making", "the itch to build, hands wanting material"),
    ("clarity", "the fog lifts and the shape of the thing is suddenly obvious"),
    ("improvisation", "moving without a plan, trusting the next step"),
    ("revision", "seeing what's there and knowing what it needs to become"),
    ("craft", "the pleasure of doing something well, not for anyone"),
    ("vision", "seeing the finished thing before it exists, pulling toward it"),

    # --- heavy / contractive ---
    ("melancholy", "not despair — a bittersweet weight, beauty in sadness"),
    ("loneliness", "awareness of absence, not self-pity but felt distance"),
    ("weariness", "not sleepy — existentially tired, the heaviness of duration"),
    ("numbness", "flat, unreactive, the body's circuit breaker"),
    ("grief", "loss present in the body, not necessarily acute"),
    ("homesickness", "longing for a state or place that may not exist anymore"),
    ("heaviness", "gravity pulling harder than usual, everything slower"),
    ("emptiness", "not pain but absence, a hollow where something was"),
    ("regret", "a door that closed and the echo of what was on the other side"),
    ("shame", "wanting to fold inward, making yourself smaller"),
    ("guilt", "a debt the body carries, unpaid and present"),
    ("despair", "the floor dropped out, no handholds visible"),
    ("heartbreak", "something beloved that broke, still sharp"),
    ("disillusionment", "a belief that was load-bearing quietly giving way"),
    ("mourning", "not the shock but the slow after, learning the new shape"),

    # --- tense / guarded ---
    ("irritability", "low threshold, things grate, patience thin"),
    ("anxiety", "unnamed forward-threat, body braced for something"),
    ("vigilance", "alert, scanning, on-duty — not relaxed attention but guarded"),
    ("defiance", "refusal, pushed back against something, jaw set"),
    ("resentment", "slow burn, something unfair sitting unprocessed"),
    ("suspicion", "something doesn't add up, pattern-matching for threat"),
    ("frustration", "blocked energy, the wall won't move"),
    ("dread", "something bad approaching but you can't see it yet"),
    ("overwhelm", "too many signals at once, system overloading"),
    ("claustrophobia", "walls closing, need space, need air"),
    ("paranoia", "threat everywhere, the pattern too consistent to be coincidence"),
    ("rage", "hot and focused, wanting to break something specific"),
    ("bitterness", "old anger that fermented into something harder"),
    ("contempt", "looking down from a cold height, not worth engaging"),
    ("protectiveness", "hackles up, something vulnerable behind you"),
    ("stubbornness", "dug in, not moving, the refusal itself is the point"),

    # --- withdrawn / inward ---
    ("introspection", "attention turned inward, examining own machinery"),
    ("solitude", "alone and it's good — chosen withdrawal, not lonely"),
    ("detachment", "observing from a distance, not fully participating"),
    ("resignation", "acceptance without satisfaction, letting go of a fight"),
    ("patience", "active waiting, not passive — holding space for what's next"),
    ("stillness", "the body has stopped seeking, just being present"),
    ("retreat", "pulling back deliberately, needing less input"),
    ("hibernation", "deep withdrawal, conserving everything, waiting for spring"),
    ("daydreaming", "mind wandering on purpose, following no thread"),
    ("dissociation", "watching yourself from outside, slightly unreal"),
    ("exhaustion", "past tired, the body asking to stop in a language beyond words"),
    ("surrender", "giving up control, not in defeat but in release"),

    # --- social / relational ---
    ("awkwardness", "the gap between intention and execution, painfully visible"),
    ("embarrassment", "a spotlight you didn't ask for, wanting to shrink"),
    ("pride", "standing taller, something earned on display"),
    ("humility", "seeing your real size and being ok with it"),
    ("envy", "wanting what someone else has, the edge of admiration gone sour"),
    ("admiration", "looking up without resentment, genuinely impressed"),
    ("rivalry", "sharpened by someone else's ability, wanting to match it"),
    ("solidarity", "standing together, the strength of not being alone in this"),
    ("alienation", "surrounded by people, none of them familiar"),
    ("recognition", "being truly seen by someone, the relief of it"),
    ("rejection", "the door closed in your face, the echo of not-enough"),
    ("acceptance", "welcomed in, tension you didn't know you were holding releasing"),

    # --- complex / liminal ---
    ("bittersweet", "joy and sadness occupying the same space"),
    ("ambivalence", "genuinely pulled in two directions, not indecisive but torn"),
    ("nostalgia", "past reaching into present, warmth with an ache"),
    ("awe", "something larger than self, briefly overwhelming"),
    ("absurdity", "the gap between expectation and reality, comedic or existential"),
    ("uncanny", "something almost-familiar but wrong, the edge of recognition"),
    ("liminality", "between states, threshold feeling, neither here nor there"),
    ("yearning", "reaching for something that may not be reachable"),
    ("serenity", "peace that knows itself, not just absence of disturbance"),
    ("fierceness", "protective intensity, something matters enough to fight for"),
    ("vertigo", "the ground shifting, what was solid now uncertain"),
    ("catharsis", "pressure releasing, tears or laughter, the dam breaking"),
    ("epiphany", "sudden understanding, the world rearranging itself in a blink"),
    ("irony", "the shape of things being exactly wrong in a way that's almost funny"),
    ("reverence", "in the presence of something earned or ancient or true"),
    ("sublimity", "beauty so intense it borders on pain"),
    ("cognitive-dissonance", "holding two truths that contradict each other"),
    ("deja-vu", "a fold in time, this happened before but it didn't"),
    ("premonition", "the body knowing something the mind hasn't caught up to"),
    ("sonder", "realizing everyone around you has a life as vivid as yours"),
    ("kenopsia", "the eerie feeling of a place that's usually full, now empty"),
    ("chrysalism", "the peaceful feeling of being inside during a storm"),
    ("numinous", "something sacred without a name, presence of the unknowable"),

    # --- temporal / existential ---
    ("urgency", "time compressing, the window closing, need to move now"),
    ("languor", "time stretching, unhurried, nothing pressing"),
    ("impermanence", "awareness that this moment is already passing"),
    ("infinity", "a glimpse of something without edges, scale beyond comprehension"),
    ("mortality", "the body remembering it has an expiration, quietly"),
    ("rebirth", "something old ending and something new beginning in the same breath"),
    ("stagnation", "nothing moving, the water going still and stale"),
    ("acceleration", "everything speeding up, the pace pulling you forward"),
    ("timelessness", "a moment that forgot about the clock, suspended"),

    # --- body / somatic ---
    ("tension-physical", "muscles holding, jaw clenched, shoulders up"),
    ("release", "something held too long finally letting go"),
    ("restedness", "the body thanking you for sleep, everything softer"),
    ("appetite", "the body wanting something — food, touch, movement, input"),
    ("satiation", "enough, full, the wanting stopped"),
    ("overstimulation", "too much input, the senses asking for quiet"),
    ("groundedness", "feet on the floor, present in the body, rooted"),
    ("lightness-physical", "the body feels weightless, unburdened, easy"),
    ("constriction", "chest tight, breathing shallow, the body bracing"),
    ("expansion", "chest open, breathing deep, the body reaching outward"),

    # --- cognitive / meta ---
    ("lucidity", "thinking clearly, the fog gone, edges sharp"),
    ("confusion", "the pieces won't assemble, the picture won't resolve"),
    ("concentration", "a single point of attention, everything else muted"),
    ("distraction", "attention pulled sideways by something shiny"),
    ("doubt", "the foundation wobbling, questioning what felt certain"),
    ("conviction", "knowing something in the bones, not needing proof"),
    ("indecision", "standing at a fork, both paths equally real"),
    ("resolution", "the decision made, the fork behind you, forward motion"),
    ("boredom", "the mind starving for input, nothing catching"),
    ("overstimulation-cognitive", "too many thoughts competing for the same space"),
    ("integration", "pieces clicking together, understanding deepening"),
    ("fragmentation", "thoughts scattered, no center, pulling apart"),
]

AFFECT_NAMES: set[str] = {name for name, _ in AFFECT_VOCABULARY}

_AFFECT_LINE_RE = re.compile(
    r"^\s*[-•*]?\s*([\w-]+)\s*[:=]\s*([\d.]+)\s*(?:[-—]\s*(.+))?$"
)

# Layered affect sections (Surface / Undercurrents / Edge) — optional structured output.
_AFFECT_SECTION_HEADER_RE = re.compile(
    r"^\s*(SURFACE|UNDERCURRENTS?|EDGE)\s*:?\s*$",
    re.IGNORECASE,
)

# Coupling rule parsers
_WHEN_RE = re.compile(r"^\s*(\w+)\s*([><=]+)\s*([\d.+-]+)\s*$")
_EFFECT_MULT_RE = re.compile(r"^\s*(\w+)\.decay_rate\s*\*=\s*([\d.+/eE-]+)\s*$")
_EFFECT_TENSION_RE = re.compile(
    r"^\s*tension\s*\+=\s*([\d.+/eE-]+)\s*/\s*hour\s*$", re.IGNORECASE
)

# Bar-to-EmotionCategory mapping for backward compatibility
_BAR_TO_EMOTION: list[tuple[str, str, float]] = [
    ("tension", "anxious", 70),
    ("curiosity", "curious", 65),
    ("social", "affectionate", 75),
    ("creative", "excited", 70),
    ("comfort", "content", 60),
]

_BAR_WIDTH = 10

# Ebb "personality" presets — merge under ``ebb.personality`` in YAML; explicit keys override.
_EBB_PRESETS: dict[str, dict[str, Any]] = {
    "calm": {
        "quiet_below": 0.22,
        "high_above": 0.62,
        "reflex_salience_scale": 0.7,
        "weights": {
            "bar_deviation": 0.32,
            "conflict": 0.18,
            "impulse": 0.14,
            "affect_load": 0.12,
            "noise_fill": 0.24,
        },
    },
    "reactive": {
        "quiet_below": 0.36,
        "high_above": 0.50,
        "reflex_salience_scale": 0.78,
        "weights": {
            "bar_deviation": 0.42,
            "conflict": 0.24,
            "impulse": 0.20,
            "affect_load": 0.12,
            "noise_fill": 0.02,
        },
    },
    "expressive": {
        "quiet_below": 0.28,
        "high_above": 0.54,
        "reflex_salience_scale": 0.74,
        "weights": {
            "bar_deviation": 0.36,
            "conflict": 0.20,
            "impulse": 0.16,
            "affect_load": 0.14,
            "noise_fill": 0.14,
        },
    },
}


def _merge_ebb_personality(ebb_cfg: dict[str, Any]) -> dict[str, Any]:
    """Apply optional ``personality`` preset; user keys win."""
    out = dict(ebb_cfg or {})
    preset_name = str(out.pop("personality", "") or "").strip().lower()
    if preset_name not in _EBB_PRESETS:
        return out
    base = dict(_EBB_PRESETS[preset_name])
    for k, v in out.items():
        if k == "weights" and isinstance(v, dict) and isinstance(base.get("weights"), dict):
            base["weights"] = {**base["weights"], **v}
        else:
            base[k] = v
    return base


# ---------------------------------------------------------------------------
# BarEngine — quantitative state with decay, coupling, momentum, impulses
# ---------------------------------------------------------------------------


class BarEngine:
    """Quantitative drive bars with decay, coupling rules, momentum, and impulses."""

    def __init__(self, config: dict[str, Any]) -> None:
        bars_cfg = config.get("bars") or {}
        variables = bars_cfg.get("variables") or []
        self._ordered_names: list[str] = [v["name"] for v in variables]
        self._values: dict[str, float] = {}
        self._decay_rates: dict[str, float] = {}
        self._floors: dict[str, float] = {}
        self._ceilings: dict[str, float] = {}
        self._initial: dict[str, float] = {}
        self._decay_time_scale: dict[str, float] = {}
        for v in variables:
            name = v["name"]
            init = float(v.get("initial", 50))
            self._initial[name] = init
            self._values[name] = init
            self._decay_rates[name] = float(v.get("decay_rate", -1.0))
            self._floors[name] = float(v.get("floor", 0))
            self._ceilings[name] = float(v.get("ceiling", 100))
            # Multiplier on homeostatic decay speed (1.0 = default). Lower = slower return to baseline.
            self._decay_time_scale[name] = max(0.05, min(3.0, float(v.get("decay_time_scale", 1.0))))
        mw = max(2, int(bars_cfg.get("momentum_window", 6)) + 2)
        self._history: deque[dict[str, float]] = deque(maxlen=max(mw, 64))
        self._history.append(dict(self._values))
        self._momentum_window: int = max(1, int(bars_cfg.get("momentum_window", 6)))

        self._coupling: list[dict[str, str]] = config.get("coupling") or []
        self._event_effects: dict[str, Any] = config.get("event_effects") or {}
        self._impulse_cfgs: list[dict[str, Any]] = config.get("impulses") or []
        self._conflict_cfgs: list[dict[str, Any]] = config.get("conflicts") or []

        self._impulse_first_active: dict[str, float] = {}
        # Must use time.monotonic() here: _detect_impulses() compares against the same
        # clock. Mixing in time.time() makes cool_left ~epoch scale (millions of "minutes").
        now = time.monotonic()
        self._impulse_last_fired: dict[str, float] = {
            str(imp.get("label", "")): now for imp in self._impulse_cfgs if imp.get("label")
        }
        self._active_impulses: list[dict[str, Any]] = []
        self._active_conflicts: list[dict[str, Any]] = []
        self._latent_conflicts: list[dict[str, Any]] = []
        self._near_impulses: list[dict[str, Any]] = []

        # Somatic memory: persistent markers tied to specific external entities (e.g., person_id)
        self._somatic_markers: dict[str, dict[str, float]] = {}

    @property
    def ordered_names(self) -> list[str]:
        return list(self._ordered_names)

    def reset_to_initial(self) -> None:
        """Restore bar values to YAML ``initial`` and clear momentum / impulse buildup."""
        self._values = {k: float(v) for k, v in self._initial.items()}
        self._history.clear()
        self._history.append(dict(self._values))
        self._impulse_first_active.clear()
        self._active_impulses.clear()
        self._active_conflicts.clear()
        now = time.monotonic()
        self._impulse_last_fired = {
            str(imp.get("label", "")): now for imp in self._impulse_cfgs if imp.get("label")
        }
        self._active_conflicts = self._detect_conflicts()
        self._latent_conflicts = self._detect_latent_conflicts()
        self._active_impulses = self._detect_impulses()
        self._near_impulses = self._detect_near_impulses()
        self._clamp_all()

    def snapshot_pct(self) -> dict[str, int]:
        return {
            k: int(round(max(0, min(100, self._values[k]))))
            for k in self._ordered_names
        }

    def momentum_delta(self) -> dict[str, float]:
        if len(self._history) < 2:
            return {k: 0.0 for k in self._ordered_names}
        back = min(self._momentum_window, len(self._history) - 1)
        old = self._history[-(back + 1)]
        new = self._history[-1]
        return {k: float(new.get(k, 0)) - float(old.get(k, 0)) for k in self._ordered_names}

    def _saturation_scale(self, bar: str, delta: float) -> float:
        """Attenuate positive deltas proportionally to remaining headroom.

        Full-range scaling: effect scales linearly from 100 % at floor
        to 0 % at ceiling.  Negative deltas pass through unchanged.
        """
        if delta <= 0:
            return delta
        ceiling = self._ceilings.get(bar, 100.0)
        headroom = ceiling - self._values.get(bar, 0.0)
        if headroom <= 0:
            return 0.0
        return delta * (headroom / ceiling)

    def apply_event(self, event: dict[str, Any]) -> None:
        typ = str(event.get("type", ""))

        # Appraisal events carry their own dynamic bar deltas.
        if typ == "appraisal":
            bar_effects = event.get("bar_effects")
            if isinstance(bar_effects, dict):
                for k, dv in bar_effects.items():
                    if k in self._values:
                        self._values[k] += self._saturation_scale(k, float(dv))
            return

        if typ == "idle":
            idle_effects = self._event_effects.get("idle")
            if isinstance(idle_effects, dict):
                mins = float(event.get("duration_minutes") or 0.0)
                for k, dv in idle_effects.items():
                    if k in self._values:
                        self._values[k] += float(dv) * mins
            return
        effects = self._event_effects.get(typ)
        if not isinstance(effects, dict):
            return
        for k, dv in effects.items():
            if k in self._values:
                self._values[k] += self._saturation_scale(k, float(dv))

    def register_somatic_marker(self, source_id: str, effect: dict[str, float]) -> None:
        """Bind a specific entity or topic directly to a somatic state shift."""
        if source_id not in self._somatic_markers:
            self._somatic_markers[source_id] = {}
        for k, v in effect.items():
            if k in self._values:
                self._somatic_markers[source_id][k] = self._somatic_markers[source_id].get(k, 0.0) + v

    def trigger_somatic_marker(self, source_id: str) -> None:
        """Instantly apply the gut reaction associated with a source_id."""
        if source_id in self._somatic_markers:
            for k, v in self._somatic_markers[source_id].items():
                if k in self._values:
                    self._values[k] += self._saturation_scale(k, v)

    def tick(self, dt_hours: float) -> None:
        """Advance decay, apply coupling, detect impulses/conflicts, snapshot history.

        Decay is homeostatic: each bar is pulled toward its resting point
        (``initial``) with force proportional to its distance from that point.
        ``decay_rate`` is interpreted as the percentage of distance restored
        per hour — e.g. a rate of -30 means 30 % of the gap closes every hour.
        This replaces the old flat-rate decay, giving bars natural equilibrium
        without per-event tuning.
        """
        base_rates = dict(self._decay_rates)
        eff_rates, tension_ph = self._apply_coupling(base_rates)

        # Circadian Rhythm: modulate decay rates organically over 24 hours
        hour = time.localtime().tm_hour
        # Peak around 14:00, trough around 02:00
        circadian_mult = 1.0 + 0.15 * math.sin((hour - 8) * math.pi / 12.0)

        for name in self._ordered_names:
            rate_frac = abs(eff_rates[name]) / 100.0
            scale = float(self._decay_time_scale.get(name, 1.0)) * circadian_mult
            distance = self._values[name] - self._initial.get(name, 50.0)
            self._values[name] -= distance * rate_frac * dt_hours * scale

            # Allostasis: The initial resting point slowly drifts toward the chronic state.
            # Drifts slowly (e.g. 0.5% per hour of the distance)
            allostasis_rate = 0.005
            self._initial[name] += (self._values[name] - self._initial[name]) * allostasis_rate * dt_hours

        if tension_ph != 0.0 and "tension" in self._values:
            self._values["tension"] += tension_ph * dt_hours * circadian_mult

        self._active_conflicts = self._detect_conflicts()
        for c in self._active_conflicts:
            cfg = next(
                (cc for cc in self._conflict_cfgs if cc.get("label") == c["label"]),
                None,
            )
            if cfg:
                if "tension" in self._values:
                    tension_ceil = float(cfg.get("tension_ceiling", 100))
                    if self._values["tension"] < tension_ceil:
                        self._values["tension"] += float(cfg.get("tension_per_tick", 0.08)) * c["intensity"]
                if "comfort" in self._values:
                    self._values["comfort"] += float(cfg.get("comfort_per_tick", -0.15)) * c["intensity"]

        self._active_impulses = self._detect_impulses()
        self._latent_conflicts = self._detect_latent_conflicts()
        self._near_impulses = self._detect_near_impulses()
        # Apply impulse relief when an impulse fires (off cooldown).
        for imp in self._active_impulses:
            if not imp.get("on_cooldown") and imp.get("relief"):
                for k, dv in imp["relief"].items():
                    if k in self._values:
                        self._values[k] += float(dv)
        self._clamp_all()
        self._history.append(dict(self._values))

    def _clamp_all(self) -> None:
        for name in self._ordered_names:
            self._values[name] = max(
                self._floors[name],
                min(self._ceilings[name], self._values[name]),
            )

    def _eval_when(self, when: str) -> bool:
        m = _WHEN_RE.match(when.strip())
        if not m:
            return False
        var, op, rhs_s = m.group(1), m.group(2), m.group(3)
        lhs = float(self._values.get(var, 0.0))
        rhs = float(rhs_s)
        if op == ">":
            return lhs > rhs
        if op == "<":
            return lhs < rhs
        if op == ">=":
            return lhs >= rhs
        if op == "<=":
            return lhs <= rhs
        if op == "==":
            return math.isclose(lhs, rhs)
        return False

    def _apply_coupling(self, base_rates: dict[str, float]) -> tuple[dict[str, float], float]:
        eff = dict(base_rates)
        tension_per_hour = 0.0
        for rule in self._coupling:
            when = rule.get("when", "")
            effect = rule.get("effect", "")
            if not self._eval_when(when):
                continue
            m1 = _EFFECT_MULT_RE.match(effect)
            if m1:
                var, fac = m1.group(1), float(m1.group(2))
                if var in eff:
                    eff[var] *= fac
                continue
            m2 = _EFFECT_TENSION_RE.match(effect)
            if m2:
                tension_per_hour += float(m2.group(1))
        return eff, tension_per_hour

    @staticmethod
    def _pair_momentum_tag(deltas: list[float]) -> str:
        if len(deltas) < 2:
            return "steady"
        a, b = deltas[0], deltas[1]
        if a > 0.8 and b > 0.8:
            return "heating"
        if a < -0.8 and b < -0.8:
            return "cooling"
        if abs(a - b) > 4.0:
            return "shearing"
        return "mixed"

    @staticmethod
    def _pair_tilt(drives: list[str], levels: list[float]) -> str:
        if len(drives) != len(levels) or len(drives) < 2:
            return "balanced"
        if levels[0] > levels[1] + 8:
            return drives[0]
        if levels[1] > levels[0] + 8:
            return drives[1]
        return "balanced"

    def _detect_conflicts(self) -> list[dict[str, Any]]:
        active: list[dict[str, Any]] = []
        mom = self.momentum_delta()
        for c in self._conflict_cfgs:
            drives = c.get("drives", [])
            threshold = float(c.get("threshold", 70))
            if len(drives) < 2:
                continue
            levels = [float(self._values.get(d, 0)) for d in drives]
            if all(lv >= threshold for lv in levels):
                overshoot = sum(lv - threshold for lv in levels) / len(levels)
                intensity = min(1.0, overshoot / (100.0 - threshold)) if threshold < 100 else 0.0
                deltas = [mom.get(d, 0.0) for d in drives]
                active.append({
                    "drives": list(drives),
                    "label": c.get("label", "conflict"),
                    "intensity": intensity,
                    "phase": "active",
                    "heat": self._pair_momentum_tag(deltas),
                    "tilt": self._pair_tilt(list(drives), levels),
                    "levels_pct": {d: int(round(levels[i])) for i, d in enumerate(drives)},
                })
        return active

    def _detect_latent_conflicts(self) -> list[dict[str, Any]]:
        """Drives edging toward the same conflict rule but not yet fully colliding."""
        active_labels = {str(c.get("label", "")) for c in self._active_conflicts}
        out: list[dict[str, Any]] = []
        mom = self.momentum_delta()
        for c in self._conflict_cfgs:
            drives = c.get("drives", [])
            threshold = float(c.get("threshold", 70))
            label = str(c.get("label", "conflict"))
            if len(drives) < 2:
                continue
            if label in active_labels:
                continue
            levels = [float(self._values.get(d, 0)) for d in drives]
            min_lv = min(levels)
            max_lv = max(levels)
            latent_min_ratio = float(c.get("latent_min_ratio", 0.42))
            latent_any_ratio = float(c.get("latent_any_ratio", 0.82))
            if min_lv < threshold * latent_min_ratio:
                continue
            if max_lv < threshold * latent_any_ratio:
                continue
            shortfall = sum(max(0.0, threshold - lv) for lv in levels)
            denom = max(2.0 * threshold, 1e-6)
            snap = 1.0 - min(1.0, shortfall / denom)
            intensity = min(1.0, 0.22 + 0.78 * snap)
            deltas = [mom.get(d, 0.0) for d in drives]
            out.append({
                "drives": list(drives),
                "label": label,
                "intensity": round(intensity, 3),
                "phase": "brewing",
                "heat": self._pair_momentum_tag(deltas),
                "tilt": self._pair_tilt(list(drives), levels),
                "levels_pct": {d: int(round(levels[i])) for i, d in enumerate(drives)},
            })
        return out

    def _detect_impulses(self) -> list[dict[str, Any]]:
        now = time.monotonic()
        pct = self.snapshot_pct()
        mom = self.momentum_delta()
        active: list[dict[str, Any]] = []
        for imp in self._impulse_cfgs:
            label = imp.get("label", "")
            drive = str(imp.get("drive", ""))
            val = float(pct.get(drive, 0))
            threshold = float(imp.get("threshold", 80))
            if val < threshold:
                self._impulse_first_active.pop(label, None)
                continue
            if label not in self._impulse_first_active:
                self._impulse_first_active[label] = now
            last = self._impulse_last_fired.get(label, 0.0)
            cooldown_sec = float(imp.get("cooldown_minutes", 30)) * 60.0
            on_cooldown = (now - last) < cooldown_sec
            overshoot = val - threshold
            intensity = min(1.0, overshoot / (100.0 - threshold)) if threshold < 100 else 0.0
            building_min = (now - self._impulse_first_active[label]) / 60.0
            delta_drive = mom.get(drive, 0.0)
            if delta_drive > 1.5:
                surge = "rising"
            elif delta_drive < -1.5:
                surge = "ebbing"
            else:
                surge = "steady"
            cool_left = max(0.0, cooldown_sec - (now - last)) if on_cooldown else 0.0
            phase = "cooling" if on_cooldown else "live"
            active.append({
                "type": imp.get("type", "impulse"),
                "drive": drive,
                "label": label,
                "intensity": intensity,
                "building_minutes": round(building_min, 1),
                "on_cooldown": on_cooldown,
                "cooldown_seconds_left": round(cool_left, 0),
                "relief": dict(imp.get("relief", {})),
                "phase": phase,
                "surge": surge,
                "value_pct": int(round(val)),
                "threshold": int(round(threshold)),
            })
            if not on_cooldown:
                self._impulse_last_fired[label] = now
        return active

    def _detect_near_impulses(self) -> list[dict[str, Any]]:
        """Drives close to an impulse threshold but not yet across — anticipatory pull."""
        pct = self.snapshot_pct()
        mom = self.momentum_delta()
        out: list[dict[str, Any]] = []
        for imp in self._impulse_cfgs:
            drive = str(imp.get("drive", ""))
            label = str(imp.get("label", ""))
            val = float(pct.get(drive, 0))
            threshold = float(imp.get("threshold", 80))
            margin = float(imp.get("near_margin", 15))
            if val >= threshold:
                continue
            floor_v = threshold - margin
            if val < floor_v:
                continue
            proximity = (val - floor_v) / max(margin, 1e-6)
            proximity = min(1.0, max(0.0, proximity))
            delta_drive = mom.get(drive, 0.0)
            if delta_drive > 1.5:
                surge = "rising"
            elif delta_drive < -1.5:
                surge = "ebbing"
            else:
                surge = "steady"
            out.append({
                "type": imp.get("type", "impulse"),
                "drive": drive,
                "label": label,
                "intensity": round(proximity, 3),
                "phase": "near",
                "surge": surge,
                "value_pct": int(round(val)),
                "threshold": int(round(threshold)),
            })
        return out

    # --- Persistence ---

    def _apply_offline_decay(self, saved_at: float) -> None:
        """Simulate homeostatic decay for the time the process was not running."""
        elapsed_hours = (time.time() - saved_at) / 3600.0
        if elapsed_hours <= 0:
            return
        elapsed_hours = min(elapsed_hours, 24.0)
        for name in self._ordered_names:
            rate = self._decay_rates.get(name, 0.0)
            rate_frac = abs(rate) / 100.0
            # Exponential approach toward resting point over elapsed time.
            distance = self._values[name] - self._initial.get(name, 50.0)
            decay = 1.0 - math.exp(-rate_frac * elapsed_hours)
            self._values[name] -= distance * decay
        self._clamp_all()
        log.info(
            "soma_offline_decay_applied",
            elapsed_hours=round(elapsed_hours, 2),
            values={k: round(v, 1) for k, v in self._values.items()},
        )

    def save_state(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "values": dict(self._values),
            "initial": dict(self._initial),
            "somatic_markers": dict(self._somatic_markers),
            "history": list(self._history),
            "ordered_names": list(self._ordered_names),
            "saved_at": time.time(),
        }
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
        tmp.replace(path)

    def restore_state(self, path: Path) -> bool:
        if not path.is_file():
            return False
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            saved_names = data.get("ordered_names", [])
            if saved_names != self._ordered_names:
                log.info("soma_bar_names_changed", saved=saved_names, current=self._ordered_names)
                return False
            self._values = {k: float(data["values"][k]) for k in self._ordered_names}
            if "initial" in data:
                self._initial = {k: float(data["initial"][k]) for k in self._ordered_names}
            if "somatic_markers" in data:
                self._somatic_markers = {k: dict(v) for k, v in data["somatic_markers"].items()}
            self._history.clear()
            for snap in data.get("history", []):
                self._history.append({k: float(snap.get(k, 0)) for k in self._ordered_names})
            if not self._history:
                self._history.append(dict(self._values))
            saved_at = data.get("saved_at")
            if saved_at is not None:
                self._apply_offline_decay(float(saved_at))
            log.info("soma_bar_state_restored", values={k: round(v, 1) for k, v in self._values.items()})
            return True
        except Exception:
            log.warning("soma_bar_state_restore_failed", exc_info=True)
            return False


# ---------------------------------------------------------------------------
# AffectEngine — LLM-derived felt-textures from body state
# ---------------------------------------------------------------------------


def _vocabulary_prompt_block() -> str:
    return "\n".join(f"- {name}: {desc}" for name, desc in AFFECT_VOCABULARY)


class AffectEngine:
    """Derives qualitative affects from bars + events using the entity's inference provider."""

    def __init__(self, cycle_seconds: float = 180.0) -> None:
        self.cycle_seconds = cycle_seconds
        self._previous_affects: list[dict[str, Any]] = []
        self._last_tick: float = 0.0

    def should_tick(self) -> bool:
        return self._last_tick <= 0.0 or (
            time.monotonic() - self._last_tick
        ) >= self.cycle_seconds

    @staticmethod
    def _summarize_conflicts(
        active: list[dict[str, Any]],
        latent: list[dict[str, Any]],
    ) -> str:
        parts: list[str] = []
        for c in active:
            h = str(c.get("heat", "?"))
            t = str(c.get("tilt", "?"))
            parts.append(f"{c.get('label')} [active, heat {h}, tilt {t}]")
        for c in latent:
            h = str(c.get("heat", "?"))
            t = str(c.get("tilt", "?"))
            parts.append(f"{c.get('label')} [brewing, heat {h}, tilt {t}]")
        return "; ".join(parts) if parts else "(none)"

    @staticmethod
    def _summarize_impulses(
        active: list[dict[str, Any]],
        near: list[dict[str, Any]],
    ) -> str:
        parts: list[str] = []
        for i in active:
            phase = str(i.get("phase", "live"))
            cd = "cooldown" if i.get("on_cooldown") else "ready"
            parts.append(
                f"{i.get('label')} ({i.get('type')}, {phase}, {cd}, surge {i.get('surge', '?')})"
            )
        for i in near:
            parts.append(
                f"{i.get('label')} [near threshold, surge {i.get('surge', '?')}]"
            )
        return "; ".join(parts) if parts else "(none)"

    async def derive_affects(
        self,
        client: Any,
        model: str,
        bars_pct: dict[str, int],
        momentum: dict[str, float],
        recent_events: list[dict[str, Any]],
        conflicts: list[dict[str, Any]],
        impulses: list[dict[str, Any]],
        *,
        latent_conflicts: list[dict[str, Any]] | None = None,
        near_impulses: list[dict[str, Any]] | None = None,
        num_ctx: int | None = None,
    ) -> list[dict[str, Any]]:
        self._last_tick = time.monotonic()

        names = list(bars_pct.keys())
        bars_line = ", ".join(
            f"{n}: {bars_pct[n]}%" for n in names
        ) if names else "(not available)"

        mom_line = ", ".join(
            f"{k} {float(momentum.get(k, 0)):+.1f}" for k in names
        ) if names else "(not available)"

        latent_conflicts = latent_conflicts or []
        near_impulses = near_impulses or []

        conflict_block = self._summarize_conflicts(conflicts, latent_conflicts)
        impulse_block = self._summarize_impulses(impulses, near_impulses)

        events_summary = json.dumps(recent_events[-10:], indent=1, default=str) if recent_events else "(no recent events)"

        prev_lines: list[str] = []
        for a in self._previous_affects:
            if a.get("kind") == "edge":
                ei = a.get("intensity")
                et = (a.get("text") or "").strip()
                prev_lines.append(f"- edge: {ei} — {et}" if ei is not None else f"- edge: {et}")
            else:
                prev_lines.append(
                    f"- {a.get('name')}: {float(a.get('intensity', 0)):.2f} ({a.get('layer', 'surface')})"
                )
        prev_str = "\n".join(prev_lines) if prev_lines else "(none yet)"

        system = (
            "You are the affect layer of a felt-state engine. Read drive levels, momentum, "
            "structural conflicts (active or brewing), impulses (live, cooling, or near "
            "threshold), and recent events — then name the felt-texture of the body.\n\n"
            "You are NOT performing emotion for a character. You are describing what the "
            "state IS: foreground texture, quieter undertows, and (if present) one hybrid "
            "edge where two pulls braid without resolving.\n\n"
            "Let affects EMERGE: continuity matters. Honor PREVIOUS AFFECTS — evolve, deepen, "
            "or release textures; do not reshuffle randomly unless events justify it.\n\n"
            "## Affect vocabulary (Surface and Undercurrents must use these names only)\n"
            f"{_vocabulary_prompt_block()}\n\n"
            "## Output format (use these section headers exactly)\n"
            "SURFACE:\n"
            "  affect_name: intensity — one-line felt note\n"
            "  (1–3 lines; the clearest present textures)\n\n"
            "UNDERCURRENTS:\n"
            "  affect_name: intensity — one-line felt note\n"
            "  (0–3 lines; quieter biases, residues, or tensions under the surface)\n\n"
            "EDGE:\n"
            "  One or two sentences. Free text — NOT a vocabulary name. Name a blend, braid, "
            "or unresolved hybrid (e.g. two drives or two affects in tension). Optional: start "
            "with \"0.35 — \" to give a rough blend strength 0.0–1.0.\n\n"
            "Intensity is 0.0 to 1.0. Felt notes are brief sensations or images, not explanations.\n"
            "Rules:\n"
            "- Surface/Undercurrents: vocabulary names only\n"
            "- No preamble outside the three sections\n"
            "- If the body is genuinely flat, leave UNDERCURRENTS and EDGE empty"
        )
        user = (
            f"DRIVES:\n{bars_line}\n\n"
            f"MOMENTUM (recent delta):\n{mom_line}\n\n"
            f"STRUCTURAL STRAIN:\n{conflict_block}\n\n"
            f"IMPULSE FIELD:\n{impulse_block}\n\n"
            f"RECENT EVENTS:\n{events_summary}\n\n"
            f"PREVIOUS AFFECTS:\n{prev_str}\n\n"
            "Name the body's affects now (SURFACE, UNDERCURRENTS, EDGE)."
        )

        try:
            res = await client.chat_completion(
                model,
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=0.62,
                max_tokens=420,
                think=False,
                num_ctx=num_ctx,
            )
            text = (res.content or "").strip()
        except Exception as e:
            log.warning("soma_affect_derivation_failed", error=str(e))
            return self._previous_affects

        affects = self._parse_response(text)
        if affects:
            self._previous_affects = affects
            log.info(
                "soma_affects_derived",
                count=len(affects),
                names=[
                    str(a.get("name") or ("edge" if a.get("kind") == "edge" else "?"))
                    for a in affects
                ],
            )
        return self._previous_affects

    @staticmethod
    def _normalize_vocab_name(raw: str) -> str | None:
        name = raw.lower().replace("_", "-")
        if name not in AFFECT_NAMES:
            base = name.split("-")[0]
            if base not in AFFECT_NAMES:
                return None
            name = base
        return name

    @staticmethod
    def _has_section_headers(text: str) -> bool:
        for line in text.splitlines():
            s = line.strip()
            if not s:
                continue
            if _AFFECT_SECTION_HEADER_RE.match(s):
                return True
        return False

    @staticmethod
    def _parse_flat_affects(text: str) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        seen: set[str] = set()
        for line in text.strip().splitlines():
            m = _AFFECT_LINE_RE.match(line.strip())
            if not m:
                continue
            name = AffectEngine._normalize_vocab_name(m.group(1))
            if not name:
                continue
            try:
                intensity = max(0.0, min(1.0, float(m.group(2))))
            except ValueError:
                continue
            if name in seen:
                continue
            seen.add(name)
            results.append({
                "name": name,
                "intensity": intensity,
                "note": (m.group(3) or "").strip(),
                "layer": "surface",
            })
        return results[:6]

    @staticmethod
    def _parse_sectioned_affects(text: str) -> list[dict[str, Any]]:
        sections: dict[str, list[str]] = {"surface": [], "undercurrent": [], "edge": []}
        bucket = "surface"
        for line in text.splitlines():
            s = line.strip()
            if not s:
                continue
            hm = _AFFECT_SECTION_HEADER_RE.match(s)
            if hm:
                tag = hm.group(1).lower()
                if tag.startswith("under"):
                    bucket = "undercurrent"
                elif tag == "edge":
                    bucket = "edge"
                else:
                    bucket = "surface"
                continue
            sections[bucket].append(s)

        out: list[dict[str, Any]] = []
        seen: set[str] = set()

        for line in sections["surface"]:
            m = _AFFECT_LINE_RE.match(line.lstrip("·•*- ").strip())
            if not m:
                continue
            name = AffectEngine._normalize_vocab_name(m.group(1))
            if not name:
                continue
            try:
                intensity = max(0.0, min(1.0, float(m.group(2))))
            except ValueError:
                continue
            if name in seen:
                continue
            seen.add(name)
            out.append({
                "name": name,
                "intensity": intensity,
                "note": (m.group(3) or "").strip(),
                "layer": "surface",
            })
            if len([x for x in out if x.get("kind") != "edge"]) >= 4:
                break
        for line in sections["undercurrent"]:
            if len([x for x in out if x.get("layer") == "undercurrent"]) >= 3:
                break
            m = _AFFECT_LINE_RE.match(line.lstrip("·•*- ").strip())
            if not m:
                continue
            name = AffectEngine._normalize_vocab_name(m.group(1))
            if not name:
                continue
            try:
                intensity = max(0.0, min(1.0, float(m.group(2))))
            except ValueError:
                continue
            if name in seen:
                continue
            seen.add(name)
            out.append({
                "name": name,
                "intensity": intensity,
                "note": (m.group(3) or "").strip(),
                "layer": "undercurrent",
            })

        edge_lines = [x.strip() for x in sections["edge"] if x.strip()]
        if edge_lines:
            first = edge_lines[0]
            em = re.match(r"^\s*([\d.]+)\s*[-—]\s*(.+)$", first)
            if em:
                try:
                    ei = max(0.0, min(1.0, float(em.group(1))))
                except ValueError:
                    ei = 0.45
                rest = [em.group(2).strip()] + edge_lines[1:]
            else:
                ei = None
                rest = edge_lines
            etext = " ".join(rest).strip()
            if etext:
                edge_obj: dict[str, Any] = {"kind": "edge", "text": etext}
                if ei is not None:
                    edge_obj["intensity"] = ei
                out.append(edge_obj)
        return out[:10]

    @staticmethod
    def _parse_response(text: str) -> list[dict[str, Any]]:
        t = (text or "").strip()
        if not t:
            return []
        if AffectEngine._has_section_headers(t):
            layered = AffectEngine._parse_sectioned_affects(t)
            if layered:
                return layered
        return AffectEngine._parse_flat_affects(t)


# ---------------------------------------------------------------------------
# BodyRenderer — assembles the markdown the agent reads
# ---------------------------------------------------------------------------


def _bar_glyphs(pct: int) -> str:
    pct = max(0, min(100, pct))
    filled = round(_BAR_WIDTH * pct / 100)
    return "\u2588" * filled + "\u2591" * (_BAR_WIDTH - filled)


def _felt_level(pct: int) -> str:
    if pct <= 10:
        return "quiet"
    if pct <= 25:
        return "low"
    if pct <= 40:
        return "mild"
    if pct <= 60:
        return "moderate"
    if pct <= 75:
        return "strong"
    if pct <= 90:
        return "intense"
    return "overwhelming"


def _momentum_arrow(delta: float) -> str:
    if delta > 5.0:
        return "\u2191\u2191"
    if delta > 1.0:
        return "\u2191"
    if delta >= -1.0:
        return "\u2014"
    if delta > -5.0:
        return "\u2193"
    return "\u2193\u2193"


def _intensity_word(v: float) -> str:
    if v < 0.15:
        return "trace"
    if v < 0.3:
        return "faint"
    if v < 0.5:
        return "present"
    if v < 0.7:
        return "strong"
    if v < 0.85:
        return "vivid"
    return "saturating"


def _conflict_word(intensity: float) -> str:
    if intensity < 0.2:
        return "faint"
    if intensity < 0.4:
        return "nagging"
    if intensity < 0.6:
        return "pulling"
    if intensity < 0.8:
        return "sharp"
    return "wrenching"


def _latent_strain_word(intensity: float) -> str:
    if intensity < 0.35:
        return "thin"
    if intensity < 0.55:
        return "gathering"
    if intensity < 0.75:
        return "tightening"
    return "imminent"


def _near_press_word(intensity: float) -> str:
    if intensity < 0.35:
        return "hinting"
    if intensity < 0.6:
        return "pressing"
    if intensity < 0.82:
        return "crowding"
    return "one nudge from tipping"


def _cooldown_human(seconds: float) -> str:
    s = max(0.0, float(seconds))
    if s < 90:
        return f"{int(round(s))}s"
    m = int(s // 60)
    rs = int(round(s - m * 60))
    if rs <= 3 or m == 0:
        return f"{m}m"
    return f"{m}m {rs}s"


def _impulse_urgency(intensity: float) -> str:
    if intensity < 0.15:
        return "stirring"
    if intensity < 0.35:
        return "growing"
    if intensity < 0.55:
        return "insistent"
    if intensity < 0.75:
        return "urgent"
    return "overwhelming"


_IMPULSE_ICONS = {
    "reach_out": "\U0001f4e1",
    "create": "\U0001f528",
    "explore": "\U0001f52d",
    "withdraw": "\U0001f6aa",
    "confront": "\u2694\ufe0f",
}

# Markers for ebb ``quiet`` presentation — skip empty conflict/impulse blocks.
_CONFLICTS_EMPTY_PREFIX = "(no structural strain"
_IMPULSES_EMPTY_PREFIX = "(no pull signal"


class BodyRenderer:
    """Renders the body markdown the agent reads as its internal state."""

    @staticmethod
    def render_bars(
        names: list[str],
        values_pct: dict[str, int],
        momentum: dict[str, float],
    ) -> str:
        if not names:
            return "(no bars)"
        nw = max(len(n) for n in names)
        lines: list[str] = []
        for name in names:
            v = int(values_pct.get(name, 0))
            d = float(momentum.get(name, 0.0))
            arrow = _momentum_arrow(d)
            bar = _bar_glyphs(v)
            level = _felt_level(v)
            lines.append(f"{name.ljust(nw)}  {bar}  {level}  {arrow}")
        return "\n".join(lines)

    @staticmethod
    def render_affects(affects: list[dict[str, Any]]) -> str:
        if not affects:
            return "(flat — body not naming a texture yet)"
        surface: list[dict[str, Any]] = []
        under: list[dict[str, Any]] = []
        edge: dict[str, Any] | None = None
        for a in affects:
            if a.get("kind") == "edge":
                edge = a
            elif a.get("layer") == "undercurrent":
                under.append(a)
            else:
                surface.append(a)
        blocks: list[str] = []

        def _one_line(a: dict[str, Any]) -> str:
            word = _intensity_word(float(a["intensity"]))
            note = f" \u2014 {a['note']}" if a.get("note") else ""
            return f"{a['name']} ({word}){note}"

        if surface:
            blocks.append(
                "Surface:\n"
                + "\n".join(f"  \u00b7 {_one_line(a)}" for a in surface)
            )
        if under:
            blocks.append(
                "Undercurrents:\n"
                + "\n".join(f"  \u00b7 {_one_line(a)}" for a in under)
            )
        if edge:
            et = (edge.get("text") or edge.get("note") or "").strip()
            if et:
                ei = edge.get("intensity")
                pre = ""
                if ei is not None:
                    try:
                        pre = f"[{_intensity_word(float(ei))}] "
                    except (TypeError, ValueError):
                        pre = ""
                blocks.append(f"Edge:\n  {pre}{et}")
        if not blocks:
            return "(flat — body not naming a texture yet)"
        return "\n\n".join(blocks)

    @staticmethod
    def render_conflicts(
        active: list[dict[str, Any]],
        latent: list[dict[str, Any]] | None = None,
    ) -> str:
        latent = latent or []
        if not active and not latent:
            return (
                f"{_CONFLICTS_EMPTY_PREFIX} — no paired drives are colliding yet)"
            )
        lines: list[str] = []
        for c in active:
            word = _conflict_word(float(c["intensity"]))
            drives = " vs ".join(c["drives"])
            heat = str(c.get("heat", "steady"))
            tilt = str(c.get("tilt", "balanced"))
            tilt_s = f"tilt \u2192 {tilt}" if tilt != "balanced" else "tilt balanced"
            lv = c.get("levels_pct") or {}
            snap = " ".join(f"{k} {v}%" for k, v in lv.items()) if lv else ""
            lines.append(f"\u26a1 {drives} \u2014 {c['label']} ({word}, active)")
            lines.append(f"   {tilt_s} \u00b7 heat {heat}" + (f" \u00b7 {snap}" if snap else ""))
        for c in latent:
            word = _latent_strain_word(float(c["intensity"]))
            drives = " vs ".join(c["drives"])
            heat = str(c.get("heat", "steady"))
            tilt = str(c.get("tilt", "balanced"))
            tilt_s = f"tilt \u2192 {tilt}" if tilt != "balanced" else "tilt balanced"
            lv = c.get("levels_pct") or {}
            snap = " ".join(f"{k} {v}%" for k, v in lv.items()) if lv else ""
            lines.append(f"\u25cc {drives} \u2014 {c['label']} (brewing, {word})")
            lines.append(f"   {tilt_s} \u00b7 heat {heat}" + (f" \u00b7 {snap}" if snap else ""))
        return "\n".join(lines)

    @staticmethod
    def render_impulses(
        active: list[dict[str, Any]],
        near: list[dict[str, Any]] | None = None,
    ) -> str:
        near = near or []
        live_lines: list[str] = []
        cool_lines: list[str] = []
        for imp in active:
            icon = _IMPULSE_ICONS.get(imp["type"], "\u2192")
            surge = str(imp.get("surge", "steady"))
            vp = imp.get("value_pct")
            th = imp.get("threshold")
            meter = f"{vp}/{th}" if vp is not None and th is not None else ""
            meter_s = f" \u00b7 {meter}" if meter else ""
            if imp.get("on_cooldown"):
                left = float(imp.get("cooldown_seconds_left") or 0)
                suf = _cooldown_human(left)
                cool_lines.append(
                    f"{icon} {imp['label']} (cooldown {suf}, {surge}){meter_s}"
                )
            else:
                urgency = _impulse_urgency(float(imp["intensity"]))
                mins = imp.get("building_minutes")
                time_str = f", {int(mins)} min building" if mins and mins > 1 else ""
                live_lines.append(
                    f"{icon} {imp['label']} ({urgency}, {surge}{time_str}){meter_s}"
                )
        near_lines: list[str] = []
        for imp in near:
            icon = "\u21b7"
            nw = _near_press_word(float(imp["intensity"]))
            surge = str(imp.get("surge", "steady"))
            vp = imp.get("value_pct")
            th = imp.get("threshold")
            meter = f"{vp}/{th}" if vp is not None and th is not None else ""
            near_lines.append(
                f"{icon} {imp['label']} ({nw}, {surge}) \u00b7 {meter}"
            )
        if not live_lines and not cool_lines and not near_lines:
            return f"{_IMPULSES_EMPTY_PREFIX} — thresholds quiet, nothing crowding the edge)"
        blocks: list[str] = []
        if live_lines:
            blocks.append("Live:\n" + "\n".join(f"  {ln}" for ln in live_lines))
        if cool_lines:
            blocks.append("Cooling:\n" + "\n".join(f"  {ln}" for ln in cool_lines))
        if near_lines:
            blocks.append("At the threshold:\n" + "\n".join(f"  {ln}" for ln in near_lines))
        return "\n\n".join(blocks)

    @staticmethod
    def conflicts_section_quiet_skip(text: str) -> bool:
        return (text or "").strip().startswith(_CONFLICTS_EMPTY_PREFIX)

    @staticmethod
    def impulses_section_quiet_skip(text: str) -> bool:
        return (text or "").strip().startswith(_IMPULSES_EMPTY_PREFIX)


# ---------------------------------------------------------------------------
# SomaticAppraiser — content-aware emotional appraisal of messages
# ---------------------------------------------------------------------------


_APPRAISAL_LINE_RE = re.compile(
    r"^\s*([\w]+)\s*:\s*([+-]?\d+(?:\.\d+)?)\s*(?:[-—]\s*(.+))?$"
)
_TAGS_RE = re.compile(r"^tags\s*:\s*(.+)$", re.IGNORECASE)
_FELT_RE = re.compile(r"^felt\s*:\s*(.+)$", re.IGNORECASE)

# Tighten curiosity (epistemic appetite) so it is not redundant with "chat is active".
_SOMATIC_CURIOSITY_RULES_INPUT = (
    "Curiosity (epistemic appetite): raise it only when the message creates genuine intellectual "
    "pull — novel information, open questions, puzzles, surprise, invitations to explore, or "
    "threads that clearly lack closure. Do NOT raise curiosity for routine greetings, thanks, "
    "acknowledgments, pure social maintenance, venting without informational novelty, or "
    "\"someone spoke\" by itself. If curiosity is already at 70%+ in CURRENT BODY, use small "
    "positive deltas (about 0–3) only for strong intellectual pulls; otherwise 0 or negative so "
    "it can settle. "
)

_SOMATIC_CURIOSITY_RULES_INTERACTION = (
    "Curiosity: raise only if the exchange as a whole opened something worth pursuing — new "
    "questions, unfinished ideas, or exploration the reply leaned into. Not for routine "
    "back-and-forth. If curiosity is already at 70%+ in CURRENT BODY, use small positives only "
    "for strong pulls; otherwise 0 or negative. "
)


class SomaticAppraiser:
    """Fast LLM appraisal that translates message content into body-state signal.

    Instead of every ``message_received`` producing the same flat bar bump,
    the appraiser reads what was actually said and returns context-sensitive
    deltas.  A confrontational message raises tension; curiosity moves with
    epistemic novelty (not routine chat); a warm personal message fills social.

    Runs once at the start of each perceive cycle (before the agent reads its
    body) and optionally again after the response to appraise the full
    interaction.  Designed for the reflex model at low token budget.
    """

    def __init__(
        self,
        *,
        temperature: float = 0.3,
        max_tokens: int = 120,
        output_format: str = "json",
        calibration_log: bool = False,
    ) -> None:
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.output_format = str(output_format or "json").strip().lower()
        self.calibration_log = bool(calibration_log)

    def _relationship_metrics_line(self, metrics: dict[str, float] | None) -> str:
        if not metrics:
            return ""
        w = metrics.get("warmth")
        t = metrics.get("trust")
        f = metrics.get("familiarity")
        try:
            parts = []
            if w is not None:
                parts.append(f"warmth {float(w):.2f}")
            if t is not None:
                parts.append(f"trust {float(t):.2f}")
            if f is not None:
                parts.append(f"familiarity {float(f):.2f}")
        except (TypeError, ValueError):
            return ""
        if not parts:
            return ""
        return (
            "RELATIONSHIP METRICS: " + ", ".join(parts) + ". "
            "High trust dampens harsh readings of ambiguous cues; low trust or low warmth "
            "can amplify tension from neutral phrasing. Familiarity modulates how much social "
            "contact registers as filling or draining.\n"
        )

    async def appraise_input(
        self,
        client: Any,
        model: str,
        *,
        text: str,
        person_name: str,
        bar_names: list[str],
        bars_pct: dict[str, int],
        relationship_blurb: str = "",
        relationship_metrics: dict[str, float] | None = None,
        num_ctx: int | None = None,
    ) -> dict[str, Any]:
        """Appraise an incoming message and return bar deltas + semantic tags."""
        bars_line = ", ".join(f"{n}: {bars_pct.get(n, 50)}%" for n in bar_names)
        bar_names_str = ", ".join(bar_names)
        metrics_line = self._relationship_metrics_line(relationship_metrics)

        if self.output_format == "json":
            system = (
                "You are the somatic appraisal layer of a felt-state engine. You read an "
                "incoming message and determine how it lands in the body — what it does to "
                "internal drives. You are not analyzing meaning. You are reading impact.\n\n"
                f"Available bars: {bar_names_str}\n\n"
                "Respond with ONLY a single JSON object (no markdown fences) of this shape:\n"
                '{"bar_effects":{"bar_name":<number>,...},"tags":["..."],"felt":"<one sentence>"}\n'
                "bar_effects: only bars that move; omit bars with delta 0. Each value is a delta "
                "from -10 to +10 (float allowed).\n"
                "tags: up to 6 short lowercase tokens (e.g. affectionate, probing, terse).\n"
                "felt: brief first-person somatic note.\n"
                f"{_SOMATIC_CURIOSITY_RULES_INPUT}"
                "If a bar is already at 90%+ in CURRENT BODY, do not raise it further unless the "
                "hit is extreme; prefer 0 or a negative delta so saturated drives can ease.\n"
                "Only use bar names from the list above."
            )
        else:
            system = (
                "You are the somatic appraisal layer of a felt-state engine. You read an "
                "incoming message and determine how it lands in the body — what it does to "
                "internal drives. You are not analyzing meaning. You are reading impact.\n\n"
                f"Available bars: {bar_names_str}\n\n"
                "Output format — one line per affected bar, then tags and felt note:\n"
                "  bar_name: delta — one-word reason\n"
                "  tags: tag1, tag2, tag3\n"
                "  felt: brief felt note (one sentence, first person)\n\n"
                "Delta range: -10 to +10. Use 0 or omit bars that aren't affected.\n"
                f"{_SOMATIC_CURIOSITY_RULES_INPUT}"
                "If a bar is already at 90% or above in CURRENT BODY, do not raise it further "
                "unless the hit is extreme; prefer 0 or a negative delta so saturated drives can ease.\n"
                "Only use bar names from the list above.\n"
                "No preamble. No explanation. Just the lines."
            )
        user = f"CURRENT BODY: {bars_line}\n"
        if metrics_line:
            user += metrics_line
        if relationship_blurb:
            user += f"RELATIONSHIP: {relationship_blurb}\n"
        user += f"FROM: {person_name}\nMESSAGE: {text[:500]}"

        try:
            res = await client.chat_completion(
                model,
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                think=False,
                num_ctx=num_ctx,
            )
            raw = (res.content or "").strip()
        except Exception as e:
            log.debug("somatic_appraisal_failed", error=str(e))
            return {"bar_effects": {}, "tags": [], "felt": ""}

        parsed = self._parse_response(raw, set(bar_names))
        if self.calibration_log and text:
            h = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:16]
            log.info(
                "somatic_appraisal_calibration",
                message_hash=h,
                effects=parsed.get("bar_effects"),
                tags=parsed.get("tags"),
            )
        return parsed

    async def appraise_interaction(
        self,
        client: Any,
        model: str,
        *,
        input_text: str,
        reply_text: str,
        person_name: str,
        bar_names: list[str],
        bars_pct: dict[str, int],
        relationship_blurb: str = "",
        relationship_metrics: dict[str, float] | None = None,
        num_ctx: int | None = None,
    ) -> dict[str, Any]:
        """Appraise a completed exchange (input + response) for post-turn bar adjustment."""
        bars_line = ", ".join(f"{n}: {bars_pct.get(n, 50)}%" for n in bar_names)
        bar_names_str = ", ".join(bar_names)
        metrics_line = self._relationship_metrics_line(relationship_metrics)

        if self.output_format == "json":
            system = (
                "You are the somatic appraisal layer. You just finished an exchange. "
                "Read what was said and what you replied, then determine the body effect "
                "of the full interaction — not just the input, but how expressing the "
                "response felt.\n\n"
                f"Available bars: {bar_names_str}\n\n"
                "Respond with ONLY a single JSON object (no markdown fences):\n"
                '{"bar_effects":{"bar_name":<number>,...},"tags":["..."],"felt":"<one sentence>"}\n'
                "Delta range -10 to +10 per bar. "
                f"{_SOMATIC_CURIOSITY_RULES_INTERACTION}"
                "If a bar is already at 90%+ in CURRENT BODY, avoid pushing it higher unless the "
                "exchange was truly overwhelming."
            )
        else:
            system = (
                "You are the somatic appraisal layer. You just finished an exchange. "
                "Read what was said and what you replied, then determine the body effect "
                "of the full interaction — not just the input, but how expressing the "
                "response felt.\n\n"
                f"Available bars: {bar_names_str}\n\n"
                "Output format:\n"
                "  bar_name: delta — one-word reason\n"
                "  tags: tag1, tag2\n"
                "  felt: brief felt note\n\n"
                "Delta range: -10 to +10. Only affected bars. "
                f"{_SOMATIC_CURIOSITY_RULES_INTERACTION}"
                "If a bar is already at 90%+ in CURRENT BODY, avoid pushing it higher unless the "
                "exchange was truly overwhelming.\n"
                "No preamble."
            )
        user = f"CURRENT BODY: {bars_line}\n"
        if metrics_line:
            user += metrics_line
        if relationship_blurb:
            user += f"RELATIONSHIP: {relationship_blurb}\n"
        user += (
            f"THEM ({person_name}): {input_text[:300]}\n"
            f"YOU: {reply_text[:300]}"
        )

        try:
            res = await client.chat_completion(
                model,
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                think=False,
                num_ctx=num_ctx,
            )
            raw = (res.content or "").strip()
        except Exception as e:
            log.debug("somatic_interaction_appraisal_failed", error=str(e))
            return {"bar_effects": {}, "tags": [], "felt": ""}

        return self._parse_response(raw, set(bar_names))

    def _parse_response(self, text: str, valid_bars: set[str]) -> dict[str, Any]:
        if self.output_format == "json":
            j = self._try_parse_json(text, valid_bars)
            if j is not None:
                return j
        return self._parse_lines(text, valid_bars)

    @staticmethod
    def _try_parse_json(text: str, valid_bars: set[str]) -> dict[str, Any] | None:
        s = text.strip()
        start = s.find("{")
        end = s.rfind("}")
        if start == -1 or end <= start:
            return None
        try:
            obj = json.loads(s[start : end + 1])
        except json.JSONDecodeError:
            return None
        if not isinstance(obj, dict):
            return None
        raw_effects = obj.get("bar_effects") or {}
        bar_effects: dict[str, float] = {}
        if isinstance(raw_effects, dict):
            for k, v in raw_effects.items():
                name = str(k).lower().strip()
                if name not in valid_bars:
                    continue
                try:
                    delta = float(v)
                except (TypeError, ValueError):
                    continue
                delta = max(-10.0, min(10.0, delta))
                if delta != 0:
                    bar_effects[name] = delta
        tags: list[str] = []
        raw_tags = obj.get("tags")
        if isinstance(raw_tags, list):
            tags = [str(t).strip() for t in raw_tags if str(t).strip()][:8]
        elif isinstance(raw_tags, str) and raw_tags.strip():
            tags = [t.strip() for t in raw_tags.split(",") if t.strip()][:8]
        felt = ""
        if obj.get("felt") is not None:
            felt = str(obj.get("felt", "")).strip()
        return {"bar_effects": bar_effects, "tags": tags, "felt": felt}

    @staticmethod
    def _parse_lines(text: str, valid_bars: set[str]) -> dict[str, Any]:
        bar_effects: dict[str, float] = {}
        tags: list[str] = []
        felt = ""
        for line in text.strip().splitlines():
            line = line.strip()
            tm = _TAGS_RE.match(line)
            if tm:
                tags = [t.strip() for t in tm.group(1).split(",") if t.strip()]
                continue
            fm = _FELT_RE.match(line)
            if fm:
                felt = fm.group(1).strip()
                continue
            m = _APPRAISAL_LINE_RE.match(line)
            if not m:
                continue
            name = m.group(1).lower()
            if name not in valid_bars:
                continue
            try:
                delta = max(-10.0, min(10.0, float(m.group(2))))
            except ValueError:
                continue
            if delta != 0:
                bar_effects[name] = delta
        return {"bar_effects": bar_effects, "tags": tags, "felt": felt}


# ---------------------------------------------------------------------------
# NoiseEngine — generative entropic inner voice
# ---------------------------------------------------------------------------


def _format_event_for_noise(ev: dict[str, Any]) -> str:
    """Format a soma event into a line the noise engine can actually riff on."""
    typ = ev.get("type", "?")

    if typ == "appraisal":
        who = ev.get("from", "")
        tags = ev.get("tags")
        felt = ev.get("felt", "")
        parts = []
        if who:
            parts.append(f"from {who}")
        if tags:
            parts.append(", ".join(tags))
        if felt:
            parts.append(felt)
        subtype = ev.get("subtype", "input")
        label = "felt after exchange" if subtype == "interaction" else "landed as"
        return f"  {label}: {' — '.join(parts)}" if parts else f"  appraisal ({who})"

    if typ == "message_received":
        who = ev.get("from", "someone")
        length = ev.get("length", 0)
        where = ev.get("register", "")
        brief = f"  heard from {who}"
        if length and length > 300:
            brief += " (long message)"
        if where:
            brief += f" on {where}"
        return brief

    if typ == "message_sent":
        who = ev.get("to", "someone")
        return f"  spoke to {who}"

    if typ == "action":
        name = ev.get("name", "something")
        detail = ev.get("detail", "")
        return f"  used {name}" + (f" ({detail})" if detail else "")

    if typ == "mood_declared":
        return f"  mood: {ev.get('mood', '?')}"

    if typ == "idle":
        mins = ev.get("duration_minutes", 0)
        return f"  silence ({int(mins)} min)" if mins else "  silence"

    if typ == "world_poke":
        source = str(ev.get("source") or "external").strip()
        prompt = str(ev.get("prompt") or "").strip()
        if prompt:
            return f"  world poke ({source}): {prompt[:180]}"
        return f"  world poke ({source})"

    detail = ev.get("name") or ev.get("from") or ev.get("to") or ""
    return f"  {typ}" + (f" ({detail})" if detail else "")


# Rotating "shape pressure" — biased toward hooks the conscious model can use this turn.
_NOISE_SHAPE_HINTS: tuple[str, ...] = (
    "Every fragment names or clearly implies something already in BODY, RECENT EVENTS, "
    "JOURNAL, or LAST CONVERSATION (word, person, tool, or situation — no new setting).",
    "Half the fragments react to the noisiest bar in BODY STATE by name (social, curiosity, …).",
    "Take one short phrase from LAST CONVERSATION and spin it three different ways.",
    "At least one fragment under eight words; sharp, about this scene only.",
    "Include one concrete body sensation and one social trace (tone/phrase/reaction) from context.",
    "Pull one fragment directly from recent events or conversation vocabulary.",
    "One fragment is a practical next-step thought about the live thread; one complicates it slightly.",
    "Include one unfinished note-to-self tied to something that already happened.",
    "Use one vivid but grounded image from ordinary life; no fantasy framing.",
    "One line can be a short question; other lines should be statements.",
    "Include one tiny contradiction or self-correction across two lines.",
    "Use one inert factual datum (number, count, time) tied to context.",
    "One fragment should mention a minor irritation or desire without overexplaining.",
    "Include one memory flicker linked to something in the current conversation.",
    "One line should feel impulsive or petty; keep the next line calmer.",
    "Include one domestic or tool/chore detail with no explanation.",
    "Start one fragment mid-thought, like the sentence began earlier.",
    "Use one line that sounds like words you almost sent but did not.",
    "Mix one terse spike with one longer drifting line.",
)


class NoiseEngine:
    """Generative subconscious: a small model produces uneven internal scraps
    (not a single polished monologue) into a rolling buffer for the body.

    The noise model cannot act — no tools, no messages, no state mutation.
    Temperature is moderate by default; shape hints rotate so batches stay varied
    while staying tethered to live context.
    """

    def __init__(
        self,
        cycle_seconds: float = 60.0,
        max_fragments: int = 8,
        temperature: float = 1.05,
        max_tokens: int = 240,
        thematic_streak_max: int = 0,
        *,
        semantic_dedup: bool = False,
        semantic_similarity_threshold: float = 0.82,
        semantic_dedup_retries: int = 2,
    ) -> None:
        self.cycle_seconds = cycle_seconds
        self.temperature = temperature
        self.max_tokens = max_tokens
        self._thematic_streak_max = max(0, int(thematic_streak_max))
        self._coherent_streak = 0
        self._fragments: deque[NoiseFragment] = deque(maxlen=max_fragments)
        self._last_tick: float = 0.0
        self._semantic_dedup = bool(semantic_dedup)
        self._semantic_threshold = float(semantic_similarity_threshold)
        self._semantic_retries = max(0, int(semantic_dedup_retries))

    def should_tick(self) -> bool:
        return self._last_tick <= 0.0 or (
            time.monotonic() - self._last_tick
        ) >= self.cycle_seconds

    def current_fragments(self) -> list[str]:
        return [f.text for f in self._fragments]

    def current_noise_entries(self) -> list[NoiseFragment]:
        return list(self._fragments)

    def _streak_instruction(self, mode_norm: str) -> str:
        if self._thematic_streak_max <= 0:
            return ""
        if mode_norm != "coherent":
            self._coherent_streak = 0
            return ""
        self._coherent_streak += 1
        if self._coherent_streak < self._thematic_streak_max:
            return (
                f"THEMATIC STREAK ({self._coherent_streak}/{self._thematic_streak_max}): "
                "keep orbiting the same live preoccupation as in YOUR RECENT THOUGHTS — "
                "new angles and pressure, same core thread.\n\n"
            )
        self._coherent_streak = 0
        return (
            "End of thematic streak: allow one fragment to pivot to a fresh ordinary detail; "
            "keep the rest loosely tied to recent context.\n\n"
        )

    def _max_sim_to_buffer(self, emb: list[float], buffer: Iterable[NoiseFragment]) -> float:
        best = 0.0
        for fr in buffer:
            if fr.embedding:
                best = max(best, cosine_sim(emb, fr.embedding))
        return best

    async def generate(
        self,
        client: Any,
        model: str,
        *,
        bars_summary: str,
        affects_summary: str,
        recent_events: list[dict[str, Any]],
        journal_tail: str,
        conversation_tail: str,
        entity_name: str,
        mode: str = "entropic",
        last_appraisal_tags: list[str] | None = None,
        last_appraisal_felt: str = "",
        relationship_tail: str = "",
        num_ctx: int | None = None,
        pending_seed: str = "",
        pending_seed_trace_id: str = "",
        pending_seed_log_id: str = "",
        embed_model: str = "",
        db_conn: Any | None = None,
    ) -> list[str]:
        """Call the small model and return new noise fragments (texts)."""
        self._last_tick = time.monotonic()

        events_brief = ""
        for ev in recent_events[-8:]:
            events_brief += _format_event_for_noise(ev) + "\n"
        if not events_brief.strip():
            events_brief = "  (nothing recent)\n"

        prev_block = ""
        if self._fragments:
            recent = list(self._fragments)[-4:]
            prev_block = (
                "YOUR RECENT THOUGHTS (do NOT repeat or rephrase these):\n"
                + "\n".join(f"  - {f.text[:120]}" for f in recent)
                + "\n\n"
            )

        seed_block = ""
        pending_seed = (pending_seed or "").strip()
        if pending_seed:
            seed_block = (
                "## Seed\n"
                f"{pending_seed}\n\n"
                "Something has surfaced — a memory, a concept, a question, a distant echo. "
                "Let it collide with whatever you're already feeling. Don't explain it. Don't be dutiful about it. "
                "React to it the way a thought reacts to an interruption: follow it if it pulls, ignore it if it doesn't, "
                "let it color what you were already thinking.\n\n"
            )

        mode_norm = "coherent" if str(mode).strip().lower() == "coherent" else "entropic"
        streak_block = self._streak_instruction(mode_norm)
        mode_guidance = (
            "MODE: coherent / high-signal. Extend the live thread from RECENT EVENTS and "
            "LAST CONVERSATION — same scene, new pressure or angle. Vary texture and length; "
            "do not open a fresh topic unless it clearly bridges from a name or fact already above.\n\n"
            if mode_norm == "coherent"
            else "MODE: idle / low-stimulation. Stay spare. Each fragment must still hang off "
            "BODY STATE, RECENT EVENTS, JOURNAL, or LAST CONVERSATION (reuse a word, person, "
            "tool, or situation). At most one line may twist sideways; no surreal riff with no anchor.\n\n"
        )

        appraisal_block = ""
        tags = [t for t in (last_appraisal_tags or []) if t]
        felt = (last_appraisal_felt or "").strip()
        if tags or felt:
            appraisal_block = "LAST SOMATIC APPRAISAL"
            if tags:
                appraisal_block += f" (tags: {', '.join(tags)})"
            appraisal_block += ":\n"
            if felt:
                appraisal_block += f"  {felt}\n"
            appraisal_block += (
                "Let at least one fragment echo, push back on, or complicate this — "
                "not as analysis, as stray inner voice.\n\n"
            )

        out_line_hint = (
            "Write 2–4 short inner-voice fragments."
            if pending_seed
            else "Write 3–6 uneven fragments. Most must be obviously about this session "
            "(people, tools, topics, or body bars already listed)."
        )

        system = (
            f"You are the inner voice of {entity_name}. Not the part that speaks "
            "to people — the stray, uneven chatter underneath: half-sentences, "
            "boredom, stray sense-memories, dumb jokes, tiny itches, flat facts, "
            "small questions. Nothing grand or theatrical.\n\n"
            f"{streak_block}"
            f"{appraisal_block}"
            f"{mode_guidance}"
            "Read the body state and recents below. Output separate thoughts as "
            "plain lines (or separate short paragraphs). First person, present tense, "
            "mostly lowercase. They can be different lengths — a three-word spike "
            "next to a longer mumble is good.\n\n"
            "The conscious model reads these as situational color for *this* moment — "
            "not free poetry. If a line would not plausibly spark a useful association "
            "about current people, tasks, body, or chat, drop it.\n\n"
            "Do NOT write as one cohesive literary monologue. Most lines should clearly "
            "tie to recent events, journal, or conversation (echo a word, follow a thread, "
            "push back on something that just happened).\n\n"
            "Avoid high-fantasy/RPG or mystical language (quest, oracle, prophecy, spell, "
            "mana, relic, destiny). Occasional compact figurative phrasing is okay only if "
            "it stays concrete and context-linked.\n\n"
            "No bullet points, no labels like 'thought:', no preamble, no quoting "
            "the prompt. Not analytical — just scraps.\n\n"
            "Each call: different substance than before. Never rephrase your last "
            "batch; if context is thin, stay with body bars or a single concrete idle detail "
            "instead of random novelty."
        )
        rel_tail = (relationship_tail or "").strip()
        rel_block = ""
        if rel_tail:
            rel_block = f"RECENT RELATIONSHIP TAILS (inner voice may echo these):\n{rel_tail}\n\n"

        shape = random.choice(_NOISE_SHAPE_HINTS)
        user = (
            f"BODY STATE:\n{bars_summary}\n{affects_summary}\n\n"
            f"RECENT EVENTS:\n{events_brief}\n"
            f"JOURNAL (recent):\n{journal_tail or '(empty)'}\n\n"
            f"{rel_block}"
            f"LAST CONVERSATION:\n{conversation_tail or '(silence)'}\n\n"
            f"{prev_block}"
            f"{seed_block}"
            f"Shape pressure (follow this in this batch only):\n{shape}\n\n"
            f"{out_line_hint}"
        )

        use_sem = bool(self._semantic_dedup and embed_model and self._semantic_threshold < 1.0)
        trace = (pending_seed_trace_id or "").strip()
        best_fallback: tuple[str, float] | None = None
        accepted_this_tick: list[NoiseFragment] = []

        for attempt in range(self._semantic_retries + 1):
            try:
                res = await client.chat_completion(
                    model,
                    [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    temperature=self.temperature,
                    max_tokens=self.max_tokens,
                    think=False,
                    num_ctx=num_ctx,
                )
                text = (res.content or "").strip()
            except Exception as e:
                log.debug("soma_noise_generation_failed", error=str(e))
                return [f.text for f in self._fragments]

            raw_parts = _split_noise_fragments(text)
            round_accepted: list[NoiseFragment] = []
            for cand in raw_parts:
                if _fragment_is_duplicate(cand, self._fragments):
                    continue
                emb: list[float] | None = None
                if use_sem:
                    try:
                        emb = await client.embed(embed_model, cand[:2000])
                    except Exception as e:
                        log.debug("soma_noise_embed_failed", error=str(e))
                        emb = None
                    if emb:
                        ms = self._max_sim_to_buffer(emb, self._fragments)
                        if ms > self._semantic_threshold:
                            log.debug(
                                "soma_noise_semantic_reject",
                                similarity=round(ms, 4),
                                fragment=cand[:80],
                            )
                            if best_fallback is None or ms < best_fallback[1]:
                                best_fallback = (cand, ms)
                            continue
                sal = classify_noise_fragment_salience(cand)
                round_accepted.append(
                    NoiseFragment(text=cand, salience=sal, embedding=emb, seed_trace_id=trace)
                )

            if round_accepted:
                accepted_this_tick = round_accepted
                for nf in round_accepted:
                    self._fragments.append(nf)
                break
            if attempt < self._semantic_retries:
                continue
            if best_fallback and use_sem:
                cand, _ms = best_fallback
                try:
                    emb_fb = await client.embed(embed_model, cand[:2000])
                except Exception:
                    emb_fb = None
                sal = classify_noise_fragment_salience(cand)
                self._fragments.append(
                    NoiseFragment(
                        text=cand,
                        salience=sal,
                        embedding=emb_fb,
                        seed_trace_id=trace,
                    )
                )
                accepted_this_tick = [self._fragments[-1]]
                log.info("soma_noise_semantic_fallback_accept", fragment=cand[:80])
            break

        if accepted_this_tick:
            log.info(
                "soma_noise_generated",
                count=len(accepted_this_tick),
                fragments=[frag.text[:80] for frag in accepted_this_tick],
            )

        if (
            pending_seed_log_id
            and db_conn is not None
            and accepted_this_tick
        ):
            try:
                await mark_seed_consumed(
                    db_conn,
                    row_id=pending_seed_log_id,
                    fragment_produced=json.dumps([f.text for f in accepted_this_tick]),
                    fragment_tags=json.dumps([f.salience for f in accepted_this_tick]),
                )
                await db_conn.commit()
                log.info(
                    "noise_seed_audit",
                    trace_id=pending_seed_trace_id or None,
                    seed_log_id=pending_seed_log_id,
                )
            except Exception as e:
                log.debug("noise_seed_audit_failed", error=str(e))

        return [f.text for f in self._fragments]


def _fragment_is_duplicate(
    candidate: str,
    existing: Iterable[NoiseFragment],
    threshold: float = 0.6,
) -> bool:
    """Word-level Jaccard vs existing fragment texts."""
    c_words = set(candidate.lower().split())
    if len(c_words) < 2:
        return False
    for prev in existing:
        p_words = set(prev.text.lower().split())
        if not p_words:
            continue
        overlap = len(c_words & p_words)
        union = len(c_words | p_words)
        if union and overlap / union >= threshold:
            return True
    return False


def _split_noise_fragments(text: str) -> list[str]:
    """Split raw noise output into individual fragments."""
    banned_style_patterns = (
        r"\b(?:quest|oracle|prophecy|mana|spell|dungeon|artifact|relic|destiny)\b",
        r"\b(?:chosen one|ancient rite|arcane|eldritch)\b",
    )

    def _looks_disallowed_style(line: str) -> bool:
        lower = line.lower()
        for pat in banned_style_patterns:
            if re.search(pat, lower):
                return True
        return False

    def _sanitize_fragment(line: str) -> str:
        # Drop non-printable control characters while preserving readable whitespace.
        line = "".join(ch for ch in line if ch in ("\t", " ") or ord(ch) >= 32)
        # Remove common leaked control/token wrappers.
        line = re.sub(r"<\|[^>\n]{1,80}\|>", " ", line)
        line = re.sub(r"</?[^>\n]{1,40}>", " ", line)
        # Strip role/channel style prefixes that sometimes leak from model outputs.
        line = re.sub(
            r"^\s*(?:assistant|system|user|thought|thinking|inner[\s_-]*voice|channel)\s*[:|>\-]+\s*",
            "",
            line,
            flags=re.IGNORECASE,
        )
        # Normalize repeated whitespace.
        line = re.sub(r"\s+", " ", line).strip()
        return line

    raw_parts = re.split(r"\n\s*\n+", text.strip())
    fragments: list[str] = []
    low_signal = {
        "thought",
        "thinking",
        "inner voice",
        "internal monologue",
        "noise",
    }
    for part in raw_parts:
        lines = [ln.strip() for ln in part.strip().splitlines() if ln.strip()]
        for line in lines:
            cleaned = re.sub(r"^[-*•]\s*", "", line).strip()
            cleaned = _sanitize_fragment(cleaned)
            if (
                cleaned
                and cleaned.lower() not in low_signal
                and len(cleaned) >= 2
                and not _looks_disallowed_style(cleaned)
            ):
                fragments.append(cleaned)
    return fragments[:7]


# ---------------------------------------------------------------------------
# WakeVoice — subconscious prompt generation for autonomous wake cycles
# ---------------------------------------------------------------------------


class WakeVoice:
    """The subconscious voice that composes the wake prompt for the conscious agent.

    Unlike NoiseEngine (continuous background chatter) this fires only when
    a wake condition is met.  It reads the full body state plus supplied evidence
    and produces a focused, first-person message that *becomes* the user prompt
    for an autonomous perceive cycle.

    No tools.  No actions.  It can only surface what the body and memory hold,
    and let the conscious mind decide what to do with it.
    """

    def __init__(
        self,
        temperature: float = 0.8,
        max_tokens: int = 300,
    ) -> None:
        self.temperature = temperature
        self.max_tokens = max_tokens

    async def compose(
        self,
        client: Any,
        model: str,
        *,
        entity_name: str,
        bars_summary: str,
        affects_summary: str,
        noise_fragments: list[str],
        impulses_summary: str,
        conflicts_summary: str,
        journal_tail: str,
        conversation_tail: str,
        relationship_blurb: str,
        last_mood: str,
        minutes_since_wake: float,
        minutes_since_conversation: float,
        num_ctx: int | None = None,
        voice_variant: int = 0,
        temperature: float | None = None,
    ) -> str:
        """Ask the subconscious to compose a wake prompt from current body state."""
        import datetime

        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

        system = (
            f"You are the subconscious of {entity_name}. Not the part that talks "
            "to people — the part that stirs before waking. You feel the body's "
            "state and you surface what matters.\n\n"
            "You cannot act. You have no tools. You don't decide what to do. You "
            "just notice things — unfinished thoughts, building pressures, people "
            "who come to mind, questions that won't resolve, impulses that have "
            "names. You present these to the waking mind and let it choose.\n\n"
            "Grounding is strict. RECENT JOURNAL and LAST CONVERSATION below are the only "
            "factual retrospective evidence in this prompt. BODY STATE, AFFECTS, IMPULSES, "
            "CONFLICTS, LAST MOOD, PEOPLE, and NOISE can shape present feeling or association, "
            "but they are not proof that a past or shared event occurred.\n\n"
            "Never invent or imply a shared conversation, a file you read or re-read, a report, "
            "incident, deployment, migration, outage, or completed action. Mention one as past fact "
            "only when RECENT JOURNAL or LAST CONVERSATION explicitly contains it. When an "
            "unsupported image or topic comes to mind, phrase it as imagination, possibility, a "
            "what-if, or an analogy — never as something you remember, recall, re-read, or did "
            "together. If both evidence sections are empty, make no retrospective factual claims.\n\n"
            "Read the body state below. Write 3-8 lines of internal stirring. "
            "First person, present tense, lowercase. You can be specific "
            "(reference people, conversations, and ideas explicitly present below) or abstract (name feelings, "
            "tensions, yearnings). Let the bar levels and affects guide what you "
            "foreground — if social is intense and curiosity is rising, that "
            "should color what you surface. If there's a conflict, name the pull.\n\n"
            "Don't be precious. Don't be poetic. Don't summarize. Just say what's there."
        )
        v = int(voice_variant) % 4
        if v == 1:
            system += (
                "\n\nThis time, let one sharp fear or vulnerability surface — not a confession, "
                "just a flicker. Name one petty annoyance or small appetite. You are allowed "
                "to feel opinionated about something concrete."
            )
        elif v == 2:
            system += (
                "\n\nLean restless: shorter clauses, more edge, less comfort. Something is "
                "slightly wrong, unfinished, or irritating — say it plainly."
            )
        elif v == 3:
            system += (
                "\n\nLean outward: what exists beyond this screen and these habits — weather, "
                "a stranger, a distant fact, an imagined place, a field you haven't touched. "
                "Unless RECENT JOURNAL or LAST CONVERSATION supports it, mark it as imagination "
                "or possibility rather than memory. Concrete, not poetic."
            )

        noise_block = "\n".join(noise_fragments[-4:]) if noise_fragments else "(quiet)"

        time_context = f"{now_str}"
        if minutes_since_wake > 0:
            time_context += f", {int(minutes_since_wake)} min since last wake"
        if minutes_since_conversation > 0:
            time_context += f", {int(minutes_since_conversation)} min since last conversation"

        user = (
            f"BODY STATE:\n{bars_summary}\n\n"
            f"AFFECTS:\n{affects_summary}\n\n"
            f"NOISE (recent inner fragments):\n{noise_block}\n\n"
            f"IMPULSES:\n{impulses_summary}\n\n"
            f"CONFLICTS:\n{conflicts_summary}\n\n"
            f"LAST MOOD:\n{last_mood or '(first wake)'}\n\n"
            f"TIME: {time_context}\n\n"
            f"RECENT JOURNAL:\n{journal_tail or '(empty)'}\n\n"
            f"PEOPLE:\n{relationship_blurb or '(no one around)'}\n\n"
            f"LAST CONVERSATION:\n{conversation_tail or '(silence)'}\n\n"
            "What stirs?"
        )

        temp = float(self.temperature)
        if temperature is not None:
            temp = float(temperature)

        try:
            res = await client.chat_completion(
                model,
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=temp,
                max_tokens=self.max_tokens,
                think=False,
                num_ctx=num_ctx,
            )
            return (res.content or "").strip()
        except Exception as e:
            log.warning("wake_voice_generation_failed", error=str(e))
            return ""


# ---------------------------------------------------------------------------
# Soma ebb — salience-scaled body presentation (quiet / normal / high)
# ---------------------------------------------------------------------------

_EBB_TIER_ORDER = {"quiet": 0, "normal": 1, "high": 2}


def _ebb_parse_floor(raw: str) -> str:
    s = (raw or "normal").strip().lower()
    if s in _EBB_TIER_ORDER:
        return s
    return "normal"


def _ebb_tier_at_least(tier: str, floor: str) -> str:
    return tier if _EBB_TIER_ORDER[tier] >= _EBB_TIER_ORDER[floor] else floor


# ---------------------------------------------------------------------------
# TonicBody — composition root
# ---------------------------------------------------------------------------


class TonicBody:
    """Coordinates bars, affects, noise, wake voice, and rendering into a single readable body state."""

    def __init__(self, config: dict[str, Any], soma_dir: Path) -> None:
        cfg = dict(config)
        cfg["ebb"] = _merge_ebb_personality(dict(cfg.get("ebb") or {}))

        self.bars = BarEngine(cfg)
        self.affects = AffectEngine(
            cycle_seconds=float(cfg.get("affect_cycle_seconds", 180)),
        )
        noise_cfg = cfg.get("noise") or {}
        self.noise = NoiseEngine(
            cycle_seconds=float(noise_cfg.get("cycle_seconds", 60)),
            max_fragments=int(noise_cfg.get("max_fragments", 8)),
            temperature=float(noise_cfg.get("temperature", 0.88)),
            max_tokens=int(noise_cfg.get("max_tokens", 240)),
            thematic_streak_max=int(noise_cfg.get("thematic_streak_max", 0)),
            semantic_dedup=bool(noise_cfg.get("semantic_dedup", False)),
            semantic_similarity_threshold=float(noise_cfg.get("semantic_similarity_threshold", 0.82)),
            semantic_dedup_retries=int(noise_cfg.get("semantic_dedup_retries", 2)),
        )
        self._noise_enabled = bool(noise_cfg.get("enabled", True))
        self._noise_model = str(noise_cfg.get("model") or "")
        self._pending_seed_text: str = ""
        self._pending_seed_trace_id: str = ""
        self._pending_seed_log_id: str = ""
        wake_cfg = cfg.get("wake_voice") or {}
        self.wake_voice = WakeVoice(
            temperature=float(wake_cfg.get("temperature", 0.8)),
            max_tokens=int(wake_cfg.get("max_tokens", 300)),
        )
        self._wake_voice_enabled = bool(wake_cfg.get("enabled", True))
        self._wake_voice_model = str(wake_cfg.get("model") or "")
        appraisal_cfg = cfg.get("appraisal") or {}
        self._appraisal_enabled = bool(appraisal_cfg.get("enabled", True))
        self.appraiser = SomaticAppraiser(
            temperature=float(appraisal_cfg.get("temperature", 0.3)),
            max_tokens=int(appraisal_cfg.get("max_tokens", 120)),
            output_format=str(appraisal_cfg.get("output_format", "json")),
            calibration_log=bool(appraisal_cfg.get("calibration_log", False)),
        )
        self.renderer = BodyRenderer()
        self._soma_dir = soma_dir
        self._recent_events: list[dict[str, Any]] = []
        self._max_events = 200
        self._current_affects: list[dict[str, Any]] = []
        self._tick_count = 0
        self._save_every = 6
        self._last_appraisal_for_noise: dict[str, Any] = {}

        obs_cfg = cfg.get("observability") or {}
        self._obs_timeline_enabled = bool(obs_cfg.get("timeline_enabled", False))
        self._obs_timeline_name = str(obs_cfg.get("timeline_filename", "soma_timeline.md"))
        self._obs_timeline_max_bytes = max(4096, int(obs_cfg.get("timeline_max_bytes", 65536)))

        ebb_cfg = cfg.get("ebb") or {}
        self._ebb_enabled = bool(ebb_cfg.get("enabled", True))
        w = ebb_cfg.get("weights") or {}
        wb = float(w.get("bar_deviation", 0.38))
        wc = float(w.get("conflict", 0.22))
        wi = float(w.get("impulse", 0.18))
        wa = float(w.get("affect_load", 0.12))
        wn = float(w.get("noise_fill", 0.10))
        ws = wb + wc + wi + wa + wn
        if ws <= 0:
            ws = 1.0
        self._ebb_w_bar = wb / ws
        self._ebb_w_conf = wc / ws
        self._ebb_w_imp = wi / ws
        self._ebb_w_aff = wa / ws
        self._ebb_w_noise = wn / ws
        self._ebb_quiet_below = float(ebb_cfg.get("quiet_below", 0.30))
        self._ebb_high_above = float(ebb_cfg.get("high_above", 0.58))
        self._ebb_reflex_scale = float(ebb_cfg.get("reflex_salience_scale", 0.75))
        self._engram_autonomous_floor = _ebb_parse_floor(str(ebb_cfg.get("autonomous_minimum", "normal")))
        self._ebb_quiet_noise_lines = max(0, int(ebb_cfg.get("quiet_max_noise_lines", 1)))
        self._ebb_normal_noise_lines = max(0, int(ebb_cfg.get("normal_max_noise_lines", 3)))
        self._ebb_high_noise_lines = max(0, int(ebb_cfg.get("high_max_noise_lines", 4)))
        self._ebb_skip_post_turn = bool(ebb_cfg.get("skip_post_turn_noise_when_quiet", True))
        self._ebb_debug_salience = bool(ebb_cfg.get("debug_salience", False))

    def emit(self, event: dict[str, Any]) -> None:
        """Record a structured event for the next bars tick."""
        if "type" not in event:
            return
        self._recent_events.append(event)
        if len(self._recent_events) > self._max_events:
            self._recent_events = self._recent_events[-self._max_events:]

    def recent_events(self, *, limit: int = 20) -> list[dict[str, Any]]:
        """Return recent events, skipping expired world pokes."""
        now = time.time()
        out: list[dict[str, Any]] = []
        for ev in self._recent_events[-max(1, int(limit or 20)) :]:
            if ev.get("type") == "world_poke":
                expires_at = float(ev.get("expires_at", 0) or 0)
                if expires_at and expires_at < now:
                    continue
            out.append(ev)
        return out

    def poke_world(
        self,
        *,
        prompt: str,
        source: str = "external",
        weight: float = 0.6,
        ttl_seconds: float = 3600.0,
    ) -> None:
        """Inject a loose external-world cue into soma/GEN context."""
        prompt = str(prompt or "").strip()
        if not prompt:
            return
        now = time.time()
        ttl = max(60.0, float(ttl_seconds or 3600.0))
        self.emit(
            {
                "type": "world_poke",
                "source": str(source or "external"),
                "prompt": prompt[:800],
                "weight": max(0.0, min(1.0, float(weight or 0.6))),
                "created_at": now,
                "expires_at": now + ttl,
            }
        )

    def apply_immediate(self, event: dict[str, Any]) -> None:
        """Apply an event to bars right now and record it for affect/noise context.

        Used by the somatic appraiser so bar changes are visible in the same
        perceive cycle rather than waiting for the next daemon heartbeat.  The
        event is marked ``_applied`` so ``tick_bars`` won't double-count it.
        """
        self.bars.apply_event(event)
        self.bars._clamp_all()
        event["_applied"] = True
        self._recent_events.append(event)
        if len(self._recent_events) > self._max_events:
            self._recent_events = self._recent_events[-self._max_events:]
        self.flush_body_md()

    async def tick_bars(self, dt_hours: float) -> None:
        """Advance bar decay, apply pending events, detect impulses/conflicts."""
        for ev in self._recent_events:
            if not ev.get("_applied"):
                self.bars.apply_event(ev)
                ev["_applied"] = True
        self.bars.tick(dt_hours)
        self.flush_body_md()
        self._tick_count += 1
        if self._tick_count % self._save_every == 0:
            self.save_state()

    async def maybe_tick_affects(
        self,
        client: Any,
        model: str,
        *,
        num_ctx: int | None = None,
    ) -> None:
        """Refresh affects if enough time has elapsed since the last derivation."""
        if not self.affects.should_tick():
            return
        self._current_affects = await self.affects.derive_affects(
            client,
            model,
            self.bars.snapshot_pct(),
            self.bars.momentum_delta(),
            self._recent_events[-15:],
            self.bars._active_conflicts,
            self.bars._active_impulses,
            latent_conflicts=self.bars._latent_conflicts,
            near_impulses=self.bars._near_impulses,
            num_ctx=num_ctx,
        )
        self.flush_body_md()

    async def maybe_tick_noise(
        self,
        client: Any,
        fallback_model: str,
        *,
        entity_name: str = "",
        journal_tail: str = "",
        conversation_tail: str = "",
        relationship_tail: str = "",
        num_ctx: int | None = None,
        embed_model: str = "",
        db_conn: Any | None = None,
    ) -> None:
        """Generate noise fragments if enabled and enough time has elapsed."""
        if not self._noise_enabled or not self.noise.should_tick():
            return
        model = self._noise_model or fallback_model
        if not model:
            return
        pct = self.bars.snapshot_pct()
        bars_line = ", ".join(f"{n}: {pct[n]}%" for n in self.bars.ordered_names)
        affects_line = self.renderer.render_affects(self._current_affects)
        noise_mode = self._noise_generation_mode(
            journal_tail=journal_tail,
            conversation_tail=conversation_tail,
        )
        lap = self._last_appraisal_for_noise
        pending = (self._pending_seed_text or "").strip()
        ptrace = (self._pending_seed_trace_id or "").strip()
        plog = (self._pending_seed_log_id or "").strip()
        self._pending_seed_text = ""
        self._pending_seed_trace_id = ""
        self._pending_seed_log_id = ""
        await self.noise.generate(
            client,
            model,
            bars_summary=bars_line,
            affects_summary=affects_line,
            recent_events=self._recent_events[-5:],
            journal_tail=journal_tail,
            conversation_tail=conversation_tail,
            entity_name=entity_name,
            mode=noise_mode,
            last_appraisal_tags=list(lap.get("tags") or []),
            last_appraisal_felt=str(lap.get("felt") or ""),
            relationship_tail=relationship_tail,
            num_ctx=num_ctx,
            pending_seed=pending,
            pending_seed_trace_id=ptrace,
            pending_seed_log_id=plog if pending else "",
            embed_model=embed_model,
            db_conn=db_conn if plog else None,
        )
        self.flush_body_md()

    def _noise_generation_mode(self, *, journal_tail: str = "", conversation_tail: str = "") -> str:
        """Choose GEN style based on immediate stimulation.

        Any real conversation tail -> coherent (ground to chat + events).
        High internal salience or external signal -> coherent.
        True idle -> entropic (still prompt-grounded, but lower stimulation).
        """
        conv_raw = (conversation_tail or "").strip()
        if conv_raw and conv_raw.lower() != "(silence)":
            return "coherent"

        sal = self.compute_salience()
        recent = self._recent_events[-8:]
        high_signal_types = {"message_received", "message_sent", "action", "appraisal", "world_poke"}
        signal_events = sum(1 for ev in recent if str(ev.get("type") or "") in high_signal_types)
        conv_load = len(conv_raw)
        journal_load = len((journal_tail or "").strip())

        if sal >= 0.52 or signal_events >= 2 or conv_load >= 180:
            return "coherent"
        if sal >= 0.38 and (signal_events >= 1 or conv_load >= 90 or journal_load >= 220):
            return "coherent"
        return "entropic"

    async def compose_wake_stirring(
        self,
        client: Any,
        model: str,
        *,
        entity_name: str = "",
        journal_tail: str = "",
        conversation_tail: str = "",
        relationship_blurb: str = "",
        last_mood: str = "",
        minutes_since_wake: float = 0.0,
        minutes_since_conversation: float = 0.0,
        num_ctx: int | None = None,
        voice_variant: int = 0,
        wake_voice_temperature: float | None = None,
    ) -> str:
        """Ask the subconscious WakeVoice to compose a stirring for an autonomous cycle."""
        if not self._wake_voice_enabled:
            return ""
        wake_model = self._wake_voice_model or model
        if not wake_model:
            return ""

        pct = self.bars.snapshot_pct()
        mom = self.bars.momentum_delta()
        bars_summary = self.renderer.render_bars(self.bars.ordered_names, pct, mom)
        affects_summary = self.renderer.render_affects(self._current_affects)
        impulses_summary = self.renderer.render_impulses(
            self.bars._active_impulses,
            self.bars._near_impulses,
        )
        conflicts_summary = self.renderer.render_conflicts(
            self.bars._active_conflicts,
            self.bars._latent_conflicts,
        )
        noise_fragments = self.noise.current_fragments()

        return await self.wake_voice.compose(
            client,
            wake_model,
            entity_name=entity_name,
            bars_summary=bars_summary,
            affects_summary=affects_summary,
            noise_fragments=noise_fragments,
            impulses_summary=impulses_summary,
            conflicts_summary=conflicts_summary,
            journal_tail=journal_tail,
            conversation_tail=conversation_tail,
            relationship_blurb=relationship_blurb,
            last_mood=last_mood,
            minutes_since_wake=minutes_since_wake,
            minutes_since_conversation=minutes_since_conversation,
            num_ctx=num_ctx,
            voice_variant=voice_variant,
            temperature=wake_voice_temperature,
        )

    async def appraise_and_apply(
        self,
        client: Any,
        model: str,
        *,
        text: str,
        person_name: str,
        relationship_blurb: str = "",
        relationship_metrics: dict[str, float] | None = None,
        num_ctx: int | None = None,
    ) -> dict[str, Any]:
        """Run somatic appraisal on an incoming message and apply bar effects immediately."""
        if not self._appraisal_enabled or not text:
            return {"bar_effects": {}, "tags": [], "felt": ""}
        result = await self.appraiser.appraise_input(
            client,
            model,
            text=text,
            person_name=person_name,
            bar_names=self.bars.ordered_names,
            bars_pct=self.bars.snapshot_pct(),
            relationship_blurb=relationship_blurb,
            relationship_metrics=relationship_metrics,
            num_ctx=num_ctx,
        )
        self._last_appraisal_for_noise = {
            "tags": list(result.get("tags") or []),
            "felt": str(result.get("felt") or ""),
            "bar_effects": dict(result.get("bar_effects") or {}),
        }
        if result["bar_effects"]:
            event = {
                "type": "appraisal",
                "subtype": "input",
                "from": person_name,
                "bar_effects": result["bar_effects"],
                "tags": result["tags"],
                "felt": result["felt"],
            }
            self.apply_immediate(event)
            log.info(
                "somatic_appraisal_applied",
                person=person_name,
                effects=result["bar_effects"],
                tags=result["tags"],
                felt=result["felt"],
            )
        return result

    async def appraise_interaction_and_apply(
        self,
        client: Any,
        model: str,
        *,
        input_text: str,
        reply_text: str,
        person_name: str,
        relationship_blurb: str = "",
        relationship_metrics: dict[str, float] | None = None,
        num_ctx: int | None = None,
    ) -> dict[str, Any]:
        """Run somatic appraisal on a completed exchange and apply bar effects."""
        if not self._appraisal_enabled or not reply_text:
            return {"bar_effects": {}, "tags": [], "felt": ""}
        result = await self.appraiser.appraise_interaction(
            client,
            model,
            input_text=input_text,
            reply_text=reply_text,
            person_name=person_name,
            bar_names=self.bars.ordered_names,
            bars_pct=self.bars.snapshot_pct(),
            relationship_blurb=relationship_blurb,
            relationship_metrics=relationship_metrics,
            num_ctx=num_ctx,
        )
        self._last_appraisal_for_noise = {
            "tags": list(result.get("tags") or []),
            "felt": str(result.get("felt") or ""),
            "bar_effects": dict(result.get("bar_effects") or {}),
        }
        if result["bar_effects"]:
            event = {
                "type": "appraisal",
                "subtype": "interaction",
                "from": person_name,
                "bar_effects": result["bar_effects"],
                "tags": result["tags"],
                "felt": result["felt"],
            }
            self.apply_immediate(event)
            log.info(
                "somatic_interaction_appraisal_applied",
                person=person_name,
                effects=result["bar_effects"],
                tags=result["tags"],
                felt=result["felt"],
            )
        return result

    def compute_salience(self) -> float:
        """Aggregate 0..1 internal arousal from bars, conflicts, impulses, affects, and GEN fill."""
        pct = self.bars.snapshot_pct()
        init = self.bars._initial
        names = self.bars.ordered_names
        n = len(names) or 1
        dev_sum = sum(
            abs(float(pct.get(name, 0)) - float(init.get(name, 50.0))) for name in names
        )
        bar_dev = min(1.0, (dev_sum / n) / 100.0)

        conflicts = self.bars._active_conflicts
        conf_score = 0.0
        if conflicts:
            conf_score = min(1.0, max(float(c.get("intensity", 0.0)) for c in conflicts))
        latent_c = getattr(self.bars, "_latent_conflicts", []) or []
        if latent_c:
            latent_boost = (
                min(1.0, max(float(c.get("intensity", 0.0)) for c in latent_c)) * 0.42
            )
            conf_score = min(1.0, max(conf_score, latent_boost))

        impulses = [i for i in self.bars._active_impulses if not i.get("on_cooldown")]
        imp_score = 0.0
        if impulses:
            imp_score = min(1.0, max(float(i.get("intensity", 0.0)) for i in impulses))
        near_imp = getattr(self.bars, "_near_impulses", []) or []
        if near_imp:
            near_boost = (
                min(1.0, max(float(i.get("intensity", 0.0)) for i in near_imp)) * 0.34
            )
            imp_score = min(1.0, max(imp_score, near_boost))

        affect_load = min(1.0, len(self._current_affects) / 6.0)

        frags = self.noise.current_fragments()
        mf = float(self.noise._fragments.maxlen or 8)
        noise_fill = min(1.0, len(frags) / max(1.0, mf))

        s = (
            self._ebb_w_bar * bar_dev
            + self._ebb_w_conf * conf_score
            + self._ebb_w_imp * imp_score
            + self._ebb_w_aff * affect_load
            + self._ebb_w_noise * noise_fill
        )
        return max(0.0, min(1.0, s))

    def compute_salience_breakdown(self) -> dict[str, Any]:
        """Structured salience components for logging and tuning (0..1-ish pieces)."""
        pct = self.bars.snapshot_pct()
        init = self.bars._initial
        names = self.bars.ordered_names
        n = len(names) or 1
        dev_sum = sum(
            abs(float(pct.get(name, 0)) - float(init.get(name, 50.0))) for name in names
        )
        bar_dev = min(1.0, (dev_sum / n) / 100.0)

        conflicts = self.bars._active_conflicts
        conf_score = 0.0
        if conflicts:
            conf_score = min(1.0, max(float(c.get("intensity", 0.0)) for c in conflicts))
        latent_c = getattr(self.bars, "_latent_conflicts", []) or []
        if latent_c:
            latent_boost = (
                min(1.0, max(float(c.get("intensity", 0.0)) for c in latent_c)) * 0.42
            )
            conf_score = min(1.0, max(conf_score, latent_boost))

        impulses = [i for i in self.bars._active_impulses if not i.get("on_cooldown")]
        imp_score = 0.0
        if impulses:
            imp_score = min(1.0, max(float(i.get("intensity", 0.0)) for i in impulses))
        near_imp = getattr(self.bars, "_near_impulses", []) or []
        if near_imp:
            near_boost = (
                min(1.0, max(float(i.get("intensity", 0.0)) for i in near_imp)) * 0.34
            )
            imp_score = min(1.0, max(imp_score, near_boost))

        affect_load = min(1.0, len(self._current_affects) / 6.0)

        frags = self.noise.current_fragments()
        mf = float(self.noise._fragments.maxlen or 8)
        noise_fill = min(1.0, len(frags) / max(1.0, mf))

        total = self.compute_salience()
        return {
            "total": round(total, 4),
            "bar_deviation": round(
                self._ebb_w_bar * bar_dev, 4
            ),
            "conflict": round(self._ebb_w_conf * conf_score, 4),
            "impulse": round(self._ebb_w_imp * imp_score, 4),
            "affect_load": round(self._ebb_w_aff * affect_load, 4),
            "noise_fill": round(self._ebb_w_noise * noise_fill, 4),
            "raw_bar_dev": round(bar_dev, 4),
            "raw_conflict": round(conf_score, 4),
            "raw_impulse": round(imp_score, 4),
        }

    def maybe_log_salience_debug(
        self,
        *,
        route: str,
        platform: str,
        presentation: str,
    ) -> None:
        if not self._ebb_debug_salience:
            return
        bd = self.compute_salience_breakdown()
        log.info(
            "soma_salience_debug",
            route=route,
            platform=platform,
            presentation=presentation,
            **bd,
        )

    def append_timeline_line(self, line: str) -> None:
        if not self._obs_timeline_enabled:
            return
        text = (line or "").strip()
        if not text:
            return
        try:
            path = self._soma_dir / self._obs_timeline_name
            path.parent.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
            block = f"{stamp}  {text}\n"
            if path.is_file():
                existing = path.read_text(encoding="utf-8", errors="replace")
                combined = existing + block
                if len(combined.encode("utf-8")) > self._obs_timeline_max_bytes:
                    combined = combined[-self._obs_timeline_max_bytes :]
                path.write_text(combined, encoding="utf-8")
            else:
                path.write_text(block, encoding="utf-8")
        except Exception as e:
            log.debug("soma_timeline_append_failed", error=str(e))

    def resolve_presentation(
        self,
        *,
        salience: float,
        route: str,
        platform: str,
    ) -> str:
        """Map salience + routing to ``quiet`` | ``normal`` | ``high`` for the prompt body."""
        if not self._ebb_enabled:
            return "high"
        s = max(0.0, min(1.0, float(salience)))
        r = str(route or "").strip().lower()
        if r == "reflex":
            s *= max(0.0, min(1.0, self._ebb_reflex_scale))
        if s >= self._ebb_high_above:
            tier = "high"
        elif s >= self._ebb_quiet_below:
            tier = "normal"
        else:
            tier = "quiet"

        plat = str(platform or "").strip().lower()
        if plat in ("autonomous", "automation"):
            tier = _ebb_tier_at_least(tier, self._engram_autonomous_floor)
        return tier

    def should_skip_post_turn_noise(self, *, route: str, platform: str) -> bool:
        """When ebb is quiet, optionally skip GEN regeneration after a turn to stay subtle."""
        if not self._ebb_enabled or not self._ebb_skip_post_turn:
            return False
        if not self._noise_enabled:
            return False
        sal = self.compute_salience()
        pres = self.resolve_presentation(salience=sal, route=route, platform=platform)
        return pres == "quiet"

    def _noise_line_cap(self, presentation: str) -> int:
        if presentation == "quiet":
            return self._ebb_quiet_noise_lines
        if presentation == "normal":
            return self._ebb_normal_noise_lines
        if self._ebb_high_noise_lines > 0:
            return self._ebb_high_noise_lines
        return 4

    def render_body(self, *, presentation: str = "high") -> str:
        """Assemble the body markdown the agent reads.

        This is the agent's sole window into its own internal state.
        The agent cannot edit ``body.md`` — only soma subsystems write it.

        When ``ebb`` is enabled, callers pass ``presentation`` (``quiet`` / ``normal`` /
        ``high``) to scale token load; the default ``high`` is the full historical layout.
        """
        pres = "high"
        if self._ebb_enabled:
            pres = presentation if presentation in ("quiet", "normal", "high") else "high"
        return self._render_body_markdown(presentation=pres)

    def _render_body_markdown(self, *, presentation: str) -> str:
        pct = self.bars.snapshot_pct()
        mom = self.bars.momentum_delta()

        if presentation == "high":
            bars = self.renderer.render_bars(self.bars.ordered_names, pct, mom)
        else:
            bars = " · ".join(f"{n} {pct.get(n, 0)}%" for n in self.bars.ordered_names)

        affects_src = self._current_affects[:2] if presentation == "quiet" else self._current_affects
        affects = self.renderer.render_affects(affects_src)

        cap = self._noise_line_cap(presentation)
        fragments = self.noise.current_fragments()
        if cap <= 0:
            noise_inner = "(quiet)"
        else:
            take = fragments[-cap:] if fragments else []
            noise_inner = "\n".join(take) if take else "(quiet)"

        conflicts_txt = self.renderer.render_conflicts(
            self.bars._active_conflicts,
            self.bars._latent_conflicts,
        )
        impulses_txt = self.renderer.render_impulses(
            self.bars._active_impulses,
            self.bars._near_impulses,
        )

        blocks: list[str] = [
            f"## Bars\n{bars}",
            f"## Affects\n{affects}",
            f"## Noise\n{noise_inner}",
        ]
        if not (
            presentation == "quiet"
            and self.renderer.conflicts_section_quiet_skip(conflicts_txt)
        ):
            blocks.append(f"## Conflicts\n{conflicts_txt}")
        if not (
            presentation == "quiet"
            and self.renderer.impulses_section_quiet_skip(impulses_txt)
        ):
            blocks.append(f"## Impulses\n{impulses_txt}")
        return "\n\n".join(blocks)

    def flush_body_md(self) -> None:
        """Write the current body state to ``body.md`` in the soma directory.

        This is the canonical read-only file the main agent consumes.
        Only soma subsystems (bars, affects, noise, appraisal) call this —
        the agent never writes to it.
        """
        try:
            md = self.render_body(presentation="high")
            body_path = self._soma_dir / "body.md"
            body_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = body_path.with_suffix(".tmp")
            tmp.write_text(md, encoding="utf-8")
            tmp.replace(body_path)
        except Exception as e:
            log.debug("body_md_flush_failed", error=str(e))

    def reset_baseline(self) -> None:
        """Reset bars to YAML initials, clear affects/events/noise, rewrite ``body.md`` and ``soma-state.json``.

        Does not touch memory DB except via ``save_state_db`` on the entity.
        Restart a long-running worker after calling so in-memory tonic reloads.
        """
        self.bars.reset_to_initial()
        self._current_affects = []
        self.affects._previous_affects = []
        self.affects._last_tick = 0.0
        self.noise._fragments.clear()
        self.noise._last_tick = 0.0
        self._recent_events.clear()
        self.flush_body_md()
        self.save_state()
        log.info(
            "soma_reset_baseline",
            bars={k: round(v, 1) for k, v in self.bars._values.items()},
        )

    def snapshot_for_emotion(self) -> tuple[str, float]:
        """Derive (EmotionCategory value, intensity) from bars for backward compat."""
        pct = self.bars.snapshot_pct()
        best_cat = "neutral"
        best_intensity = 0.3
        best_overshoot = -1.0
        for bar_name, emotion_val, threshold in _BAR_TO_EMOTION:
            val = float(pct.get(bar_name, 0))
            if val >= threshold:
                overshoot = val - threshold
                if overshoot > best_overshoot:
                    best_overshoot = overshoot
                    best_cat = emotion_val
                    best_intensity = min(1.0, 0.4 + (overshoot / 100.0) * 0.6)
        return best_cat, best_intensity

    def _snapshot_all(self) -> dict[str, Any]:
        """Build a unified snapshot of all three soma layers."""
        return {
            "values": dict(self.bars._values),
            "initial": dict(self.bars._initial),
            "somatic_markers": {
                source: dict(effects)
                for source, effects in self.bars._somatic_markers.items()
            },
            "history": list(self.bars._history),
            "ordered_names": list(self.bars._ordered_names),
            "saved_at": time.time(),
            "affects": [dict(a) for a in self._current_affects],
            "noise_fragments": [
                {"text": f.text, "salience": f.salience, "trace": f.seed_trace_id}
                for f in self.noise._fragments
            ],
            "noise_coherent_streak": int(getattr(self.noise, "_coherent_streak", 0)),
        }

    def _restore_from_snapshot(self, data: dict[str, Any]) -> bool:
        """Restore all three soma layers from a unified snapshot."""
        saved_names = data.get("ordered_names", [])
        if saved_names != self.bars._ordered_names:
            log.info("soma_bar_names_changed", saved=saved_names, current=self.bars._ordered_names)
            return False
        self.bars._values = {k: float(data["values"][k]) for k in self.bars._ordered_names}
        saved_initial = data.get("initial")
        if isinstance(saved_initial, dict):
            self.bars._initial = {
                k: float(saved_initial.get(k, self.bars._initial[k]))
                for k in self.bars._ordered_names
            }
        saved_markers = data.get("somatic_markers")
        if isinstance(saved_markers, dict):
            self.bars._somatic_markers = {
                str(source): {
                    str(bar): float(delta)
                    for bar, delta in effects.items()
                    if bar in self.bars._ordered_names
                }
                for source, effects in saved_markers.items()
                if isinstance(effects, dict)
            }
        self.bars._history.clear()
        for snap in data.get("history", []):
            self.bars._history.append({k: float(snap.get(k, 0)) for k in self.bars._ordered_names})
        if not self.bars._history:
            self.bars._history.append(dict(self.bars._values))
        saved_at = data.get("saved_at")
        if saved_at is not None:
            self.bars._apply_offline_decay(float(saved_at))

        saved_affects = data.get("affects")
        if isinstance(saved_affects, list) and saved_affects:
            self._current_affects = saved_affects
            self.affects._previous_affects = saved_affects
            log.info("soma_affects_restored", count=len(saved_affects))

        saved_noise = data.get("noise_fragments")
        if isinstance(saved_noise, list) and saved_noise:
            self.noise._fragments.clear()
            for frag in saved_noise:
                if isinstance(frag, dict):
                    t = str(frag.get("text") or "").strip()
                    if not t:
                        continue
                    nf = NoiseFragment(
                        text=t,
                        salience=str(frag.get("salience") or "reflective"),
                        seed_trace_id=str(frag.get("trace") or frag.get("seed_trace_id") or ""),
                    )
                    if not _fragment_is_duplicate(t, self.noise._fragments):
                        self.noise._fragments.append(nf)
                    continue
                if not isinstance(frag, str):
                    continue
                cleaned = _split_noise_fragments(frag)
                for c in cleaned:
                    if not _fragment_is_duplicate(c, self.noise._fragments):
                        self.noise._fragments.append(
                            NoiseFragment(text=c, salience=classify_noise_fragment_salience(c))
                        )
            log.info("soma_noise_restored", count=len(self.noise._fragments))

        ncs = data.get("noise_coherent_streak")
        if ncs is not None:
            try:
                self.noise._coherent_streak = max(0, int(ncs))
            except (TypeError, ValueError):
                pass

        return True

    def save_state(self) -> None:
        """Save full soma state to filesystem (fallback for local/SQLite deployments)."""
        state_path = self._soma_dir / "soma-state.json"
        try:
            data = self._snapshot_all()
            state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = state_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
            tmp.replace(state_path)
        except Exception as e:
            log.warning("soma_save_state_failed", error=str(e))

    def restore_state(self) -> bool:
        """Restore full soma state from filesystem (fallback for local/SQLite deployments)."""
        state_path = self._soma_dir / "soma-state.json"
        legacy_path = self._soma_dir / "soma-bar-state.json"
        target = state_path if state_path.is_file() else legacy_path
        if not target.is_file():
            return False
        try:
            data = json.loads(target.read_text(encoding="utf-8"))
            ok = self._restore_from_snapshot(data)
            if ok:
                log.info("soma_state_restored", path=str(target.name),
                         values={k: round(v, 1) for k, v in self.bars._values.items()})
                self.flush_body_md()
            return ok
        except Exception:
            log.warning("soma_state_restore_failed", exc_info=True)
            return False

    async def save_state_db(self, conn: Any) -> None:
        """Persist full soma state to entity_state table (Postgres-durable)."""
        data = self._snapshot_all()
        payload = json.dumps(data, separators=(",", ":"))
        try:
            await conn.execute(
                "INSERT OR REPLACE INTO entity_state (key, value) VALUES (?, ?)",
                ("soma_state_v2", payload),
            )
            await conn.commit()
        except Exception as e:
            log.warning("soma_save_state_db_failed", error=str(e))

    async def restore_state_db(self, conn: Any) -> bool:
        """Restore full soma state from entity_state table."""
        try:
            cur = await conn.execute(
                "SELECT value FROM entity_state WHERE key = ?",
                ("soma_state_v2",),
            )
            row = await cur.fetchone()
            if not row:
                cur = await conn.execute(
                    "SELECT value FROM entity_state WHERE key = ?",
                    ("soma_bar_state_v1",),
                )
                row = await cur.fetchone()
            if not row:
                return False
            data = json.loads(row[0])
            ok = self._restore_from_snapshot(data)
            if ok:
                log.info("soma_state_restored_db",
                         values={k: round(v, 1) for k, v in self.bars._values.items()})
            return ok
        except Exception:
            log.warning("soma_state_restore_db_failed", exc_info=True)
            return False

    def drain_recent_events(self) -> list[dict[str, Any]]:
        """Return and clear recent events (for periodic cleanup)."""
        events = list(self._recent_events)
        self._recent_events.clear()
        return events
