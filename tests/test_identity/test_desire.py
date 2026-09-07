from __future__ import annotations

import time
from dataclasses import dataclass

from ngram.identity.desire import infer_desires
from ngram.identity.drives import Drive


class _FakeBars:
    def __init__(self) -> None:
        self._active_impulses: list[dict] = []
        self._active_conflicts: list[dict] = []
        self._pct = {"social": 50, "curiosity": 50, "creative": 40, "tension": 20, "comfort": 65}

    def snapshot_pct(self) -> dict[str, int]:
        return dict(self._pct)


class _FakeNoise:
    def __init__(self) -> None:
        self._fragments: list[str] = []

    def current_fragments(self) -> list[str]:
        return list(self._fragments)


@dataclass
class _FakeTonic:
    bars: _FakeBars
    noise: _FakeNoise
    _events: list[dict]

    def recent_events(self, *, limit: int = 20) -> list[dict]:
        return self._events[-limit:]


def _tonic() -> _FakeTonic:
    return _FakeTonic(bars=_FakeBars(), noise=_FakeNoise(), _events=[])


def test_impulse_generates_high_urgency_desire() -> None:
    tonic = _tonic()
    tonic.bars._pct["social"] = 90
    tonic.bars._active_impulses = [{"drive": "social", "type": "reach_out", "label": "reach_out"}]
    desires = infer_desires(tonic=tonic, drives=[], max_items=3)
    assert desires
    assert desires[0]["kind"] == "reach_out"
    assert desires[0]["urgency"] >= 0.8


def test_world_poke_is_exposed_as_explore_world_desire() -> None:
    tonic = _tonic()
    tonic._events.append(
        {
            "type": "world_poke",
            "source": "weather",
            "prompt": "big storm tonight over downtown",
            "weight": 0.9,
            "expires_at": time.time() + 3600,
        }
    )
    desires = infer_desires(tonic=tonic, drives=[], max_items=3)
    assert any(d["kind"] == "explore_world" for d in desires)


def test_expired_world_poke_is_ignored() -> None:
    tonic = _tonic()
    tonic._events.append(
        {
            "type": "world_poke",
            "source": "news",
            "prompt": "old stale poke",
            "weight": 1.0,
            "expires_at": time.time() - 5,
        }
    )
    desires = infer_desires(tonic=tonic, drives=[], max_items=3)
    assert all(d["kind"] != "explore_world" for d in desires)


def test_drive_threshold_maps_to_desire_kind() -> None:
    tonic = _tonic()
    drives = [Drive(name="curiosity", level=0.93, threshold=0.72)]
    desires = infer_desires(tonic=tonic, drives=drives, max_items=3)
    assert any(d["kind"] == "explore" for d in desires)
