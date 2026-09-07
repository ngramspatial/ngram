"""Tests for exogenous GEN seeding."""

from __future__ import annotations

import pytest

from ngram.identity.noise_seeder import NoiseSeeder, _normalize_weights
from ngram.identity.soma import classify_noise_fragment_salience


def test_normalize_weights_defaults_when_empty() -> None:
    w = _normalize_weights({})
    assert abs(sum(w.values()) - 1.0) < 1e-6
    assert len(w) == 10


def test_classify_salience_curiosity_question() -> None:
    assert classify_noise_fragment_salience("what even is biosemiotics?") == "curious"


def test_classify_salience_restless() -> None:
    assert classify_noise_fragment_salience("i need to do something different today") == "restless"


@pytest.mark.asyncio
async def test_noise_seeder_temporal_fallback(tmp_path) -> None:
    """Temporal strategy always yields when other stores are empty."""
    import aiosqlite

    from ngram.memory.store import SCHEMA

    db_path = tmp_path / "m.db"
    conn = await aiosqlite.connect(str(db_path))
    await conn.executescript(SCHEMA)
    seeder = NoiseSeeder(
        entity_name="t",
        soma_cfg={
            "noise_seeder": {
                "enabled": True,
                "cycle_seconds": 1,
                "source_weights": {"temporal": 1.0},
            }
        },
        knowledge_path=tmp_path / "k.md",
        journal_path=tmp_path / "j.md",
        curiosity_topics=[],
        relational_enabled=False,
        web_tools_enabled=False,
    )
    class _Tonic:
        _pending_seed_text = ""
        _pending_seed_trace_id = ""
        _pending_seed_log_id = ""

    tonic = _Tonic()
    await seeder.tick(conn, tonic=tonic, client=None)
    assert getattr(tonic, "_pending_seed_text", "")
    assert "[time]" in tonic._pending_seed_text
    await conn.close()
