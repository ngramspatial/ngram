from __future__ import annotations

from pathlib import Path

import pytest

from ngram.presence.autonomy_transcript import append_autonomy_transcript


@pytest.mark.asyncio
async def test_append_autonomy_transcript_writes_header_and_sections(tmp_path) -> None:
    p = str(tmp_path / "autonomy_transcript.md")
    await append_autonomy_transcript(p, ["line a"], heading="wake start")
    await append_autonomy_transcript(p, ["tool line"], heading="tool shell")
    text = (tmp_path / "autonomy_transcript.md").read_text(encoding="utf-8")
    assert "# Autonomy transcript" in text
    assert "wake start" in text
    assert "line a" in text
    assert "tool shell" in text
    assert "tool line" in text


def test_entity_config_autonomy_transcript_path_resolves_beside_journal(tmp_path, monkeypatch) -> None:
    from ngram.config import HarnessConfig, EntityConfig, EntityPersonality, EntityDrives, EntityCognition, EntityPresence

    h = HarnessConfig()
    h.autonomy.transcript_path = ""
    h.autonomy.transcript_filename = "autonomy_transcript.md"
    cfg = EntityConfig(
        name="t",
        harness=h,
        personality=EntityPersonality(),
        drives=EntityDrives(),
        cognition=EntityCognition(),
        presence=EntityPresence(),
    )

    jp = tmp_path / "journal.md"
    jp.parent.mkdir(parents=True, exist_ok=True)
    jp.write_text("x", encoding="utf-8")

    def fake_journal_path() -> str:
        return str(jp)

    monkeypatch.setattr(cfg, "journal_path", fake_journal_path)
    monkeypatch.setattr(cfg, "execution_workspace_dir", lambda: "")
    out = Path(cfg.autonomy_transcript_path()).resolve()
    assert out.name == "autonomy_transcript.md"
    assert out.parent == tmp_path.resolve()
