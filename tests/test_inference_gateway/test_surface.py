"""Gateway HTTP surface: allowlist and 404 behavior (requires ``pip install 'ngram[gateway]'``)."""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from ngram.inference_gateway.app import CloudflareAccessVerifier, create_app  # noqa: E402


@pytest.fixture
def client(monkeypatch) -> TestClient:
    monkeypatch.setenv("INFERENCE_GATEWAY_TOKEN", "test-secret-token")
    monkeypatch.delenv("INFERENCE_GATEWAY_REQUIRE_CF_ACCESS", raising=False)
    with TestClient(create_app()) as c:
        yield c


def test_unknown_path_json_404(client: TestClient) -> None:
    r = client.get("/")
    assert r.status_code == 404
    body = r.json()
    assert body["error"] == "not_found"
    assert "inference" in body["message"].lower()


def test_openapi_ui_not_exposed(client: TestClient) -> None:
    for path in ("/docs", "/redoc", "/openapi.json"):
        r = client.get(path)
        assert r.status_code == 404


def test_health_requires_bearer(client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code == 401


def test_health_returns_json_with_token(monkeypatch) -> None:
    monkeypatch.setenv("INFERENCE_GATEWAY_TOKEN", "test-secret-token")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:9")
    with TestClient(create_app()) as tc:
        r = tc.get("/health", headers={"Authorization": "Bearer test-secret-token"})
    assert r.status_code == 200
    data = r.json()
    assert "ok" in data


def test_authenticated_requests_are_rate_limited(monkeypatch) -> None:
    monkeypatch.setenv("INFERENCE_GATEWAY_TOKEN", "test-secret-token")
    monkeypatch.setenv("INFERENCE_GATEWAY_RATE_LIMIT_AUTHENTICATED", "2")
    monkeypatch.setenv("INFERENCE_GATEWAY_RATE_LIMIT_GLOBAL", "20")
    headers = {"Authorization": "Bearer test-secret-token"}
    with TestClient(create_app()) as tc:
        assert tc.head("/health", headers=headers).status_code == 200
        assert tc.head("/health", headers=headers).status_code == 200
        blocked = tc.head("/health", headers=headers)
    assert blocked.status_code == 429
    assert int(blocked.headers["Retry-After"]) >= 1


def test_unauthenticated_requests_have_stricter_limit(monkeypatch) -> None:
    monkeypatch.setenv("INFERENCE_GATEWAY_TOKEN", "test-secret-token")
    monkeypatch.setenv("INFERENCE_GATEWAY_RATE_LIMIT_UNAUTHENTICATED", "2")
    with TestClient(create_app()) as tc:
        assert tc.get("/health").status_code == 401
        assert tc.get("/health").status_code == 401
        assert tc.get("/health").status_code == 429


def test_cloudflare_edge_request_requires_valid_access_jwt(monkeypatch) -> None:
    monkeypatch.setenv("INFERENCE_GATEWAY_TOKEN", "test-secret-token")
    monkeypatch.setenv("INFERENCE_GATEWAY_REQUIRE_CF_ACCESS", "1")
    monkeypatch.setenv("CF_ACCESS_TEAM_DOMAIN", "https://unit-test.cloudflareaccess.com")
    monkeypatch.setenv("CF_ACCESS_AUD", "unit-test-audience")

    async def fake_verify(_self, token: str) -> None:
        if token != "valid-access-jwt":
            raise ValueError("invalid")

    monkeypatch.setattr(CloudflareAccessVerifier, "verify", fake_verify)
    bearer = {"Authorization": "Bearer test-secret-token"}
    with TestClient(create_app(), client=("127.0.0.1", 50000)) as tc:
        # Direct loopback probes do not traverse Access.
        assert tc.head("/health", headers=bearer).status_code == 200
        assert tc.head("/health", headers={**bearer, "CF-Ray": "test"}).status_code == 403
        accepted = tc.head(
            "/health",
            headers={
                **bearer,
                "CF-Ray": "test",
                "Cf-Access-Jwt-Assertion": "valid-access-jwt",
            },
        )
    assert accepted.status_code == 200


def test_non_loopback_request_cannot_bypass_cloudflare_access(monkeypatch) -> None:
    monkeypatch.setenv("INFERENCE_GATEWAY_TOKEN", "test-secret-token")
    monkeypatch.setenv("INFERENCE_GATEWAY_REQUIRE_CF_ACCESS", "1")
    monkeypatch.setenv("CF_ACCESS_TEAM_DOMAIN", "https://unit-test.cloudflareaccess.com")
    monkeypatch.setenv("CF_ACCESS_AUD", "unit-test-audience")

    async def fake_verify(_self, token: str) -> None:
        if token != "valid-access-jwt":
            raise ValueError("invalid")

    monkeypatch.setattr(CloudflareAccessVerifier, "verify", fake_verify)
    bearer = {"Authorization": "Bearer test-secret-token"}
    with TestClient(create_app(), client=("192.0.2.10", 50000)) as tc:
        assert tc.head("/health", headers=bearer).status_code == 403
        accepted = tc.head(
            "/health",
            headers={**bearer, "Cf-Access-Jwt-Assertion": "valid-access-jwt"},
        )
    assert accepted.status_code == 200


def test_cloudflare_access_configuration_fails_closed(monkeypatch) -> None:
    monkeypatch.setenv("INFERENCE_GATEWAY_REQUIRE_CF_ACCESS", "true")
    monkeypatch.delenv("CF_ACCESS_TEAM_DOMAIN", raising=False)
    monkeypatch.delenv("CF_ACCESS_AUD", raising=False)
    with pytest.raises(RuntimeError, match="CF_ACCESS_TEAM_DOMAIN"):
        create_app()


def test_cloudflare_access_verifier_checks_signature_issuer_and_audience() -> None:
    jwt = pytest.importorskip("jwt")
    rsa = pytest.importorskip("cryptography.hazmat.primitives.asymmetric.rsa")
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    verifier = CloudflareAccessVerifier(
        team_domain="https://unit-test.cloudflareaccess.com",
        audience="expected-audience",
    )
    verifier._jwks = SimpleNamespace(  # noqa: SLF001
        get_signing_key_from_jwt=lambda _token: SimpleNamespace(key=private_key.public_key())
    )
    now = int(time.time())
    token = jwt.encode(
        {
            "iss": "https://unit-test.cloudflareaccess.com",
            "aud": "expected-audience",
            "iat": now,
            "exp": now + 300,
        },
        private_key,
        algorithm="RS256",
        headers={"kid": "unit-test"},
    )
    verifier._verify_sync(token)  # noqa: SLF001

    wrong_audience = jwt.encode(
        {
            "iss": "https://unit-test.cloudflareaccess.com",
            "aud": "wrong-audience",
            "iat": now,
            "exp": now + 300,
        },
        private_key,
        algorithm="RS256",
        headers={"kid": "unit-test"},
    )
    with pytest.raises(jwt.InvalidAudienceError):
        verifier._verify_sync(wrong_audience)  # noqa: SLF001
