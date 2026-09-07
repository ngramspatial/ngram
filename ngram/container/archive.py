"""Deterministic, traversal-safe tar transport for portable ngram containers."""

from __future__ import annotations

import io
import shutil
import tarfile
import tempfile
import uuid
from pathlib import Path, PurePosixPath

from ngram.container.format import (
    ContainerError,
    VerificationReport,
    load_manifest,
    verify_container,
)

_MAX_ARCHIVE_MEMBERS = 100_000
_MAX_ARCHIVE_FILE_BYTES = 5 * 1024 * 1024 * 1024
_MAX_ARCHIVE_TOTAL_BYTES = 20 * 1024 * 1024 * 1024


def _archive_root_name(container_root: Path) -> str:
    name = container_root.name
    return name if name.endswith(".ngram") else f"{name}.ngram"


def pack_archive(container_root: Path, output_path: Path) -> Path:
    """Create a deterministic uncompressed tar archive with a ``.ngram`` suffix."""
    container_root = container_root.resolve()
    report = verify_container(container_root)
    if not report.ok:
        raise ContainerError("refusing to archive invalid container: " + "; ".join(report.errors))
    output_path = output_path.expanduser().resolve()
    if output_path.suffix.lower() != ".ngram":
        raise ContainerError("archive path must end in .ngram")
    if output_path.exists():
        raise ContainerError(f"archive output already exists: {output_path}")
    try:
        output_path.relative_to(container_root)
    except ValueError:
        pass
    else:
        raise ContainerError("archive output cannot be inside the container directory")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = output_path.with_name(f".{output_path.name}.{uuid.uuid4().hex}.tmp")
    arc_root = _archive_root_name(container_root)
    try:
        with tarfile.open(temp_path, mode="w", format=tarfile.PAX_FORMAT) as archive:
            directories = [container_root]
            directories.extend(
                p for p in container_root.rglob("*") if p.is_dir() and not p.is_symlink()
            )
            for directory in sorted(directories, key=lambda p: p.as_posix()):
                if directory.is_symlink():
                    raise ContainerError(f"symbolic links are not allowed: {directory}")
                rel = directory.relative_to(container_root).as_posix()
                arcname = arc_root if rel == "." else f"{arc_root}/{rel}"
                info = tarfile.TarInfo(arcname.rstrip("/") + "/")
                info.type = tarfile.DIRTYPE
                info.mode = 0o755
                info.mtime = 0
                archive.addfile(info)
            files = sorted(
                (p for p in container_root.rglob("*") if p.is_file()),
                key=lambda p: p.as_posix(),
            )
            for path in files:
                if path.is_symlink():
                    raise ContainerError(f"symbolic links are not allowed: {path}")
                rel = path.relative_to(container_root).as_posix()
                payload = path.read_bytes()
                info = tarfile.TarInfo(f"{arc_root}/{rel}")
                info.size = len(payload)
                info.mode = 0o644
                info.mtime = 0
                archive.addfile(info, io.BytesIO(payload))
        temp_path.replace(output_path)
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise
    return output_path


def _validated_members(archive: tarfile.TarFile) -> tuple[str, list[tarfile.TarInfo]]:
    members = archive.getmembers()
    if not members:
        raise ContainerError("empty ngram archive")
    if len(members) > _MAX_ARCHIVE_MEMBERS:
        raise ContainerError("ngram archive contains too many members")
    roots: set[str] = set()
    seen: set[str] = set()
    total_bytes = 0
    for member in members:
        if "\\" in member.name:
            raise ContainerError(f"non-portable archive member path: {member.name!r}")
        pure = PurePosixPath(member.name)
        if pure.is_absolute() or not pure.parts or ".." in pure.parts:
            raise ContainerError(f"unsafe archive member path: {member.name!r}")
        collision_key = pure.as_posix().casefold()
        if collision_key in seen:
            raise ContainerError(f"duplicate archive member path: {member.name!r}")
        seen.add(collision_key)
        if member.issym() or member.islnk() or member.isdev() or member.isfifo():
            raise ContainerError(f"unsupported archive member type: {member.name!r}")
        if not member.isdir() and not member.isfile():
            raise ContainerError(f"unsupported archive member type: {member.name!r}")
        if member.isfile():
            if member.size < 0 or member.size > _MAX_ARCHIVE_FILE_BYTES:
                raise ContainerError(f"archive member is too large: {member.name!r}")
            total_bytes += member.size
            if total_bytes > _MAX_ARCHIVE_TOTAL_BYTES:
                raise ContainerError("ngram archive expands beyond the allowed size")
        roots.add(pure.parts[0])
    if len(roots) != 1:
        raise ContainerError("ngram archive must contain exactly one top-level directory")
    root_name = next(iter(roots))
    if not root_name.endswith(".ngram"):
        raise ContainerError("ngram archive top-level directory must end in .ngram")
    return root_name, members


def extract_archive(archive_path: Path, destination: Path) -> Path:
    """Extract and verify an archive without following links or traversal paths."""
    archive_path = archive_path.expanduser().resolve()
    destination = destination.expanduser().resolve()
    if destination.exists():
        raise ContainerError(f"destination already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage_parent = destination.parent / f".{destination.name}.{uuid.uuid4().hex}.tmp"
    stage_parent.mkdir()
    try:
        with tarfile.open(archive_path, mode="r:*") as archive:
            root_name, members = _validated_members(archive)
            archive_root = stage_parent / root_name
            for member in members:
                pure = PurePosixPath(member.name)
                target = stage_parent.joinpath(*pure.parts)
                target.resolve().relative_to(stage_parent.resolve())
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                source = archive.extractfile(member)
                if source is None:
                    raise ContainerError(f"could not read archive member: {member.name}")
                with source, target.open("wb") as sink:
                    shutil.copyfileobj(source, sink)
        report = verify_container(archive_root)
        if not report.ok:
            raise ContainerError(
                "extracted container failed verification: " + "; ".join(report.errors)
            )
        archive_root.replace(destination)
    except BaseException:
        shutil.rmtree(stage_parent, ignore_errors=True)
        raise
    shutil.rmtree(stage_parent, ignore_errors=True)
    return destination


def verify_artifact(path: Path) -> VerificationReport:
    path = path.expanduser().resolve()
    if path.is_dir():
        return verify_container(path)
    if not path.is_file():
        raise ContainerError(f"ngram artifact not found: {path}")
    with tempfile.TemporaryDirectory(prefix="ngram-verify-") as tmp:
        destination = Path(tmp) / "opened.ngram"
        extract_archive(path, destination)
        return verify_container(destination)


def load_artifact_manifest(path: Path) -> dict:
    """Load a manifest from either an open directory or a transport archive."""
    path = path.expanduser().resolve()
    if path.is_dir():
        return load_manifest(path)
    if not path.is_file():
        raise ContainerError(f"ngram artifact not found: {path}")
    with tempfile.TemporaryDirectory(prefix="ngram-manifest-") as tmp:
        destination = Path(tmp) / "opened.ngram"
        extract_archive(path, destination)
        return load_manifest(destination)
