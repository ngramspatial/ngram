"""Tests for time-decayed relationship familiarity."""

from __future__ import annotations

import time

import pytest

from ngram.config import MemoryHarnessSettings
from ngram.memory.relational import RelationalMemory, _decay_toward_floor
from ngram.memory.store import MemoryStore


@pytest.mark.parametrize(
    "dt_hours,half_life,expected_mid",
    [
        (0, 336, 0.8),
        (336, 336, 0.44),
    ],
)
def test_decay_toward_floor_half_life(dt_hours, half_life, expected_mid):
    out = _decay_toward_floor(0.8, floor=0.08, dt_hours=dt_hours, half_life_hours=half_life)
    assert abs(out - expected_mid) < 0.02


@pytest.mark.asyncio
async def test_familiarity_decays_after_gap_then_bumps(tmp_path):
    db = tmp_path / "r.db"
    store = MemoryStore(str(db))
    mem_cfg = MemoryHarnessSettings(
        familiarity_decay_half_life_hours=24.0,
        familiarity_floor=0.1,
        familiarity_bump_meaningful=0.05,
        familiarity_bump_light=0.02,
    )
    relm = RelationalMemory(store, mem_cfg)
    conn = await store.connect()
    try:
        await relm.upsert_interaction(conn, "u1", "Alice", meaningful=True)
        await conn.execute(
            "UPDATE relationships SET familiarity = 0.92, last_interaction = ? WHERE person_id = ?",
            (time.time() - 120 * 3600, "u1"),
        )
        await conn.commit()
        r2 = await relm.upsert_interaction(conn, "u1", "Alice", meaningful=True)
        assert r2.familiarity < 0.5
        assert r2.interaction_count == 2
    finally:
        await conn.close()
