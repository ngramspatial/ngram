"""Person messaging routes persist in entity_state across process restarts."""

from __future__ import annotations

import json

import pytest

from ngram.config import HarnessConfig, entity_from_dict
from ngram.entity import PERSON_ROUTES_STATE_KEY, Entity
from ngram.models import Input


def _minimal_entity_config(tmp_path, name: str = "PersistTest"):
    h = HarnessConfig()
    h.memory.database_path = str(tmp_path / "{entity_name}.db")
    data = {
        "name": name,
        "personality": {
            "core_traits": {k: 0.5 for k in ["curiosity", "warmth", "assertiveness", "humor", "openness", "neuroticism", "conscientiousness"]},
            "behavioral_patterns": {},
            "voice": {},
            "backstory": "Test.",
        },
        "drives": {
            "curiosity_topics": [],
            "attachment_threshold": 5,
            "restlessness_decay": 3600,
            "initiative_cooldown": 1800,
        },
        "cognition": {"rolling_history_max_messages": 8},
        "presence": {"platforms": [{"type": "cli"}], "daemon": {}},
    }
    return entity_from_dict(h, data)


@pytest.mark.asyncio
async def test_person_routes_roundtrip_via_entity_state(tmp_path) -> None:
    ec = _minimal_entity_config(tmp_path)
    ent = Entity(ec)
    ent._person_routes = {
        "555": {
            "platform": "telegram",
            "channel": "555",
            "person_id": "555",
            "person_name": "Yamashi",
            "chat_type": "private",
            "at": 1700000000.0,
        },
        "name:yamashi": {
            "platform": "telegram",
            "channel": "555",
            "person_id": "555",
            "person_name": "Yamashi",
            "chat_type": "private",
            "at": 1700000000.0,
        },
    }
    async with ent.store.session() as conn:
        await ent._persist_person_routes(conn)

    ent2 = Entity(ec)
    assert ent2._person_routes == {}
    async with ent2.store.session() as conn:
        await ent2._ensure_state_hydrated(conn)
    assert ent2._person_routes["555"]["person_name"] == "Yamashi"
    assert ent2._person_routes["555"]["platform"] == "telegram"


@pytest.mark.asyncio
async def test_hydrate_merges_disk_without_wiping_memory_keys(tmp_path) -> None:
    ec = _minimal_entity_config(tmp_path)
    ent = Entity(ec)
    async with ent.store.session() as conn:
        await conn.execute(
            "INSERT OR REPLACE INTO entity_state (key, value) VALUES (?, ?)",
            (
                PERSON_ROUTES_STATE_KEY,
                json.dumps(
                    {
                        "entries": {
                            "1": {
                                "platform": "telegram",
                                "channel": "1",
                                "person_id": "1",
                                "person_name": "Alice",
                                "chat_type": "private",
                                "at": 100.0,
                            }
                        }
                    }
                ),
            ),
        )
        await conn.commit()

    ent._person_routes["2"] = {
        "platform": "discord",
        "channel": "dm-9",
        "person_id": "2",
        "person_name": "Bob",
        "chat_type": "direct",
        "at": 200.0,
    }
    async with ent.store.session() as conn:
        await ent._ensure_state_hydrated(conn)

    assert "1" in ent._person_routes
    assert ent._person_routes["1"]["person_name"] == "Alice"
    assert "2" in ent._person_routes
    assert ent._person_routes["2"]["person_name"] == "Bob"


@pytest.mark.asyncio
async def test_remember_after_hydrate_updates_timestamp(tmp_path) -> None:
    ec = _minimal_entity_config(tmp_path)
    ent = Entity(ec)
    inp_early = Input(
        text="hi",
        person_id="99",
        person_name="Zed",
        channel="99",
        platform="telegram",
        metadata={"chat_type": "private"},
    )
    ent._remember_person_route(inp_early)
    async with ent.store.session() as conn:
        await ent._persist_person_routes(conn)

    ent2 = Entity(ec)
    inp_late = Input(
        text="again",
        person_id="99",
        person_name="Zed",
        channel="99",
        platform="telegram",
        metadata={"chat_type": "private"},
    )
    async with ent2.store.session() as conn:
        await ent2._ensure_state_hydrated(conn)
        early_loaded = float(ent2._person_routes["99"]["at"])
        ent2._remember_person_route(inp_late)
        late_at = float(ent2._person_routes["99"]["at"])
        assert late_at >= early_loaded
        await ent2._persist_person_routes(conn)

    ent3 = Entity(ec)
    async with ent3.store.session() as conn:
        await ent3._ensure_state_hydrated(conn)
    assert float(ent3._person_routes["99"]["at"]) == late_at
