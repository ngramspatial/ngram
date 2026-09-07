"""Safe local operations behind ngram Studio."""

from __future__ import annotations

import json
import math
import re
import shutil
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from ngram.config import HarnessConfig, load_live_container_config
from ngram.container import (
    ContainerError,
    LocalMountLock,
    commit_canonical_json_update,
    container_control_dir,
    load_manifest,
    pack_archive,
    recover_interrupted_container,
    verify_container,
)
from ngram.container.format import REQUIRED_DIRECTORIES

_EDITABLE_SETTINGS = {
    "preferences": "agency/preferences.json",
    "policies": "agency/policies.json",
    "schedule": "agency/schedule.json",
    "embodiment": "embodiment/profile.json",
}
_SECRET_KEY = re.compile(
    r"(?:^|_)(?:api_?key|token|secret|password|credential)(?:$|_)", re.IGNORECASE
)
_MAX_INSPECT_BYTES = 2 * 1024 * 1024
_MAX_SETTINGS_BYTES = 512 * 1024


class StudioError(RuntimeError):
    """A user-actionable Studio operation failure."""


def _read_object(path: Path, default: dict[str, Any] | None = None) -> dict[str, Any]:
    if not path.is_file():
        return dict(default or {})
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StudioError(f"Invalid JSON document: {path.name}") from exc
    if not isinstance(value, dict):
        raise StudioError(f"Expected a JSON object: {path.name}")
    return value


def _read_control(path: Path) -> dict[str, Any]:
    try:
        return _read_object(path)
    except StudioError:
        return {"invalid": True, "path": str(path)}


def _read_object_soft(path: Path) -> dict[str, Any]:
    try:
        return _read_object(path)
    except StudioError as exc:
        return {"_studio_error": str(exc)}


def _json_write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    temp.replace(path)


def _contains_embedded_secret(value: Any, parent_key: str = "") -> bool:
    allowed_placeholders = (None, "", "<redacted>")
    if isinstance(value, dict):
        for key, item in value.items():
            name = str(key).lower()
            env_reference = name.endswith("_env") or name in {"token_env", "api_key_env"}
            if not env_reference and _SECRET_KEY.search(name) and item not in allowed_placeholders:
                return True
            if parent_key == "env" and item not in allowed_placeholders:
                return True
            if _contains_embedded_secret(item, name):
                return True
    elif isinstance(value, list):
        return any(_contains_embedded_secret(item, parent_key) for item in value)
    return False


