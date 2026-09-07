import pytest

from ngram.config import HarnessConfig, entity_from_dict
from ngram.identity.drives import DriveSystem


@pytest.fixture
def entity_config():
    h = HarnessConfig()
    data = {
        "name": "E",
        "personality": {
            "core_traits": {k: 0.7 for k in ["curiosity", "warmth", "assertiveness", "humor", "openness", "neuroticism", "conscientiousness"]},
            "behavioral_patterns": {},
            "voice": {},
            "backstory": "",
        },
        "drives": {"curiosity_topics": [], "attachment_threshold": 5, "restlessness_decay": 3600, "initiative_cooldown": 1800},
        "cognition": {},
        "presence": {"platforms": [], "daemon": {}},
    }
    return entity_from_dict(h, data)


def test_drive_tick_and_cooldown(entity_config):
    ds = DriveSystem(entity_config)
    now = ds._last_initiative + 11.0
    assert ds.can_initiate(now, 10)
    ds.register_initiative_time(now)
    assert not ds.can_initiate(now + 5, 10)
    assert ds.can_initiate(now + 11, 10)
