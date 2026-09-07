from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from ngram.container import (
    ContainerError,
    pack_archive,
    restore_artifact,
    restore_legacy_entity,
)
from ngram.container.format import finalize_manifest, load_manifest, verify_container
from ngram.container.migration import migrate_legacy_entity
from ngram.governance import ChangeProtocol


class FakeConfig:
    def __init__(self, root: Path) -> None:
        self.name = "Aya"
        self.root = root
        self.raw = {
            "name": "Aya",
            "personality": {
                "core_traits": {"curiosity": 0.8},
                "behavioral_patterns": ["asks careful questions"],
                "voice": {"sentence_style": "measured"},
                "backstory": "Aya remembers becoming herself.",
            },
            "drives": {"curiosity_topics": ["continuity"]},
            "cognition": {"deliberate_model": "model-b", "temperature": 0.7},
            "presence": {"platforms": [{"type": "telegram", "token": "secret"}]},
            "mcp_servers": {"private": {"env": {"ACCESS_TOKEN": "secret"}}},
        }
        self.harness = SimpleNamespace(
            inference=SimpleNamespace(provider="local"),
            attachments=SimpleNamespace(
                backend="local_disk",
                local_dir=str(root / "attachments"),
            ),
        )

    def execution_workspace_dir(self) -> str:
        return str(self.root)

    def db_path(self) -> str:
        return str(self.root / "memory.db")

    def database_url(self) -> str:
        return ""

    def skills_dir(self) -> str:
        return str(self.root / "skills")

    def projects_path(self) -> str:
        return str(self.root / "projects.json")

    def relationships_dir(self) -> str:
        return str(self.root / "relationships")

    def knowledge_path(self) -> str:
        return str(self.root / "knowledge.md")

    def journal_path(self) -> str:
        return str(self.root / "journal.md")

    def autonomy_transcript_path(self) -> str:
        return str(self.root / "autonomy.md")

    def soma_dir(self) -> str:
        return str(self.root / "soma")

    def effective_deliberate_model(self) -> str:
        return "model-b"

    def _merged_tools(self) -> dict:
        return {"web": {"enabled": True}, "service": {"api_key": "secret"}}


def _legacy_entity(tmp_path: Path) -> tuple[FakeConfig, Path]:
    root = tmp_path / "legacy"
    root.mkdir()
    cfg = FakeConfig(root)
    entity_yaml = tmp_path / "aya.yaml"
    entity_yaml.write_text("name: Aya\n", encoding="utf-8")
    (root / "knowledge.md").write_text("# Knowledge\n\nA durable fact.\n", encoding="utf-8")
    (root / "relationships").mkdir()
    (root / "relationships" / "darrius.md").write_text("# Darrius\n", encoding="utf-8")
    (root / "skills").mkdir()
    (root / "skills" / "observe.md").write_text("# Observe\n", encoding="utf-8")
    (root / "attachments").mkdir()
    (root / "attachments" / "photo.bin").write_bytes(b"photo")
    (root / "soma").mkdir()
    (root / "soma" / "soma-state.json").write_text(
        json.dumps(
            {
                "values": {"curiosity": 72.0},
                "initial": {"curiosity": 60.0},
                "ordered_names": ["curiosity"],
                "saved_at": 100.0,
                "somatic_markers": {"person:darrius": {"curiosity": 2.0}},
                "noise_fragments": [{"text": "something unfinished", "salience": "reflective"}],
            }
        ),
        encoding="utf-8",
    )

    db = sqlite3.connect(root / "memory.db")
    db.executescript(
        """
        CREATE TABLE episodes (
            id TEXT PRIMARY KEY, timestamp REAL, summary TEXT, self_reflection TEXT,
            embedding BLOB
        );
        CREATE TABLE beliefs (
            id TEXT PRIMARY KEY, content TEXT, confidence REAL, source TEXT
        );
        CREATE TABLE entity_state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE automations (id TEXT PRIMARY KEY, name TEXT);
        """
    )
    db.execute(
        "INSERT INTO episodes VALUES (?, ?, ?, ?, ?)",
        ("ep-1", 10.0, "A remembered conversation", "It mattered.", b"embedding"),
    )
    db.execute(
        "INSERT INTO beliefs VALUES (?, ?, ?, ?)", ("b-1", "Continuity matters", 0.9, "lived")
    )
    db.execute("INSERT INTO automations VALUES (?, ?)", ("a-1", "Morning reflection"))
    db.commit()
    db.close()
    return cfg, entity_yaml


def test_persisted_identity_rejects_declaration_mismatch(tmp_path: Path) -> None:
    config, entity_yaml = _legacy_entity(tmp_path)
    migrate_legacy_entity(config, entity_yaml, tmp_path / "first.ngram", runtime_version="test")
    config.raw["continuity"] = {
        "entity_id": "ng1:" + "f" * 40,
        "created_at": "2026-09-01T00:00:00Z",
    }

    with pytest.raises(ContainerError, match="does not match persisted identity"):
        migrate_legacy_entity(
            config,
            entity_yaml,
            tmp_path / "second.ngram",
            runtime_version="test",
        )