class StudioSession:
    """Own the local Studio lock and expose bounded canonical operations."""

    def __init__(self, container_path: Path, harness: HarnessConfig | None = None) -> None:
        self.root = container_path.expanduser().resolve()
        self.config = load_live_container_config(self.root, harness, verify=False)
        self.control = container_control_dir(self.config)
        self.lock = LocalMountLock(self.control, self.root, purpose="studio")
        self.read_only_reason = "Studio is starting"
        self.notice = ""

    @property
    def transaction_dir(self) -> Path:
        return self.control / "studio-transaction"

    def start(self) -> None:
        dirty = _read_control(self.control / "dirty.json")
        if dirty:
            self.read_only_reason = "Interrupted runtime state must be recovered before editing"
            return
        report = verify_container(self.root)
        if self.transaction_dir.exists():
            try:
                self.lock.acquire()
                self._rollback_transaction()
                report = verify_container(self.root)
                self.notice = "An interrupted Studio edit was rolled back safely."
            except (ContainerError, StudioError) as exc:
                self.read_only_reason = str(exc)
                self.lock.release()
                return
        elif report.ok:
            try:
                self.lock.acquire()
            except ContainerError as exc:
                self.read_only_reason = str(exc)
                return
        if report.ok and self.lock.held:
            self.read_only_reason = ""
        elif not report.ok:
            self.read_only_reason = "Container integrity must be restored before editing"

    def close(self) -> None:
        self.lock.release()

    def _domain_summary(self) -> list[dict[str, Any]]:
        summaries: list[dict[str, Any]] = []
        for name in REQUIRED_DIRECTORIES:
            directory = self.root / name
            files = (
                [path for path in directory.rglob("*") if not path.is_symlink() and path.is_file()]
                if directory.is_dir()
                else []
            )
            summaries.append(
                {
                    "name": name,
                    "files": len(files),
                    "bytes": sum(path.stat().st_size for path in files),
                }
            )
        return summaries

    def _lineage_rows(self) -> list[dict[str, Any]]:
        path = self.root / "lineage" / "chain.jsonl"
        if not path.is_file():
            return []
        rows: list[dict[str, Any]] = []
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                rows.append({"sequence": number - 1, "type": "invalid", "line": number})
                continue
            if isinstance(row, dict):
                rows.append(row)
        return rows

    def _lock_status(self) -> dict[str, Any]:
        if self.lock.held:
            return {"status": "studio", "purpose": "studio", "pid": None, "host": "local"}
        payload = _read_control(self.control / "mount.lock")
        if payload:
            return {
                "status": "held",
                "purpose": payload.get("purpose") or "runtime",
                "pid": payload.get("pid"),
                "host": payload.get("host"),
            }
        return {"status": "available", "purpose": None, "pid": None, "host": None}

    def overview(self) -> dict[str, Any]:
        report = verify_container(self.root)
        try:
            manifest = load_manifest(self.root)
        except ContainerError:
            manifest = {}
        lineage = self._lineage_rows()
        dirty = _read_control(self.control / "dirty.json")
        soma = _read_object_soft(self.root / "soma" / "state.json")
        baselines = _read_object_soft(self.root / "soma" / "baselines.json")
        return {
            "manifest": manifest,
            "verification": {
                "ok": report.ok,
                "files_checked": report.files_checked,
                "expected_root": report.expected_root,
                "actual_root": report.actual_root,
                "errors": list(report.errors),
                "warnings": list(report.warnings),
            },
            "root": str(self.root),
            "domains": self._domain_summary(),
            "lineage_count": len(lineage),
            "last_transition": lineage[-1] if lineage else None,
            "soma": {"state": soma, "baselines": baselines},
            "lock": self._lock_status(),
            "dirty": bool(dirty),
            "dirty_detail": dirty,
            "writable": bool(report.ok and self.lock.held and not dirty),
            "read_only_reason": self.read_only_reason,
            "notice": self.notice,
            "settings": self.settings(),
        }

    def settings(self) -> dict[str, dict[str, Any]]:
        return {
            name: _read_object_soft(self.root / relative)
            for name, relative in _EDITABLE_SETTINGS.items()
        }

    def lineage(self) -> list[dict[str, Any]]:
        return list(reversed(self._lineage_rows()))

    def list_domain(self, domain: str) -> list[dict[str, Any]]:
        if domain not in REQUIRED_DIRECTORIES:
            raise StudioError(f"Unknown state domain: {domain}")
        directory = self.root / domain
        if not directory.is_dir():
            return []
        rows: list[dict[str, Any]] = []
        for path in sorted(directory.rglob("*"), key=lambda item: item.as_posix()):
            if path.is_symlink() or not path.is_file():
                continue
            relative = path.relative_to(self.root).as_posix()
            suffix = path.suffix.lower()
            rows.append(
                {
                    "path": relative,
                    "name": path.name,
                    "bytes": path.stat().st_size,
                    "kind": "json" if suffix in {".json", ".jsonl"} else "text",
                }
            )
        return rows

    def inspect_file(self, relative_path: str) -> dict[str, Any]:
        pure = PurePosixPath(relative_path)
        if pure.is_absolute() or not pure.parts or ".." in pure.parts:
            raise StudioError("Unsafe container path")
        if pure.parts[0] not in REQUIRED_DIRECTORIES and pure.as_posix() != "manifest.json":
            raise StudioError("Path is outside the inspectable container domains")
        path = self.root.joinpath(*pure.parts)
        try:
            path.resolve().relative_to(self.root)
        except ValueError as exc:
            raise StudioError("Path escapes the container") from exc
        if path.is_symlink() or not path.is_file():
            raise StudioError("State file was not found")
        size = path.stat().st_size
        if size > _MAX_INSPECT_BYTES:
            return {"path": pure.as_posix(), "bytes": size, "inspectable": False, "content": ""}
        payload = path.read_bytes()
        if b"\0" in payload:
            return {"path": pure.as_posix(), "bytes": size, "inspectable": False, "content": ""}
        try:
            content = payload.decode("utf-8")
        except UnicodeDecodeError:
            return {"path": pure.as_posix(), "bytes": size, "inspectable": False, "content": ""}
        return {"path": pure.as_posix(), "bytes": size, "inspectable": True, "content": content}

    def simulate_soma(self, hours: float, steps: int = 24) -> dict[str, Any]:
        hours = max(0.25, min(168.0, float(hours)))
        steps = max(2, min(96, int(steps)))
        state = _read_object(self.root / "soma" / "state.json")
        baselines = _read_object(self.root / "soma" / "baselines.json")
        dynamics = _read_object(self.root / "soma" / "dynamics.json")
        values = state.get("values") if isinstance(state.get("values"), dict) else {}
        variables = ((dynamics.get("bars") or {}).get("variables") or [])
        definitions = {
            str(item.get("name")): item
            for item in variables
            if isinstance(item, dict) and item.get("name")
        }
        names = list(state.get("ordered_names") or values)
        timeline: list[dict[str, Any]] = []
        for index in range(steps + 1):
            at = hours * index / steps
            projected: dict[str, float] = {}
            for name in names:
                current = float(values.get(name, baselines.get(name, 50.0)))
                baseline = float(baselines.get(name, current))
                definition = definitions.get(name, {})
                rate = abs(float(definition.get("decay_rate", 2.5))) / 100.0
                floor = float(definition.get("floor", 0.0))
                ceiling = float(definition.get("ceiling", 100.0))
                result = baseline + (current - baseline) * math.exp(-rate * at)
                projected[name] = round(max(floor, min(ceiling, result)), 2)
            timeline.append({"hour": round(at, 2), "values": projected})
        final_values = timeline[-1]["values"] if timeline else {}
        changes = {
            name: round(float(final_values.get(name, 0)) - float(values.get(name, 0)), 2)
            for name in names
        }
        return {
            "hours": hours,
            "source": "container-dynamics" if definitions else "portable-defaults",
            "timeline": timeline,
            "changes": changes,
            "note": "Preview only. Canonical soma state is not changed.",
        }

    def _begin_transaction(self, relative_path: str) -> None:
        transaction = self.transaction_dir
        transaction.mkdir(parents=True, exist_ok=False)
        targets = {
            "target": self.root / relative_path,
            "lineage": self.root / "lineage" / "chain.jsonl",
            "manifest": self.root / "manifest.json",
        }
        metadata = {"relative_path": relative_path, "created_at": time.time(), "files": {}}
        for name, path in targets.items():
            exists = path.is_file()
            metadata["files"][name] = {"exists": exists}
            if exists:
                (transaction / f"{name}.bak").write_bytes(path.read_bytes())
        _json_write(transaction / "transaction.json", metadata)

    def _rollback_transaction(self) -> None:
        transaction = self.transaction_dir
        metadata = _read_object(transaction / "transaction.json")
        relative_path = str(metadata.get("relative_path") or "")
        if relative_path not in _EDITABLE_SETTINGS.values():
            raise StudioError("Studio transaction metadata is invalid")
        targets = {
            "target": self.root / relative_path,
            "lineage": self.root / "lineage" / "chain.jsonl",
            "manifest": self.root / "manifest.json",
        }
        file_meta = metadata.get("files") if isinstance(metadata.get("files"), dict) else {}
        for name, path in targets.items():
            existed = bool((file_meta.get(name) or {}).get("exists"))
            backup = transaction / f"{name}.bak"
            if existed:
                if not backup.is_file():
                    raise StudioError(f"Studio transaction backup is missing: {name}")
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(backup.read_bytes())
            else:
                path.unlink(missing_ok=True)
            path.with_name(path.name + ".tmp").unlink(missing_ok=True)
        shutil.rmtree(transaction)

    def update_setting(self, section: str, value: dict[str, Any]) -> dict[str, Any]:
        relative_path = _EDITABLE_SETTINGS.get(section)
        if relative_path is None:
            raise StudioError(f"Unknown editable settings section: {section}")
        if not self.lock.held or self.read_only_reason:
            raise StudioError(self.read_only_reason or "Studio does not hold the entity lock")
        if not isinstance(value, dict):
            raise StudioError("Settings must be a JSON object")
        encoded = json.dumps(value, ensure_ascii=False).encode("utf-8")
        if len(encoded) > _MAX_SETTINGS_BYTES:
            raise StudioError("Settings document is too large")
        if _contains_embedded_secret(value):
            raise StudioError("Credentials and secret values do not belong in a portable container")
        self._begin_transaction(relative_path)
        try:
            manifest = commit_canonical_json_update(
                self.root,
                relative_path,
                value,
                event_type="studio_settings_updated",
                event_payload={"section": section, "keys": sorted(value)},
            )
            report = verify_container(self.root)
            if not report.ok:
                raise StudioError("Edited container failed verification")
        except (ContainerError, StudioError) as exc:
            self._rollback_transaction()
            raise StudioError(str(exc)) from exc
        except BaseException:
            self._rollback_transaction()
            raise
        shutil.rmtree(self.transaction_dir)
        self.notice = f"{section.title()} settings saved and lineage advanced."
        return {"manifest": manifest, "section": section, "value": value}

    def recover(self) -> dict[str, Any]:
        if self.lock.held:
            raise StudioError("Studio recovery is available only for an interrupted runtime session")
        try:
            recover_interrupted_container(self.config)
            self.lock.acquire()
        except ContainerError as exc:
            raise StudioError(str(exc)) from exc
        self.read_only_reason = ""
        self.notice = "Interrupted runtime cache recovered; lineage and integrity were advanced."
        return self.overview()

    def export_snapshot(self) -> tuple[Path, Path, str]:
        if not self.lock.held or self.read_only_reason:
            raise StudioError(self.read_only_reason or "Studio does not hold the entity lock")
        report = verify_container(self.root)
        if not report.ok:
            raise StudioError("Container must verify before export")
        manifest = load_manifest(self.root)
        display = str(manifest.get("display_name") or self.root.stem)
        safe = re.sub(r"[^A-Za-z0-9._-]+", "-", display).strip(".-").lower() or "entity"
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        directory = Path(tempfile.mkdtemp(prefix="ngram-studio-export-"))
        archive = directory / f"{safe}-{stamp}.ngram"
        pack_archive(self.root, archive)
        return archive, directory, archive.name
