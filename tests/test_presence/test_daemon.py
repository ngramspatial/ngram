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


@pytest.mark.asyncio
async def test_daemon_start_recovers_saved_code_work_once(entity_config, monkeypatch):
    from unittest.mock import AsyncMock, MagicMock

    from ngram.presence.daemon import PresenceDaemon

    entity = Entity(entity_config)
    entity.start_code_tasks = MagicMock()
    scheduler = MagicMock(running=False)
    scheduler.start.side_effect = lambda: setattr(scheduler, "running", True)
    monkeypatch.setattr("ngram.presence.daemon.AsyncIOScheduler", lambda: scheduler)
    engine = MagicMock(startup=AsyncMock(), shutdown=AsyncMock())
    monkeypatch.setattr("ngram.presence.daemon.AutomationEngine", lambda *_: engine)
    daemon = PresenceDaemon(entity)
    try:
        await daemon.start()
        await daemon.start()
        entity.start_code_tasks.assert_called_once_with()
    finally:
        await daemon.stop()
