import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from ngram.cloud_setup import _ensure_volume, cloud_variables
from ngram.config import HarnessConfig, apply_harness_env_overrides, entity_from_dict, load_entity_config
from ngram.inference.factory import effective_inference_provider_name
from ngram.ngram_ar.onboarding import setup_snapshot
from ngram.ngram_ar.setup import configure_shell


def test_first_run_defaults_to_the_cloud_wizard(monkeypatch, tmp_path):
    import ngram.cloud_setup as cloud
    import ngram.setup_wizard as setup
    env = tmp_path / ".env"
    env.write_text("")
    calls = []
    monkeypatch.setattr(setup, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(setup, "_dotenv_path", lambda: env)
    monkeypatch.setattr(setup.click, "prompt", lambda *args, **kwargs: kwargs["default"])
    monkeypatch.setattr(cloud, "run_cloud_setup", lambda root, path: calls.append((root, path)))
    setup.run_setup_wizard()
    assert calls == [(tmp_path, env)]


def test_cloud_profile_has_hosted_memory_and_a_remote_execution_boundary(monkeypatch):
    values = cloud_variables("nova", "nova-memory", {
        "NGRAM_INFERENCE_PROVIDER": "openai", "NGRAM_INFERENCE_MODEL": "test-model",
        "NGRAM_EMBEDDING_MODEL": "text-embedding-3-small", "NGRAM_EMBEDDING_DIMENSIONS": "768",
    })
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    harness = HarnessConfig()
    apply_harness_env_overrides(harness)
    assert harness.deployment.mode == "cloud"
    assert effective_inference_provider_name(harness) == "openai"
    assert harness.models.embedding == "text-embedding-3-small"
    assert harness.memory.embedding_dimensions == 768
    assert values["DATABASE_URL"] == "${{nova-memory.DATABASE_URL}}"
    assert values["NGRAM_EXECUTION_REQUIRE_RAILWAY"] == "true"
    assert not any("GATEWAY" in key or "CF_ACCESS" in key for key in values)


def test_volume_check_does_not_mistake_another_workers_mount_for_ours():
    class Railway:
        calls = []
        def status(self):
            return {"services": {"edges": [{"node": {"name": "nova-worker", "id": "nova-id"}}]}}
        def run(self, arguments):
            self.calls.append(arguments)
            return json.dumps({"volumes": [{"serviceName": "other-worker", "mountPath": "/app/data"}]})
    railway = Railway()
    _ensure_volume(railway, "nova-worker", "/app/data")
    assert railway.calls[-1] == ["volume", "--service", "nova-id", "add", "--mount-path", "/app/data", "--json"]


def test_existing_volume_is_reused_from_the_railway_json_schema():
    class Railway:
        calls = []
        def status(self):
            return {"services": {"edges": [{"node": {"name": "nova-worker", "id": "nova-id"}}]}}
        def run(self, arguments):
            self.calls.append(arguments)
            return json.dumps({"volumes": [{"serviceName": "nova-worker", "mountPath": "/app/data"}]})
    railway = Railway()
    _ensure_volume(railway, "nova-worker", "/app/data")
    assert railway.calls == [["volume", "list", "--json"]]


def test_deployed_identity_survives_ignored_personal_yaml(monkeypatch):
    monkeypatch.setenv("NGRAM_ENTITY", "nova")
    monkeypatch.setenv("NGRAM_ENTITY_CONFIG_YAML", "name: Nova\npersonality:\n  backstory: Remembers the stars.\n")
    entity = load_entity_config("nova", HarnessConfig())
    assert entity.name == "Nova"
    assert entity.personality.backstory == "Remembers the stars."
    with pytest.raises(FileNotFoundError):
        load_entity_config("unrelated-nonexistent", HarnessConfig())


def test_cli_pairing_publishes_private_reference_for_the_app(tmp_path):
    import yaml
    shell = tmp_path / "ngramAR" / "shells" / "nova"
    shell.mkdir(parents=True)
    (shell / "shell.yaml").write_text("name: Nova\nmodel: default\nbinding:\n  type: ngram_entity\n")
    configure_shell(shell, bridge_url="wss://worker.example/", token="pairing-secret")
    definition = yaml.safe_load((shell / "shell.yaml").read_text())
    connection_id = definition["binding"]["options"]["connectionId"]
    private = tmp_path / "ngramAR" / ".runtime" / "connections" / f"{connection_id}.json"
    assert json.loads(private.read_text())["token"] == "pairing-secret"
    assert "pairing-secret" not in (shell / "shell.yaml").read_text()
    configure_shell(shell, bridge_url="wss://worker.example/", token="replacement")
    assert json.loads(private.read_text())["token"] == "replacement"


@pytest.mark.asyncio
async def test_setup_metadata_reports_real_memory_without_personal_content(monkeypatch):
    class Cursor:
        async def fetchone(self): return (1,)
    class Connection:
        async def execute(self, query): return Cursor()
    class Store:
        @asynccontextmanager
        async def session(self): yield Connection()
    harness = HarnessConfig()
    harness.models.embedding = "nomic-embed-text"
    config = entity_from_dict(harness, {"name": "Nova"})
    monkeypatch.setenv("DATABASE_URL", "postgres://private-secret")
    monkeypatch.setenv("NGRAM_EXECUTION_WORKSPACE_DIR", "/app/data")
    monkeypatch.setenv("RAILWAY_VOLUME_MOUNT_PATH", "/app/data")
    status = await setup_snapshot(SimpleNamespace(config=config, store=Store()))
    assert status["hasMemories"] is True
    assert status["embeddingModel"] == "nomic-embed-text"
    assert status["embeddingDimensions"] == 768
    assert status["memoryStore"] == "postgres"
    assert status["volumeMounted"] is True
    assert "private-secret" not in json.dumps(status)
