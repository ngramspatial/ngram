from __future__ import annotations

import json
import tarfile
from pathlib import Path

import pytest

from ngram.container.archive import extract_archive, pack_archive, verify_artifact
from ngram.container.format import (
    FORMAT_PROFILE,
    REQUIRED_DIRECTORIES,
    SPEC_VERSION,
    ContainerError,
    finalize_manifest,
    verify_container,
)


def _container(root: Path) -> Path:
    for name in REQUIRED_DIRECTORIES:
        (root / name).mkdir(parents=True, exist_ok=True)
    (root / "identity" / "identity.md").write_text("# Aya\n", encoding="utf-8")
    finalize_manifest(
        root,
        {
            "spec_version": SPEC_VERSION,
            "format_profile": FORMAT_PROFILE,
            "entity_id": "ng1:" + "a" * 40,
            "display_name": "Aya",
            "security": {"profile": "filesystem-local", "encrypted": False},
        },
    )
    return root


def test_verify_detects_modified_and_unexpected_files(tmp_path: Path) -> None:
    root = _container(tmp_path / "aya.ngram")
    assert verify_container(root).ok

    identity = root / "identity" / "identity.md"
    identity.write_text("# Someone else\n", encoding="utf-8")
    report = verify_container(root)
    assert not report.ok
    assert "modified state file: identity/identity.md" in report.errors

    identity.write_text("# Aya\n", encoding="utf-8")
    (root / "world" / "extra.json").write_text("{}\n", encoding="utf-8")
    report = verify_container(root)
    assert not report.ok
    assert "unexpected state file: world/extra.json" in report.errors


def test_archive_is_deterministic_and_roundtrips(tmp_path: Path) -> None:
    root = _container(tmp_path / "aya.ngram")
    first = pack_archive(root, tmp_path / "first.ngram")
    second = pack_archive(root, tmp_path / "second.ngram")
    assert first.read_bytes() == second.read_bytes()

    opened = extract_archive(first, tmp_path / "opened.ngram")
    assert verify_container(opened).ok
    assert verify_artifact(first).ok
    assert (
        json.loads((opened / "manifest.json").read_text(encoding="utf-8"))["display_name"] == "Aya"
    )


def test_extract_rejects_path_traversal(tmp_path: Path) -> None:
    archive_path = tmp_path / "bad.ngram"
    payload = tmp_path / "payload.txt"
    payload.write_text("bad", encoding="utf-8")
    with tarfile.open(archive_path, "w") as archive:
        archive.add(payload, arcname="aya.ngram/../../outside.txt")

    with pytest.raises(ContainerError, match="unsafe archive member path"):
        extract_archive(archive_path, tmp_path / "opened.ngram")


def test_pack_refuses_overwrite(tmp_path: Path) -> None:
    root = _container(tmp_path / "aya.ngram")
    output = tmp_path / "aya-transport.ngram"
    pack_archive(root, output)
    with pytest.raises(ContainerError, match="already exists"):
        pack_archive(root, output)
