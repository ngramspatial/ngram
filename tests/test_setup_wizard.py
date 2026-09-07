from __future__ import annotations

from pathlib import Path

import ngram.setup_wizard as setup_wizard


def test_default_setup_menu_routes_to_hosted_without_hybrid(monkeypatch, tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("", encoding="utf-8")
    calls: list[str] = []
    monkeypatch.setattr(setup_wizard, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(setup_wizard, "_dotenv_path", lambda: env_path)
    monkeypatch.setattr(setup_wizard.click, "prompt", lambda *args, **kwargs: "hosted")
    monkeypatch.setattr(
        setup_wizard, "_run_hosted_flow", lambda path: calls.append(f"hosted:{path.name}")
    )
    monkeypatch.setattr(
        setup_wizard,
        "_run_local_flow",
        lambda path: calls.append(f"local:{path.name}"),
    )
    monkeypatch.setattr(setup_wizard, "_entity_section", lambda: None)

    setup_wizard.run_setup_wizard(profile="ask")

    assert calls == ["hosted:.env"]


def test_venice_hosted_flow_writes_local_no_tunnel_profile(monkeypatch, tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("", encoding="utf-8")
    monkeypatch.setattr(setup_wizard.click, "prompt", lambda *args, **kwargs: "venice")
    monkeypatch.setattr(setup_wizard.click, "confirm", lambda *args, **kwargs: False)
    monkeypatch.setattr(setup_wizard, "_activate_process_environment", lambda updates: None)
    monkeypatch.setattr(
        setup_wizard,
        "_hosted_prompt",
        lambda label, default="": default,
    )
    monkeypatch.setattr(setup_wizard, "_prompt_secret", lambda prompt: "venice-test-key")

    setup_wizard._run_hosted_flow(env_path)

    text = env_path.read_text(encoding="utf-8")
    assert "NGRAM_DEPLOYMENT_MODE=local" in text
    assert "NGRAM_INFERENCE_PROVIDER=venice" in text
    assert "NGRAM_INFERENCE_BASE_URL=" in text
    assert "NGRAM_INFERENCE_MODEL=qwen3-235b-a22b-instruct-2507" in text
    assert "NGRAM_EMBEDDING_MODEL=text-embedding-bge-m3" in text
    assert "NGRAM_EMBEDDING_DIMENSIONS=768" in text
    assert "VENICE_API_KEY=venice-test-key" in text
    assert "CF_ACCESS_" not in text
    assert "RAILWAY" not in text