def test_change_ledger_survives_container_round_trip(tmp_path: Path) -> None:
    config, entity_yaml = _legacy_entity(tmp_path)
    entity_id = "ng1:" + "e" * 40
    config.raw["continuity"] = {
        "entity_id": entity_id,
        "created_at": "2026-09-01T00:00:00Z",
    }
    protocol = ChangeProtocol(config.root / "governance", entity_id)
    proposal = protocol.propose(
        title="Change a schedule",
        reason="The old schedule causes unnecessary wakes.",
        expected_effect="Autonomous wakes happen less often.",
        rollback_plan="Restore the previous interval.",
        changes=[
            {
                "domain": "autonomy",
                "path": "/autonomy/interval",
                "before": 60,
                "after": 120,
            }
        ],
        operator_id="operator-1",
    )
    portable = tmp_path / "portable.ngram"
    migrate_legacy_entity(config, entity_yaml, portable, runtime_version="test")
    restored_yaml = tmp_path / "restored" / "aya.yaml"
    restored_state = tmp_path / "restored" / "state"
    restore_legacy_entity(portable, restored_yaml, restored_state)

    restored = ChangeProtocol(restored_state / "governance", entity_id)
    assert restored.verify()["events"] == 1
    assert restored.inspect(proposal.proposal_id).status == "proposed"


def test_migration_maps_existing_state_into_domains(tmp_path: Path) -> None:
    cfg, entity_yaml = _legacy_entity(tmp_path)
    target = tmp_path / "aya.ngram"
    migrate_legacy_entity(cfg, entity_yaml, target, runtime_version="0.2.0")

    report = verify_container(target)
    assert report.ok, report.errors
    manifest = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["security"]["profile"] == "filesystem-local"
    assert manifest["security"]["encrypted"] is False
    assert manifest["conformance"] == "development-portable-local"
    assert (target / "autobiography" / "episodes" / "ep-1.md").is_file()
    assert "Continuity matters" in (target / "autobiography" / "beliefs.md").read_text(
        encoding="utf-8"
    )
    assert json.loads((target / "soma" / "baselines.json").read_text(encoding="utf-8")) == {
        "curiosity": 60.0
    }
    assert "something unfinished" in (target / "soma" / "noise.log").read_text(encoding="utf-8")
    assert (target / "agency" / "skills" / "observe.md").is_file()
    assert (target / "autobiography" / "attachments" / "photo.bin").read_bytes() == b"photo"

    tools = (target / "agency" / "tools.json").read_text(encoding="utf-8")
    policies = (target / "agency" / "policies.json").read_text(encoding="utf-8")
    assert "secret" not in tools
    assert "secret" not in policies
    assert "<redacted>" in tools
    assert "<redacted>" in policies


def test_migration_reuses_stable_entity_id(tmp_path: Path) -> None:
    cfg, entity_yaml = _legacy_entity(tmp_path)
    first = migrate_legacy_entity(
        cfg, entity_yaml, tmp_path / "first.ngram", runtime_version="0.2.0"
    )
    second = migrate_legacy_entity(
        cfg, entity_yaml, tmp_path / "second.ngram", runtime_version="0.2.0"
    )
    first_id = json.loads((first / "manifest.json").read_text(encoding="utf-8"))["entity_id"]
    second_id = json.loads((second / "manifest.json").read_text(encoding="utf-8"))["entity_id"]
    assert first_id == second_id


def test_migration_refuses_incomplete_postgres_export(tmp_path: Path) -> None:
    cfg, entity_yaml = _legacy_entity(tmp_path)
    cfg.database_url = lambda: "postgresql://example.invalid/ngram"
    with pytest.raises(ContainerError, match="Postgres export"):
        migrate_legacy_entity(cfg, entity_yaml, tmp_path / "aya.ngram", runtime_version="0.2.0")


def test_portable_container_restores_current_runtime_layout(tmp_path: Path) -> None:
    cfg, entity_yaml = _legacy_entity(tmp_path)
    portable = migrate_legacy_entity(
        cfg,
        entity_yaml,
        tmp_path / "portable.ngram",
        runtime_version="0.2.0",
    )
    restored_yaml = tmp_path / "configs" / "aya.yaml"
    restored_state = tmp_path / "restored" / "aya"
    archive = pack_archive(portable, tmp_path / "aya-transport.ngram")

    key = restore_artifact(archive, restored_yaml, restored_state)

    assert key == "aya"
    runtime_config = restored_yaml.read_text(encoding="utf-8")
    assert "name: Aya" in runtime_config
    assert "model-b" not in runtime_config
    assert "secret" not in runtime_config
    assert "pattern_1: asks careful questions" in runtime_config
    assert (restored_state / "knowledge.md").is_file()
    assert (restored_state / "relationships" / "darrius.md").is_file()
    assert (restored_state / "attachments" / "photo.bin").read_bytes() == b"photo"

    db = sqlite3.connect(restored_state / "memory.db")
    episode = db.execute("SELECT summary, embedding FROM episodes WHERE id = 'ep-1'").fetchone()
    soma_row = db.execute("SELECT value FROM entity_state WHERE key = 'soma_state_v2'").fetchone()
    db.close()
    assert episode == ("A remembered conversation", b"embedding")
    assert json.loads(soma_row[0])["somatic_markers"] == {"person:darrius": {"curiosity": 2.0}}


def test_restore_rejects_non_table_sql_even_with_valid_integrity(tmp_path: Path) -> None:
    cfg, entity_yaml = _legacy_entity(tmp_path)
    portable = migrate_legacy_entity(
        cfg,
        entity_yaml,
        tmp_path / "portable.ngram",
        runtime_version="0.2.0",
    )
    records_path = portable / "autobiography" / "records.json"
    records = json.loads(records_path.read_text(encoding="utf-8"))
    records["tables"]["episodes"]["schema"] = "ATTACH DATABASE 'other.db' AS other"
    records_path.write_text(json.dumps(records), encoding="utf-8")
    finalize_manifest(portable, load_manifest(portable))

    with pytest.raises(ContainerError, match="unsafe or invalid"):
        restore_legacy_entity(
            portable,
            tmp_path / "configs" / "aya.yaml",
            tmp_path / "restored" / "aya",
        )
