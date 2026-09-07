"""Attachment persistence helpers."""

from __future__ import annotations

import base64

import pytest

from ngram.config import HarnessConfig, entity_from_dict
from ngram.models import Input
from ngram.storage import build_attachment_store, load_stored_attachment, persist_incoming_attachments

# 1×1 transparent PNG
PNG_1X1_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


@pytest.fixture
def entity_cfg(tmp_path):
    h = HarnessConfig()
    h.memory.database_path = str(tmp_path / "e" / "memory.db")
    h.attachments.local_dir = str(tmp_path / "e" / "attachments")
    data = {
        "name": "AttTest",
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
async def test_persist_incoming_attachments_sets_refs(entity_cfg, tmp_path):
    store = build_attachment_store(entity_cfg)
    inp = Input(
        text="hi",
        person_id="u1",
        person_name="U",
        channel="99",
        platform="discord",
        images=[{"base64": PNG_1X1_B64, "mime": "image/png"}],
    )
    out = await persist_incoming_attachments(store, inp)
    assert out.images[0].get("storage_ref")
    assert out.metadata.get("attachment_storage_refs")
    raw = await load_stored_attachment(store, out.images[0]["storage_ref"])
    assert raw == base64.standard_b64decode(PNG_1X1_B64)
