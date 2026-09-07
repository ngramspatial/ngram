from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from ngram.studio import create_entity_factory_app

_LOCAL_MUTATION = {"X-Ngram-Studio": "1"}


def test_factory_api_creates_validates_and_plans_without_model(tmp_path: Path) -> None:
    app = create_entity_factory_app(tmp_path / "entities")
    with TestClient(app) as client:
        empty = client.get("/api/factory")
        assert empty.status_code == 200
        assert empty.json()["model_loaded"] is False
        assert empty.json()["provider_contacted"] is False

        blocked = client.post("/api/factory/entities", json={"name": "Canary"})
        assert blocked.status_code == 403

        created = client.post(
            "/api/factory/entities",
            headers=_LOCAL_MUTATION,
            json={
                "name": "Canary",
                "archetype": "sardonic",
                "model": "fixture-model",
                "surface": "telegram",
            },
        )
        assert created.status_code == 200, created.text
        assert created.json()["validation"]["entity_key"] == "canary"
        assert created.json()["model_loaded"] is False

        manifest = client.get("/api/factory/entities/canary/manifest")
        assert manifest.status_code == 200
        assert manifest.json()["model"]["deliberate"] == "fixture-model"

        plan = client.get("/api/factory/entities/canary/plan")
        assert plan.status_code == 200
        assert plan.json()["project"]["name"] == "ngram-canary"
        assert plan.json()["operator_inputs"]["required_secrets"][
            "CANARY_TELEGRAM_TOKEN"
        ] == ["canary-worker"]

        listed = client.get("/api/factory").json()["entities"]
        assert len(listed) == 1
        assert listed[0]["ok"] is True


def test_factory_api_rejects_unknown_fields_and_unsafe_keys(tmp_path: Path) -> None:
    app = create_entity_factory_app(tmp_path / "entities")
    with TestClient(app) as client:
        unknown = client.post(
            "/api/factory/entities",
            headers=_LOCAL_MUTATION,
            json={"name": "Canary", "token": "do-not-store"},
        )
        assert unknown.status_code == 409
        assert "unknown creation fields" in unknown.json()["detail"]

        traversal = client.get("/api/factory/entities/../rook/manifest")
        assert traversal.status_code in {404, 409}
