from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ngram.config import HarnessConfig, load_live_container_config
from ngram.container import ContainerError, LiveContainerMount, verify_artifact, verify_container
from ngram.container.migration import migrate_legacy_entity
from ngram.studio import create_studio_app
from tests.test_container.test_migration import _legacy_entity

_LOCAL_MUTATION = {"X-Ngram-Studio": "1"}


def _studio_container(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    legacy, entity_yaml = _legacy_entity(tmp_path)
    legacy.harness.soma = {
        "bars": {
            "variables": [
                {
                    "name": "curiosity",
                    "initial": 60,
                    "decay_rate": -10,
                    "floor": 0,
                    "ceiling": 100,
                }
            ]
        }
    }
    root = migrate_legacy_entity(
        legacy,
        entity_yaml,
        tmp_path / "aya.ngram",
        runtime_version="studio-test",
    )
    monkeypatch.setenv("NGRAM_RUNTIME_CACHE_DIR", str(tmp_path / "runtime-cache"))
    return root


def test_studio_overview_is_writable_and_model_free(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _studio_container(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "ngram.inference.build_inference_provider",
        lambda _harness: pytest.fail("Studio must not initialize inference"),
    )

    with TestClient(create_studio_app(root)) as client:
        home = client.get("/")
        script = client.get("/studio.js")
        styles = client.get("/studio.css")
        assert home.status_code == script.status_code == styles.status_code == 200
        assert "ngram Studio" in home.text
        assert 'id="soma-chart"' in home.text
        assert 'id="recovery-banner"' in home.text
        assert "@media (max-width: 720px)" in styles.text
        assert "boot();" in script.text

        response = client.get("/api/overview")
        assert response.status_code == 200
        overview = response.json()
        assert overview["manifest"]["display_name"] == "Aya"
        assert overview["manifest"]["format_profile"] == "portable-local-v1"
        assert overview["verification"]["ok"] is True
        assert overview["writable"] is True
        assert overview["lock"]["status"] == "studio"
        assert len(overview["domains"]) == 7


def test_studio_edits_bounded_settings_and_advances_lineage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _studio_container(tmp_path, monkeypatch)
    before = (root / "lineage" / "chain.jsonl").read_text(encoding="utf-8").splitlines()

    with TestClient(create_studio_app(root)) as client:
        settings = client.get("/api/settings").json()["settings"]
        settings["preferences"]["studio_note"] = "Edited locally"
        settings["policies"]["studio_note"] = "Edited locally"
        settings["schedule"]["studio_note"] = "Edited locally"
        settings["embodiment"]["rig"] = "studio-test-rig"
        for section in ("preferences", "policies", "schedule", "embodiment"):
            response = client.put(
                f"/api/settings/{section}",
                json=settings[section],
                headers=_LOCAL_MUTATION,
            )
            assert response.status_code == 200, response.text

        rejected = client.put(
            "/api/settings/policies",
            json={"api_key": "must-not-enter-the-container"},
            headers=_LOCAL_MUTATION,
        )
        assert rejected.status_code == 409
        assert "secret values" in rejected.json()["detail"]

    saved = json.loads((root / "agency" / "preferences.json").read_text(encoding="utf-8"))
    after = (root / "lineage" / "chain.jsonl").read_text(encoding="utf-8").splitlines()
    assert saved["studio_note"] == "Edited locally"
    assert len(after) == len(before) + 4
    assert json.loads(after[-1])["type"] == "studio_settings_updated"
    reloaded = load_live_container_config(root, HarnessConfig())
    assert reloaded.raw["embodiment"]["rig"] == "studio-test-rig"
    assert verify_container(root).ok


def test_studio_soma_projection_is_read_only_and_uses_container_dynamics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _studio_container(tmp_path, monkeypatch)
    before = (root / "soma" / "state.json").read_bytes()

    with TestClient(create_studio_app(root)) as client:
        response = client.get("/api/soma/simulate", params={"hours": 24, "steps": 12})
        assert response.status_code == 200
        simulation = response.json()
        assert simulation["source"] == "container-dynamics"
        assert len(simulation["timeline"]) == 13
        assert simulation["timeline"][0]["values"]["curiosity"] == 72.0
        assert simulation["timeline"][-1]["values"]["curiosity"] < 72.0
        assert simulation["timeline"][-1]["values"]["curiosity"] > 60.0

    assert (root / "soma" / "state.json").read_bytes() == before


def test_studio_file_inspection_blocks_traversal_and_exports_verified_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _studio_container(tmp_path, monkeypatch)

    with TestClient(create_studio_app(root)) as client:
        identity = client.get("/api/file", params={"path": "identity/identity.md"})
        assert identity.status_code == 200
        assert "# Aya" in identity.json()["content"]

        traversal = client.get("/api/file", params={"path": "../.env"})
        assert traversal.status_code == 409

        exported = client.get("/api/export")
        assert exported.status_code == 200
        archive = tmp_path / "studio-export.ngram"
        archive.write_bytes(exported.content)

    assert verify_artifact(archive).ok


def test_studio_guides_and_completes_interrupted_runtime_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _studio_container(tmp_path, monkeypatch)
    config = load_live_container_config(root, HarnessConfig())
    mount = LiveContainerMount(config)
    mount.prepare()
    Path(config.knowledge_path()).write_text(
        "# Knowledge\n\nRecovered through Studio.\n",
        encoding="utf-8",
    )
    mount.abort()

    with TestClient(create_studio_app(root)) as client:
        overview = client.get("/api/overview").json()
        assert overview["dirty"] is True
        assert overview["writable"] is False

        recovered = client.post("/api/recover", headers=_LOCAL_MUTATION)
        assert recovered.status_code == 200, recovered.text
        assert recovered.json()["dirty"] is False
        assert recovered.json()["writable"] is True

    assert "Recovered through Studio" in (
        root / "autobiography" / "knowledge.md"
    ).read_text(encoding="utf-8")
    assert verify_container(root).ok


def test_studio_rolls_back_a_failed_canonical_edit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _studio_container(tmp_path, monkeypatch)
    original = (root / "agency" / "preferences.json").read_bytes()

    def fail_after_write(container_root, relative_path, value, **_kwargs):
        (container_root / relative_path).write_text(json.dumps(value), encoding="utf-8")
        raise ContainerError("simulated interrupted edit")

    monkeypatch.setattr("ngram.studio.service.commit_canonical_json_update", fail_after_write)
    with TestClient(create_studio_app(root)) as client:
        response = client.put(
            "/api/settings/preferences",
            json={"changed": True},
            headers=_LOCAL_MUTATION,
        )
        assert response.status_code == 409
        assert "simulated interrupted edit" in response.json()["detail"]

    assert (root / "agency" / "preferences.json").read_bytes() == original
    assert verify_container(root).ok


def test_studio_rejects_cross_site_mutations_and_untrusted_hosts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _studio_container(tmp_path, monkeypatch)

    with TestClient(create_studio_app(root)) as client:
        missing_header = client.put(
            "/api/settings/preferences",
            json={"changed": True},
        )
        assert missing_header.status_code == 403

        hostile_host = client.get("/api/overview", headers={"Host": "attacker.invalid"})
        assert hostile_host.status_code == 400

        overview = client.get("/api/overview")
        assert overview.headers["cache-control"] == "no-store"
        assert overview.headers["x-frame-options"] == "DENY"
        assert "default-src 'self'" in overview.headers["content-security-policy"]
