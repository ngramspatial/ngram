"""Desire-pressure synthesis from soma, drives, and world pokes.

This module bridges low-level internal signals (bars, impulses, conflicts,
noise fragments) into explicit "what feels pressing now" candidates that the
autonomous wake cycle can reason about.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import Any

from ngram.identity.drives import Drive


@dataclass
class DesireCandidate:
    kind: str
    urgency: float
    why: str
    target: str = ""
    source: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "urgency": round(float(self.urgency), 3),
            "why": self.why,
            "target": self.target,
            "source": self.source,
        }


def infer_desires(
    *,
    tonic: Any,
    drives: list[Drive],
    max_items: int = 3,
    entropy_extras: int = 0,
) -> list[dict[str, Any]]:
    """Return ranked desire candidates inferred from current internal state.

    ``entropy_extras`` adds synthetic candidates (novelty, avoidance, contrarian pulls) so
    autonomous wakes see a wider affective space than drives/impulses alone.
    """
    out: list[DesireCandidate] = []
    now = time.time()

    # 1) Soma impulses are direct "pull" signals.
    for imp in getattr(tonic.bars, "_active_impulses", []) or []:
        if imp.get("on_cooldown"):
            continue
        raw_drive = str(imp.get("drive") or "").strip()
        bar_name = _map_soma_drive_name(raw_drive)
        bar_pct = 0
        try:
            bar_pct = int((tonic.bars.snapshot_pct() or {}).get(bar_name, 0))
        except Exception:
            bar_pct = 0
        urgency = min(0.98, 0.72 + (bar_pct / 100.0) * 0.22)
        out.append(
            DesireCandidate(
                kind=_impulse_to_kind(str(imp.get("type") or "")),
                urgency=urgency,
                why=f"impulse:{imp.get('label') or imp.get('type') or 'unknown'}",
                target=str(imp.get("label") or "").strip(),
                source="impulse",
            )
        )

    # 2) Conflicts create a pressure to resolve tension.
    for c in getattr(tonic.bars, "_active_conflicts", []) or []:
        intensity = float(c.get("intensity", 0.0) or 0.0)
        if intensity <= 0:
            continue
        out.append(
            DesireCandidate(
                kind="resolve_tension",
                urgency=min(0.95, 0.55 + intensity * 0.4),
                why=f"conflict:{c.get('label') or 'unnamed'}",
                target=str(c.get("label") or "").strip(),
                source="conflict",
            )
        )

    # 3) Drive thresholds map to broad wants.
    for d in drives:
        if d.level < d.threshold:
            continue
        span = max(0.01, 1.0 - d.threshold)
        over = max(0.0, d.level - d.threshold)
        urgency = min(0.94, 0.5 + (over / span) * 0.44)
        out.append(
            DesireCandidate(
                kind=_drive_to_kind(d.name),
                urgency=urgency,
                why=f"drive:{d.name}={d.level:.2f}",
                target=d.name,
                source="drive",
            )
        )

    # 4) External world pokes can seed latent wants.
    recent_events = []
    try:
        recent_events = tonic.recent_events(limit=40)
    except Exception:
        recent_events = list(getattr(tonic, "_recent_events", []) or [])[-40:]
    for ev in recent_events:
        if ev.get("type") != "world_poke":
            continue
        expires_at = float(ev.get("expires_at", 0) or 0)
        if expires_at and expires_at < now:
            continue
        prompt = str(ev.get("prompt") or "").strip()
        if not prompt:
            continue
        weight = max(0.0, min(1.0, float(ev.get("weight", 0.6) or 0.6)))
        out.append(
            DesireCandidate(
                kind="explore_world",
                urgency=min(0.9, 0.35 + weight * 0.5),
                why=f"world_poke:{str(ev.get('source') or 'external')}",
                target=prompt[:90],
                source="world_poke",
            )
        )

    # 5) Question-like noise fragments signal unresolved internal loops.
    for frag in (tonic.noise.current_fragments() or [])[-3:]:
        fl = str(frag).lower()
        if "?" not in fl and all(k not in fl for k in ("should", "wonder", "why", "need to")):
            continue
        out.append(
            DesireCandidate(
                kind="follow_thread",
                urgency=0.58,
                why="noise unresolved thread",
                target=str(frag)[:90],
                source="noise",
            )
        )

    # 6) Entropy / soul extras — widen the space of "what feels pressing".
    n_extras = max(0, int(entropy_extras or 0))
    if n_extras > 0:
        out.extend(_sample_entropy_desire_candidates(tonic, n_extras))

    ranked = sorted(out, key=lambda d: d.urgency, reverse=True)
    deduped: list[DesireCandidate] = []
    seen: set[tuple[str, str]] = set()
    for item in ranked:
        key = (item.kind, item.target.lower().strip())
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
        if len(deduped) >= max(1, int(max_items or 3)):
            break
    return [d.as_dict() for d in deduped]


def _sample_entropy_desire_candidates(tonic: Any, n: int) -> list[DesireCandidate]:
    """Synthetic pressures: fears, novelty hunger, opinions — not from user text."""
    pool: list[tuple[str, str, str]] = [
        ("fear_stir", "a specific way stalling could hurt you", "entropy:soul"),
        ("novelty_hunger", "something outside your default loop this week", "entropy:soul"),
        ("avoidance", "a thread you keep sidestepping", "entropy:soul"),
        ("contrarian", "a comfortable belief worth poking", "entropy:soul"),
        ("longing", "a small honest reach you owe someone or something", "entropy:soul"),
        ("opinion", "something you actually think (not neutral)", "entropy:soul"),
        ("world_expand", "a place, field, or practice you barely know", "entropy:soul"),
        ("resolve_tension", "two pulls that disagree inside you", "entropy:soul"),
        ("explore", "a question you pretend not to care about", "entropy:soul"),
        ("reach_out", "contact you keep deferring", "entropy:soul"),
    ]
    random.shuffle(pool)
    out: list[DesireCandidate] = []
    sal = 0.5
    try:
        sal = float(tonic.compute_salience())
    except Exception:
        pass
    for i in range(min(n, len(pool))):
        kind, target, why = pool[i]
        base = 0.38 + sal * 0.28 + random.uniform(0.0, 0.22)
        urgency = min(0.91, base + (i * 0.01))
        out.append(
            DesireCandidate(
                kind=kind,
                urgency=urgency,
                why=why,
                target=target[:90],
                source="entropy",
            )
        )
    return out


def _drive_to_kind(name: str) -> str:
    return {
        "connection": "reach_out",
        "curiosity": "explore",
        "expression": "create",
        "autonomy": "self_direct",
        "comfort": "stabilize",
    }.get(name, "explore")


def _impulse_to_kind(kind: str) -> str:
    return {
        "reach_out": "reach_out",
        "explore": "explore",
    }.get(kind, "act")


def _map_soma_drive_name(name: str) -> str:
    return {
        "connection": "social",
        "expression": "creative",
    }.get(name, name)
