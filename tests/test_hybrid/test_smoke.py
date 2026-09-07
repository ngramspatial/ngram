"""
Hybrid-oriented smoke tests (no real Railway or Cloudflare).

Exercises a **simulated** body + inference path:
- Stub inference provider (stands in for tunnel + gateway + local model).
- SQLite + local disk attachment store (same code paths as hybrid; production hybrid swaps Postgres + S3).

Also covers **gateway-unavailable** behavior when model checks raise (same code path as unreachable remote gateway).
"""

from __future__ import annotations

import base64
from pathlib import Path

import pytest

from ngram.config import HarnessConfig, entity_from_dict
from ngram.entity import Entity
from ngram.models import Input
from tests.support.stub_inference import StubInferenceProvider, UnreachableInferenceProvider

PNG_1X1_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def _entity_config(tmp_path, *, hybrid: bool = False):
    h = HarnessConfig()
    h.memory.database_path = str(tmp_path / "hybrid" / "memory.db")
    h.attachments.local_dir = str(tmp_path / "hybrid_attachments")
    if hybrid:
        h.deployment.mode = "hybrid_railway"
        h.inference.provider = "remote_gateway"
        h.inference.base_url = "http://127.0.0.1:9"
    data = {
        "name": "HybridSmoke",
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
async def test_hybrid_smoke_body_inference_memory_attachment(tmp_path, monkeypatch):
    """Body (Entity) + stub brain + persist image + episodic row cites storage refs."""
    monkeypatch.setattr(
        "ngram.entity.build_inference_provider",
        lambda _h: StubInferenceProvider(),
    )
    ec = _entity_config(tmp_path, hybrid=False)
    ent = Entity(ec)
    assert ent.attachments is not None
    text = "x" * 120
    inp = Input(
        text=text,
        person_id="user_smoke",
        person_name="Smoke",
        channel="100",
        platform="telegram",
        images=[{"base64": PNG_1X1_B64, "mime": "image/png"}],
    )
    try:
        reply, _needs = await ent.perceive(inp, record_user_message=True, reply_platform=None)
        assert "Stub" in reply or "stub" in reply.lower()
        att_dir = Path(ec.harness.attachments.local_dir).expanduser()
        blobs = list(att_dir.glob("*.bin"))
        assert blobs, "expected attachment blob on disk after perceive"
        ref = str(blobs[0])
        loaded = await ent.read_stored_attachment(ref)
        assert loaded == base64.standard_b64decode(PNG_1X1_B64)

        async with ent.store.session() as conn:
            cur = await conn.execute(
                "SELECT raw_context FROM episodes ORDER BY timestamp DESC LIMIT 1"
            )
            row = await cur.fetchone()
        assert row is not None
        assert "attachment_storage" in (row[0] or "")
    finally:
        await ent.shutdown()


@pytest.mark.asyncio
async def test_gateway_unavailable_graceful_message(tmp_path, monkeypatch):
    """When ensure_models / inference is unreachable, perceive returns a safe user-visible line."""
    monkeypatch.setattr(
        "ngram.entity.build_inference_provider",
        lambda _h: UnreachableInferenceProvider(),
    )
    ec = _entity_config(tmp_path, hybrid=True)
    ent = Entity(ec)
    try:
        inp = Input(
            text="Hello there friend",
            person_id="u",
            person_name="U",
            channel="1",
            platform="discord",
        )
        reply, _ = await ent.perceive(inp, reply_platform=None)
        low = reply.lower()
        assert "inference" in low or "sleep" in low or "dream" in low or "reach" in low
        assert ent.dormant is True
    finally:
        await ent.shutdown()
