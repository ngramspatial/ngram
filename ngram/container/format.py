"""Directory format, manifest construction, and deterministic integrity checks."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

SPEC_VERSION = "0.1.0-draft"
FORMAT_PROFILE = "portable-local-v1"
MANIFEST_NAME = "manifest.json"

REQUIRED_DIRECTORIES = (
    "identity",
    "autobiography",
    "soma",
    "agency",
    "embodiment",
    "world",
    "lineage",
)


class ContainerError(RuntimeError):
    """Raised when a container cannot be safely created, read, or transported."""


@dataclass(frozen=True)
class VerificationReport:
    root: Path
    ok: bool
    files_checked: int
    expected_root: str
    actual_root: str
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


def canonical_json_bytes(value: Any) -> bytes:
    """Encode JSON deterministically for hashes and append-only records."""
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(payload, encoding="utf-8", newline="\n")
    tmp.replace(path)


def _portable_relative_path(root: Path, path: Path) -> str:
    rel = path.relative_to(root).as_posix()
    pure = PurePosixPath(rel)
    if pure.is_absolute() or not pure.parts or ".." in pure.parts:
        raise ContainerError(f"unsafe path in container: {rel!r}")
    return rel


def collect_file_digests(root: Path) -> dict[str, str]:
    """Hash every regular state file, excluding the self-referential manifest."""
    root = root.resolve()
    if not root.is_dir():
        raise ContainerError(f"container directory not found: {root}")
    rows: dict[str, str] = {}
    portable_names: set[str] = set()
    for path in sorted(root.rglob("*"), key=lambda p: p.as_posix()):
        if path.is_symlink():
            raise ContainerError(f"symbolic links are not allowed in containers: {path}")
        if path.is_dir():
            continue
        if not path.is_file():
            raise ContainerError(f"unsupported filesystem entry in container: {path}")
        rel = _portable_relative_path(root, path)
        if rel == MANIFEST_NAME:
            continue
        portable_name = rel.casefold()
        if portable_name in portable_names:
            raise ContainerError(f"case-colliding paths are not portable: {rel!r}")
        portable_names.add(portable_name)
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        rows[rel] = digest.hexdigest()
    return rows


def integrity_root(file_digests: Mapping[str, str]) -> str:
    """Return a domain-separated root over sorted path/digest pairs."""
    digest = hashlib.sha256()
    digest.update(b"ngram-integrity-root-v1\0")
    for rel, file_digest in sorted(file_digests.items()):
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        try:
            digest.update(bytes.fromhex(file_digest))
        except ValueError as exc:
            raise ContainerError(f"invalid SHA-256 digest for {rel!r}") from exc
    return f"sha256:{digest.hexdigest()}"


def finalize_manifest(root: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    """Compute state integrity and atomically write ``manifest.json``."""
    files = collect_file_digests(root)
    root_hash = integrity_root(files)
    out = dict(manifest)
    out["integrity_root"] = root_hash
    out["integrity"] = {
        "algorithm": "sha256",
        "root_version": 1,
        "scope": "all regular files except manifest.json",
        "files": files,
    }
    write_json(root / MANIFEST_NAME, out)
    return out


def load_manifest(root: Path) -> dict[str, Any]:
    path = root / MANIFEST_NAME
    if not path.is_file():
        raise ContainerError(f"missing {MANIFEST_NAME}: {root}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContainerError(f"invalid {MANIFEST_NAME}: {exc}") from exc
    if not isinstance(value, dict):
        raise ContainerError(f"{MANIFEST_NAME} must contain a JSON object")
    return value


def verify_container(root: Path) -> VerificationReport:
    root = root.resolve()
    errors: list[str] = []
    warnings: list[str] = []
    try:
        manifest = load_manifest(root)
    except ContainerError as exc:
        return VerificationReport(root, False, 0, "", "", (str(exc),), ())

    if manifest.get("spec_version") != SPEC_VERSION:
        errors.append(
            f"unsupported spec_version: {manifest.get('spec_version')!r}; expected {SPEC_VERSION!r}"
        )
    if not str(manifest.get("entity_id") or "").startswith("ng1:"):
        errors.append("manifest entity_id must start with 'ng1:'")
    if manifest.get("format_profile") != FORMAT_PROFILE:
        errors.append(
            f"unsupported format_profile: {manifest.get('format_profile')!r}; "
            f"expected {FORMAT_PROFILE!r}"
        )
    security = manifest.get("security")
    if not isinstance(security, dict) or security.get("profile") != "filesystem-local":
        errors.append("portable local containers must declare security.profile='filesystem-local'")
    elif security.get("encrypted") is not False:
        errors.append("portable local containers must declare security.encrypted=false")
    else:
        warnings.append("FILESYSTEM-LOCAL: contents are integrity-checked but not encrypted")
    if isinstance(security, dict) and security.get("canonical_lease_enforced") is True:
        lease = manifest.get("lease")
        if not isinstance(lease, dict):
            errors.append("lease-enforced containers must include a public lease record")
        else:
            if lease.get("entity_id") != manifest.get("entity_id"):
                errors.append("portable lease entity does not match manifest entity_id")
            if lease.get("activation_required") is not True:
                errors.append("portable lease record must require activation")
            if lease.get("token_included") is not False:
                errors.append("portable lease records must never contain a lease token")

    for rel in REQUIRED_DIRECTORIES:
        if not (root / rel).is_dir():
            errors.append(f"missing required domain directory: {rel}/")

    inventory_path = root / "autobiography" / "attachments" / "inventory.json"
    if inventory_path.is_file():
        try:
            from ngram.container.object_inventory import load_attachment_inventory

            load_attachment_inventory(root)
        except ContainerError as exc:
            errors.append(str(exc))

    governance_ledger = root / "agency" / "governance" / "ledger.jsonl"
    if governance_ledger.is_file():
        try:
            from ngram.governance import ChangeProtocol, ChangeProtocolError

            ChangeProtocol(
                governance_ledger.parent,
                str(manifest.get("entity_id") or ""),
            ).verify()
        except ChangeProtocolError as exc:
            errors.append(f"invalid change protocol ledger: {exc}")

    try:
        actual_files = collect_file_digests(root)
        actual_root = integrity_root(actual_files)
    except ContainerError as exc:
        errors.append(str(exc))
        actual_files = {}
        actual_root = ""

    expected_root = str(manifest.get("integrity_root") or "")
    if expected_root != actual_root:
        errors.append(
            f"integrity root mismatch: expected {expected_root or '(missing)'}, got {actual_root}"
        )

    integrity = manifest.get("integrity")
    expected_files = integrity.get("files") if isinstance(integrity, dict) else None
    if not isinstance(expected_files, dict):
        errors.append("manifest integrity.files must be an object")
    elif expected_files != actual_files:
        expected_names = set(expected_files)
        actual_names = set(actual_files)
        for rel in sorted(expected_names - actual_names):
            errors.append(f"missing state file: {rel}")
        for rel in sorted(actual_names - expected_names):
            errors.append(f"unexpected state file: {rel}")
        for rel in sorted(expected_names & actual_names):
            if expected_files[rel] != actual_files[rel]:
                errors.append(f"modified state file: {rel}")

    return VerificationReport(
        root=root,
        ok=not errors,
        files_checked=len(actual_files),
        expected_root=expected_root,
        actual_root=actual_root,
        errors=tuple(errors),
        warnings=tuple(warnings),
    )
