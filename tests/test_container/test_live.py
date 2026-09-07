from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path

import pytest
from click.testing import CliRunner

from ngram.config import HarnessConfig, load_live_container_config
from ngram.container import ContainerError, LiveContainerMount, verify_container
from ngram.container.migration import migrate_legacy_entity
from ngram.main import cli
from tests.support.stub_inference import StubInferenceProvider

from .test_migration import _legacy_entity


def _open_container(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    legacy, entity_yaml = _legacy_entity(tmp_path)
    root = migrate_legacy_entity(
        legacy,
        entity_yaml,
        tmp_path / "aya.ngram",
        runtime_version="test",
    )
    monkeypatch.setenv("NGRAM_RUNTIME_CACHE_DIR", str(tmp_path / "runtime-cache"))
    config = load_live_container_config(root, HarnessConfig())
    return root, config


def _lineage(root: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in (root / "lineage" / "chain.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


@pytest.mark.asyncio
async def test_live_mount_hydrates_checkpoints_and_withdraws_cleanly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, config = _open_container(tmp_path, monkeypatch)
    mount = LiveContainerMount(config)

    mount.prepare()
    cache = Path(config.runtime_cache_dir)
    assert (cache / "memory.db").is_file()
    assert (cache.parent / "dirty.json").is_file()
    assert _lineage(root)[-1]["type"] == "runtime_mounted"

    (cache / "knowledge.md").write_text("# Knowledge\n\nLearned while mounted.\n", encoding="utf-8")
    with sqlite3.connect(cache / "memory.db") as database:
        database.execute(
            "INSERT INTO beliefs VALUES (?, ?, ?, ?)",
            ("b-live", "Caches are derived", 1.0, "offline-test"),
        )

    await mount.checkpoint("offline_state_changed", {"source": "test-stub"})
    assert "Learned while mounted" in (
        root / "autobiography" / "knowledge.md"
    ).read_text(encoding="utf-8")
    assert "Caches are derived" in (
        root / "autobiography" / "beliefs.md"
    ).read_text(encoding="utf-8")
    assert verify_container(root).ok

    await mount.close()
    assert not (cache.parent / "dirty.json").exists()
    assert not (cache.parent / "mount.lock").exists()
    assert _lineage(root)[-1]["type"] == "runtime_withdrawn"
    assert verify_container(root).ok


@pytest.mark.asyncio
async def test_entity_lifecycle_uses_stub_provider_and_checkpoints_container(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, config = _open_container(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "ngram.entity.build_inference_provider",
        lambda _harness: StubInferenceProvider(),
    )
    from ngram.entity import Entity

    entity = Entity(config)
    Path(config.journal_path()).write_text("# Journal\n\nOffline lifecycle test.\n", encoding="utf-8")
    await entity.sync_live_container("offline_entity_checkpoint", {"model_called": False})
    await entity.shutdown()

    assert "Offline lifecycle test" in (
        root / "autobiography" / "journal.md"
    ).read_text(encoding="utf-8")
    assert _lineage(root)[-2]["type"] == "offline_entity_checkpoint"
    assert _lineage(root)[-2]["payload"]["model_called"] is False
    assert _lineage(root)[-1]["type"] == "runtime_withdrawn"
    assert verify_container(root).ok


def test_mount_refuses_a_second_local_process_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _root, config = _open_container(tmp_path, monkeypatch)
    first = LiveContainerMount(config)
    first.prepare()
    try:
        with pytest.raises(ContainerError, match="already mounted"):
            LiveContainerMount(config).prepare()
    finally:
        asyncio.run(first.close())


def test_cli_recover_reconciles_dirty_cache_without_inference(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, config = _open_container(tmp_path, monkeypatch)
    mount = LiveContainerMount(config)
    mount.prepare()
    Path(config.knowledge_path()).write_text(
        "# Knowledge\n\nRecovered after interruption.\n",
        encoding="utf-8",
    )
    mount.abort()

    result = CliRunner().invoke(cli, ["recover", str(root)])

    assert result.exit_code == 0, result.output
    assert "Recovered interrupted container" in result.output
    assert "Recovered after interruption" in (
        root / "autobiography" / "knowledge.md"
    ).read_text(encoding="utf-8")
    assert _lineage(root)[-1]["type"] == "crash_recovered"
    assert not (Path(config.runtime_cache_dir).parent / "dirty.json").exists()
    assert verify_container(root).ok


def test_recovery_discards_only_checkpoint_temporary_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, config = _open_container(tmp_path, monkeypatch)
    mount = LiveContainerMount(config)
    mount.prepare()
    Path(config.journal_path()).write_text(
        "# Journal\n\nState retained in the cache.\n",
        encoding="utf-8",
    )
    mount.abort()
    (root / "manifest.json.tmp").write_text("incomplete", encoding="utf-8")
    (root / "agency" / "records.json.tmp").write_text("incomplete", encoding="utf-8")

    result = CliRunner().invoke(cli, ["recover", str(root)])

    assert result.exit_code == 0, result.output
    assert not (root / "manifest.json.tmp").exists()
    assert not (root / "agency" / "records.json.tmp").exists()
    assert "State retained in the cache" in (
        root / "autobiography" / "journal.md"
    ).read_text(encoding="utf-8")
    assert verify_container(root).ok


def test_recovery_refuses_changes_outside_runtime_managed_domains(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, config = _open_container(tmp_path, monkeypatch)
    mount = LiveContainerMount(config)
    mount.prepare()
    mount.abort()
    (root / "world" / "anchors.json").write_text('{"tampered":true}\n', encoding="utf-8")

    result = CliRunner().invoke(cli, ["recover", str(root)])

    assert result.exit_code != 0
    assert "outside runtime-managed paths" in result.output
    assert (Path(config.runtime_cache_dir).parent / "dirty.json").is_file()


def test_knowledge_editor_mounts_and_checkpoints_without_model(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _config = _open_container(tmp_path, monkeypatch)
    monkeypatch.setenv("EDITOR", "offline-editor")

    def edit_file(arguments, *, check):
        assert check is False
        path = Path(arguments[-1])
        path.write_text("# Knowledge\n\nEdited through the CLI.\n", encoding="utf-8")

    monkeypatch.setattr("ngram.main.subprocess.run", edit_file)

    result = CliRunner().invoke(cli, ["knowledge", str(root)])

    assert result.exit_code == 0, result.output
    assert "Edited through the CLI" in (
        root / "autobiography" / "knowledge.md"
    ).read_text(encoding="utf-8")
    assert _lineage(root)[-1]["type"] == "runtime_withdrawn"
    assert verify_container(root).ok


def test_export_of_open_container_uses_local_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, config = _open_container(tmp_path, monkeypatch)
    mount = LiveContainerMount(config)
    mount.prepare()
    try:
        blocked = CliRunner().invoke(cli, ["export", str(root), str(tmp_path / "blocked.ngram")])
        assert blocked.exit_code != 0
        assert "already mounted" in blocked.output
    finally:
        asyncio.run(mount.close())

    exported = CliRunner().invoke(cli, ["export", str(root), str(tmp_path / "copy.ngram")])
    assert exported.exit_code == 0, exported.output
    assert (tmp_path / "copy.ngram").is_file()
