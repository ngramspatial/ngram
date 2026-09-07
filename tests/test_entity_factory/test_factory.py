from __future__ import annotations

import json
from pathlib import Path

import yaml
from click.testing import CliRunner

from ngram.config import HarnessConfig
from ngram.entity_factory import (
    FACTORY_SCHEMA_VERSION,
    build_deployment_plan,
    build_entity_manifest,
    create_entity_config,
    entity_status,
    validate_entity,
)
from ngram.main import cli


def _harness() -> HarnessConfig:
    harness = HarnessConfig()
    harness.models.deliberate = "test-model:8b"
    harness.models.reflex = "test-model:8b"
    return harness


def test_factory_create_validate_manifest_and_plan_are_model_free(tmp_path: Path) -> None:
    declaration = create_entity_config(
        "Canary Finch",
        tmp_path,
        archetype="curious",
        surface="telegram",
        harness=_harness(),
    )

    assert declaration == (tmp_path / "canary-finch.yaml").resolve()
    raw = yaml.safe_load(declaration.read_text(encoding="utf-8"))
    assert raw["name"] == "Canary Finch"
    assert raw["continuity"]["entity_id"].startswith("ng1:")
    assert raw["autonomy"]["enabled"] is False
    assert raw["automations"]["enabled"] is False
    assert raw["presence"]["platforms"][0]["token_env"] == "CANARY_FINCH_TELEGRAM_TOKEN"

    report = validate_entity(declaration, harness=_harness())
    assert report.ok, report.errors
    manifest = build_entity_manifest(declaration, harness=_harness())
    assert manifest["schema_version"] == FACTORY_SCHEMA_VERSION
    assert manifest["entity"]["key"] == "canary-finch"
    assert manifest["entity"]["entity_id"] == raw["continuity"]["entity_id"]
    assert manifest["model"]["deliberate"] == "test-model:8b"
    assert manifest["required_secret_env"] == [
        "CANARY_FINCH_TELEGRAM_TOKEN",
        "NGRAM_CF_ACCESS_CLIENT_ID",
        "NGRAM_CF_ACCESS_CLIENT_SECRET",
        "NGRAM_INFERENCE_GATEWAY_TOKEN",
    ]
    encoded = json.dumps(manifest)
    assert "secret-value" not in encoded

    plan = build_deployment_plan(declaration, harness=_harness())
    assert plan["project"] == {"name": "ngram-canary-finch", "isolation_boundary": True}
    assert {row["name"] for row in plan["resources"]} == {
        "canary-finch-api",
        "canary-finch-worker",
        "canary-finch-worker-volume",
        "Postgres",
    }
    assert plan["settings"]["canary-finch-worker"]["DATABASE_URL"] == "${{Postgres.DATABASE_URL}}"
    assert plan["settings"]["canary-finch-worker"]["NGRAM_CANONICAL_LEASE_REQUIRED"] == "true"
    assert "CANARY_FINCH_TELEGRAM_TOKEN" not in plan["settings"]["canary-finch-api"]
    assert plan["operator_inputs"]["required_secrets"]["CANARY_FINCH_TELEGRAM_TOKEN"] == [
        "canary-finch-worker"
    ]
    assert plan["operator_inputs"]["required_secrets"]["NGRAM_CF_ACCESS_CLIENT_ID"] == [
        "canary-finch-worker",
        "canary-finch-api",
    ]

    status = entity_status(declaration, harness=_harness())
    assert status["validation"]["ok"] is True
    assert status["model_loaded"] is False
    assert status["lifecycle"]["provider_state"] == "not_queried"


def test_factory_rejects_literal_secrets_and_duplicate_surfaces(tmp_path: Path) -> None:
    declaration = create_entity_config(
        "Unsafe",
        tmp_path,
        archetype="gentle",
        harness=_harness(),
    )
    raw = yaml.safe_load(declaration.read_text(encoding="utf-8"))
    raw["presence"]["platforms"] = [
        {"type": "telegram", "token": "secret-value"},
        {"type": "telegram", "token_env": "UNSAFE_TELEGRAM_TOKEN"},
    ]
    declaration.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    report = validate_entity(declaration, harness=_harness())
    assert report.ok is False
    assert any("literal credentials" in error for error in report.errors)
    assert any("duplicate presence" in error for error in report.errors)


def test_factory_cli_create_validate_manifest_plan_and_status(tmp_path: Path) -> None:
    runner = CliRunner()
    declaration = tmp_path / "ember.yaml"
    created = runner.invoke(
        cli,
        [
            "entity",
            "create",
            "Ember",
            "--destination",
            str(declaration),
            "--archetype",
            "guardian",
            "--model",
            "fixture-model",
        ],
    )
    assert created.exit_code == 0, created.output
    assert declaration.is_file()

    validated = runner.invoke(cli, ["entity", "validate", str(declaration), "--json"])
    assert validated.exit_code == 0, validated.output
    assert json.loads(validated.output)["ok"] is True

    manifested = runner.invoke(cli, ["entity", "manifest", str(declaration)])
    assert manifested.exit_code == 0, manifested.output
    assert json.loads(manifested.output)["entity"]["key"] == "ember"

    planned = runner.invoke(cli, ["entity", "plan", str(declaration)])
    assert planned.exit_code == 0, planned.output
    assert json.loads(planned.output)["project"]["name"] == "ngram-ember"

    inspected = runner.invoke(cli, ["entity", "status", str(declaration)])
    assert inspected.exit_code == 0, inspected.output
    assert json.loads(inspected.output)["model_loaded"] is False


def test_factory_plans_are_isolated_without_running_entities(tmp_path: Path) -> None:
    first = create_entity_config("First", tmp_path / "first.yaml", harness=_harness())
    second = create_entity_config("Second", tmp_path / "second.yaml", harness=_harness())

    first_plan = build_deployment_plan(first, harness=_harness())
    second_plan = build_deployment_plan(second, harness=_harness())
    assert first_plan["project"]["name"] != second_plan["project"]["name"]
    first_resources = {row["name"] for row in first_plan["resources"]}
    second_resources = {row["name"] for row in second_plan["resources"]}
    assert first_resources.isdisjoint(second_resources - {"Postgres"})
    assert all("second" not in json.dumps(row) for row in first_plan["resources"])
    assert all("first" not in json.dumps(row) for row in second_plan["resources"])
