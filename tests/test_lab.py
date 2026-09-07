from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import click
import pytest

import ngram.config as config
import ngram.lab as lab
from ngram.ngram_ar.setup import ArSetupResult


def test_entity_name_lookup_is_case_insensitive(monkeypatch, tmp_path: Path) -> None:
    entities_dir = tmp_path / "entities"
    entities_dir.mkdir()
    rook_path = entities_dir / "rook.yaml"
    rook_path.write_text("name: Rook\n", encoding="utf-8")
    monkeypatch.setattr(config, "project_configs_dir", lambda: tmp_path)

    assert config._resolve_named_entity_path("Rook") == rook_path


def test_hosted_url_requires_https_except_loopback() -> None:
    assert lab.validate_api_base_url("http://127.0.0.1:8000/v1", required=True) == (
        "http://127.0.0.1:8000/v1"
    )
    assert lab.validate_api_base_url("https://gpu.example/v1/", required=True) == (
        "https://gpu.example/v1"
    )
    with pytest.raises(click.ClickException, match="must use HTTPS"):
        lab.validate_api_base_url("http://gpu.example/v1", required=True)
    with pytest.raises(click.ClickException, match="credentials"):
        lab.validate_api_base_url("https://user:pass@gpu.example/v1", required=True)


def test_secret_rejects_dotenv_injection() -> None:
    with pytest.raises(click.ClickException, match="control character"):
        lab.validate_secret("token\nINJECTED=yes")


def test_setup_writes_secrets_only_to_ignored_env(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path
    shell = root / "ngramAR" / "shells" / "rook"
    shell.mkdir(parents=True)
    (root / "ngramAR" / "package-lock.json").write_text("{}\n", encoding="utf-8")
    (shell / "shell.yaml").write_text("name: Rook\n", encoding="utf-8")
    (shell / ".env").write_text(
        "NGRAM_AR_ENTITY_BRIDGE_TOKEN=" + ("b" * 40) + "\n",
        encoding="utf-8",
    )
    (root / ".env").write_text("OPENAI_API_KEY=throwaway-secret\n", encoding="utf-8")
    monkeypatch.setattr(lab, "repo_root", lambda: root)
    monkeypatch.setattr(lab, "npm_executable", lambda: "npm")
    monkeypatch.setattr(
        lab.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0)
    )
    monkeypatch.setattr(
        lab,
        "run_ar_setup",
        lambda **kwargs: ArSetupResult("Rook", shell, "local", "ws://127.0.0.1:7878/"),
    )
    tracked = {
        key: os.environ.get(key)
        for key in (
            "OPENAI_API_KEY",
            "NGRAM_INFERENCE_PROVIDER",
            "NGRAM_INFERENCE_MODEL",
            "NGRAM_EMBEDDING_MODEL",
            "NGRAM_INFERENCE_BASE_URL",
            "NGRAM_INFERENCE_API_KEY_ENV",
            "NGRAM_INFERENCE_PASS_NUM_CTX",
            "NGRAM_DEPLOYMENT_MODE",
            "NGRAM_AR_HOST",
            "NGRAM_AR_PORT",
            "NGRAM_AR_SURFACE_TOKEN",
        )
    }
    try:
        manifest = lab.setup_lab(
            "Rook",
            provider="openai",
            model="gpt-test",
            embedding_model="",
            base_url="",
            api_key_env="",
            person_id="person-1",
            person_name="Tester",
            shell_path=shell,
            bridge_port=7878,
            surface_port=3000,
            install_node=False,
            check_provider=False,
            assume_yes=True,
        )
    finally:
        for key, value in tracked.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    stored = (root / ".runtime" / "lab.json").read_text(encoding="utf-8")
    assert "throwaway-secret" not in stored
    assert "OPENAI_API_KEY" not in stored
    assert "NGRAM_AR_SURFACE_TOKEN" not in stored
    stored_manifest = json.loads(stored)
    assert stored_manifest["embedding_model"] == "text-embedding-3-small"
    assert stored_manifest["shell_path"] == "ngramAR/shells/rook"
    assert str(root) not in stored
    assert manifest.model == "gpt-test"
    env_text = (root / ".env").read_text(encoding="utf-8")
    assert "NGRAM_INFERENCE_MODEL=gpt-test" in env_text
    assert "NGRAM_EMBEDDING_MODEL=text-embedding-3-small" in env_text
    assert "NGRAM_EMBEDDING_DIMENSIONS=768" in env_text


def test_venice_lab_has_no_tunnel_defaults() -> None:
    assert lab.PROVIDER_DEFAULTS["venice"] == {
        "base_url": "",
        "key_env": "VENICE_API_KEY",
        "model": "qwen3-235b-a22b-instruct-2507",
        "embedding_model": "text-embedding-bge-m3",
    }


def test_lab_inherits_existing_venice_profile(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path
    shell = root / "ngramAR" / "shells" / "nova"
    shell.mkdir(parents=True)
    (root / "ngramAR" / "package-lock.json").write_text("{}\n", encoding="utf-8")
    (shell / "shell.yaml").write_text("name: Nova\n", encoding="utf-8")
    (shell / ".env").write_text(
        "NGRAM_AR_ENTITY_BRIDGE_TOKEN=" + ("b" * 40) + "\n",
        encoding="utf-8",
    )
    (root / ".env").write_text(
        "NGRAM_INFERENCE_PROVIDER=venice\n"
        "NGRAM_INFERENCE_MODEL=venice-chat\n"
        "NGRAM_EMBEDDING_MODEL=text-embedding-bge-m3\n"
        "NGRAM_INFERENCE_API_KEY_ENV=VENICE_API_KEY\n"
        "VENICE_API_KEY=test-key\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(lab, "repo_root", lambda: root)
    monkeypatch.setattr(lab.os, "environ", dict(os.environ))
    monkeypatch.setattr(lab, "npm_executable", lambda: "npm")
    monkeypatch.setattr(
        lab.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0)
    )
    monkeypatch.setattr(
        lab,
        "run_ar_setup",
        lambda **kwargs: ArSetupResult("Nova", shell, "local", "ws://127.0.0.1:7878/"),
    )
    monkeypatch.setattr(lab, "load_entity_config", lambda *args, **kwargs: SimpleNamespace())

    manifest = lab.setup_lab(
        "Nova",
        provider=None,
        model="",
        embedding_model="",
        base_url="",
        api_key_env="",
        person_id="person-1",
        person_name="Tester",
        shell_path=shell,
        bridge_port=7878,
        surface_port=3000,
        install_node=False,
        check_provider=False,
        assume_yes=True,
    )

    assert manifest.provider == "venice"
    assert manifest.model == "venice-chat"
    assert manifest.embedding_model == "text-embedding-bge-m3"


@pytest.mark.asyncio
async def test_provider_probe_rejects_incompatible_memory_width(monkeypatch) -> None:
    class StubProvider:
        closed = False

        async def list_models(self):
            return ["chat"]

        async def embed(self, model, text):
            return [0.0] * 12

        async def close(self):
            self.closed = True

    stub = StubProvider()
    monkeypatch.setattr(lab, "build_inference_provider", lambda *args, **kwargs: stub)
    with pytest.raises(click.ClickException, match="requires 768"):
        await lab._probe_provider(
            provider_name="openai",
            model="chat",
            embedding_model="embed",
            base_url="",
            api_key="secret",
        )
    assert stub.closed is True
