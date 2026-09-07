from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from ngram.ngram_ar.setup import (
    RailwayCli,
    _collect_service_names,
    _health_url,
    _websocket_url,
    configure_shell,
    set_shell_entity_binding,
)


def test_shell_binding_change_preserves_body_and_system_prompt(tmp_path: Path) -> None:
    shell = tmp_path / "shell.yaml"
    shell.write_text(
        """name: Rook
model: models/rook.glb
binding:
  type: openai
  model: local-model
  system: |
    Keep this spatial instruction.
memory:
  path: ./memory
""",
        encoding="utf-8",
    )

    set_shell_entity_binding(shell)

    result = shell.read_text(encoding="utf-8")
    assert "model: models/rook.glb" in result
    assert "type: ngram_entity" in result
    assert "Keep this spatial instruction." in result
    assert "memory:\n  path: ./memory" in result


def test_shell_binding_is_added_without_reformatting(tmp_path: Path) -> None:
    shell = tmp_path / "shell.yaml"
    shell.write_text("name: Rook\nmodel: default\n", encoding="utf-8")

    set_shell_entity_binding(shell)

    assert shell.read_text(encoding="utf-8") == (
        "name: Rook\nmodel: default\n\nbinding:\n  type: ngram_entity\n  options: {}\n"
    )


def test_configure_shell_writes_ignored_private_connection(tmp_path: Path) -> None:
    shell_dir = tmp_path / "rook"
    shell_dir.mkdir()
    (shell_dir / "shell.yaml").write_text(
        "name: Rook\nbinding:\n  type: openai\n",
        encoding="utf-8",
    )

    configure_shell(
        shell_dir,
        bridge_url="wss://rook.example/",
        token="private-token",
    )

    env_text = (shell_dir / ".env").read_text(encoding="utf-8")
    assert "NGRAM_AR_ENTITY_BRIDGE_URL=wss://rook.example/" in env_text
    assert "NGRAM_AR_ENTITY_BRIDGE_TOKEN=private-token" in env_text
    assert ".env" in (shell_dir / ".gitignore").read_text(encoding="utf-8").splitlines()


def test_railway_status_and_url_helpers() -> None:
    status = {
        "services": {
            "edges": [
                {"node": {"name": "rook-api"}},
                {"node": {"name": "rook-worker"}},
            ]
        }
    }
    assert _collect_service_names(status) == ["rook-api", "rook-worker"]
    assert _websocket_url("https://rook.example") == "wss://rook.example/"
    assert _websocket_url("ws://127.0.0.1:7878/") == "ws://127.0.0.1:7878/"
    assert _health_url("wss://rook.example/") == "https://rook.example/health"


class RecordingRailway(RailwayCli):
    def __init__(self, responses: list[str] | None = None) -> None:
        super().__init__(["railway"], cwd=Path("."))
        self.responses = list(responses or [])
        self.calls: list[tuple[list[str], str | None]] = []

    def run(
        self,
        arguments: list[str],
        *,
        stdin: str | None = None,
        timeout: int = 180,
    ) -> str:
        del timeout
        self.calls.append((arguments, stdin))
        return self.responses.pop(0) if self.responses else ""


def test_railway_secret_is_sent_over_stdin() -> None:
    railway = RecordingRailway()
    railway.configure_bridge(
        service="rook-worker",
        port=8080,
        token="dedicated-secret",
        person_id="123456789",
        person_name="You",
    )

    assert len(railway.calls) == 2
    assert "dedicated-secret" not in " ".join(railway.calls[1][0])
    assert railway.calls[1][1] == "dedicated-secret"
    assert "NGRAM_AR_PERSON_ID=123456789" in railway.calls[0][0]


def test_existing_railway_domain_is_reused() -> None:
    railway = RecordingRailway(
        [
            '{"domains":[{"domain":"rook.example","targetPort":8080}]}'
        ]
    )

    assert railway.ensure_domain(service="rook-worker", port=8080) == "wss://rook.example/"
    assert len(railway.calls) == 1


def test_railway_discovery_skips_stale_unauthorized_cli(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fake_which(name: str) -> str | None:
        return {
            "railway": "railway-old",
            "npx.cmd": "npx-new",
        }.get(name)

    def fake_run(command: list[str], **_kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(returncode=1 if command[0] == "railway-old" else 0)

    monkeypatch.setattr("ngram.ngram_ar.setup.shutil.which", fake_which)
    monkeypatch.setattr("ngram.ngram_ar.setup.subprocess.run", fake_run)

    railway = RailwayCli.discover(cwd=tmp_path)

    assert railway.command == ["npx-new", "-y", "@railway/cli@latest"]
