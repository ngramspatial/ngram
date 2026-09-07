import pytest

from ngram.config import HarnessConfig, entity_from_dict
from ngram.entity import Entity


@pytest.fixture
def entity_config():
    h = HarnessConfig()
    data = {
        "name": "D",
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


def test_entity_construct(entity_config):
    e = Entity(entity_config)
    assert e.config.name == "D"
