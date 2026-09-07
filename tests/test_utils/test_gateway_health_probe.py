"""Tests for gateway /health URL helpers."""

from ngram.utils.gateway_health_probe import health_url_from_base


def test_health_url_from_base_appends_path() -> None:
    assert health_url_from_base("https://ngram.example.com") == "https://ngram.example.com/health"


def test_health_url_from_base_preserves_existing_health() -> None:
    assert health_url_from_base("https://ngram.example.com/health") == "https://ngram.example.com/health"
