from pathlib import Path

import pytest

from ngram.config import HarnessConfig, entity_from_dict
from ngram.memory.procedural import ProceduralMemoryStore
from ngram.memory.projects import ProjectLedger
from ngram.memory.self_model import SelfModelStore
from tests.support.stub_inference import StubInferenceProvider


def _entity_config(tmp_path):
    h = HarnessConfig()
    h.memory.database_path = str(tmp_path / "memory.db")
    data = {
        "name": "OperatorFoundation",
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
async def test_procedural_memory_can_store_and_query(tmp_path) -> None:
    ec = _entity_config(tmp_path)
    store = ProceduralMemoryStore(ec, StubInferenceProvider())
    await store.upsert_skill(
        "python-debug-loop",
        "When debugging Python, run the failing test first, inspect the traceback, patch the file, and rerun.",
    )
    hits = await store.query("rerun the failing python test", limit=3)
    assert hits
    assert "python-debug-loop" in hits[0]


@pytest.mark.asyncio
async def test_project_ledger_can_create_and_update(tmp_path) -> None:
    ledger = ProjectLedger(Path(tmp_path / "projects.json"))
    row = await ledger.create_project(
        "Railway hardening",
        "Lock execution to Railway and tighten operator behavior.",
        next_steps=["verify logs", "test persistence"],
    )
    assert row.id.startswith("proj_")
    upd = await ledger.update_project(
        row.id,
        status="paused",
        last_activity="waiting on deploy",
    )
    rows = await ledger.list_projects()
    assert rows[0].status == "paused"
    assert upd.last_activity == "waiting on deploy"


@pytest.mark.asyncio
async def test_self_model_store_tracks_tools_and_snapshot(tmp_path) -> None:
    store = SelfModelStore(Path(tmp_path / "self_model.json"))
    await store.record_tool_result("run_command", True, "")
    await store.record_tool_result("search_web", False, "network blocked")
    await store.refresh_snapshot(open_project_count=2, skill_count=3, note="updated after test")
    summary = await store.summary()
    assert "run_command" in summary
    assert "search_web" in summary
    assert "open projects: 2" in summary
    assert "skills: 3" in summary
