import pytest


@pytest.fixture(autouse=True)
def isolate_external_runtime(monkeypatch):
    """Keep a developer's local deployment credentials out of offline tests."""
    monkeypatch.delenv("DATABASE_URL", raising=False)


@pytest.fixture
def anyio_backend():
    return "asyncio"
