import pytest

from ngram.config import HarnessConfig, entity_from_dict
from ngram.memory.consolidation import ConsolidationJob
from ngram.memory.episodic import EpisodicMemory
from ngram.memory.store import MemoryStore
from ngram.models import EmotionCategory, Episode


@pytest.fixture
def entity_config():
    h = HarnessConfig()
    data = {
        "name": "C",
        "personality": {
            "core_traits": {k: 0.5 for k in ["curiosity", "warmth", "assertiveness", "humor", "openness", "neuroticism", "conscientiousness"]},
            "behavioral_patterns": {},
            "voice": {},
            "backstory": "",
        },
        "drives": {"curiosity_topics": [], "attachment_threshold": 5, "restlessness_decay": 3600, "initiative_cooldown": 1800},
        "cognition": {},
        "presence": {"platforms": [], "daemon": {}},
    }
    return entity_from_dict(h, data)


@pytest.mark.asyncio
async def test_consolidation_runs(tmp_path, entity_config):
    store = MemoryStore(str(tmp_path / "c.db"))
    episodic = EpisodicMemory(entity_config, store)
    job = ConsolidationJob(entity_config)
    conn = await store.connect()
    try:
        await episodic.save_episode(
            conn,
            Episode(
                id="x",
                timestamp=1.0,
                summary="s",
                participants=[],
                emotional_imprint=EmotionCategory.NEUTRAL,
                emotional_intensity=0.1,
                significance=0.5,
                tags=[],
            ),
        )
        await job.run(conn, None)
    finally:
        await conn.close()
