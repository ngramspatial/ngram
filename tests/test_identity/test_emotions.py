import pytest

from ngram.config import HarnessConfig, entity_from_dict
from ngram.identity.emotions import EmotionEngine
from ngram.models import EmotionCategory


@pytest.fixture
def entity_config():
    h = HarnessConfig()
    data = {
        "name": "E",
        "personality": {
            "core_traits": {
                "curiosity": 0.5,
                "warmth": 0.8,
                "assertiveness": 0.5,
                "humor": 0.5,
                "openness": 0.5,
                "neuroticism": 0.2,
                "conscientiousness": 0.5,
            },
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
async def test_process_stimulus_positive(entity_config):
    eng = EmotionEngine(entity_config)
    await eng.process_stimulus("positive", 0.9, {"relationship_warmth": 0.5})
    assert eng.get_state().primary in (EmotionCategory.CONTENT, EmotionCategory.NEUTRAL, EmotionCategory.CURIOUS)


@pytest.mark.asyncio
async def test_tick_decay(entity_config):
    eng = EmotionEngine(entity_config)
    eng.get_state().intensity = 0.9
    await eng.tick()
    assert eng.get_state().intensity <= 0.9
