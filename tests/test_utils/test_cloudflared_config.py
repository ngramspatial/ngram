from pathlib import Path

from ngram.utils.cloudflared_config import tunnel_https_url_from_config


def test_tunnel_url_from_ingress_hostname(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yml"
    cfg.write_text(
        "tunnel: x\ningress:\n  - hostname: brain.example.com\n    service: http://127.0.0.1:8010\n",
        encoding="utf-8",
    )
    assert tunnel_https_url_from_config(cfg) == "https://brain.example.com"


def test_tunnel_url_missing(tmp_path: Path) -> None:
    assert tunnel_https_url_from_config(tmp_path / "nope.yml") == ""
