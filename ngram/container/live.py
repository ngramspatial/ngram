"""Live mounting for canonical portable-local containers with disposable runtime caches."""

from __future__ import annotations

import asyncio
import atexit
import json
import os
import shutil
import socket
import time
import uuid
from pathlib import Path
from typing import Any

import psutil

from ngram.container.archive import pack_archive
from ngram.container.format import (
    ContainerError,
    collect_file_digests,
    load_manifest,
    verify_container,
)
from ngram.container.migration import checkpoint_runtime_state
from ngram.container.restore import hydrate_runtime_state

_RECOVERABLE_PATHS = (
    "identity/profile.json",
    "autobiography/records.json",
    "autobiography/episodes/",
    "autobiography/people/",
    "autobiography/beliefs.md",
    "autobiography/biography.md",
    "autobiography/knowledge.md",
    "autobiography/journal.md",
    "autobiography/autonomy-transcript.md",
    "autobiography/attachments/",
    "soma/",
    "agency/preferences.json",
    "agency/records.json",
    "agency/runtime-extension-records.json",
    "agency/skills/",
    "agency/governance/",
    "agency/goals.json",
    "lineage/chain.jsonl",
)


def _json_write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temp.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    temp.replace(path)


def _json_read(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContainerError(f"invalid live-container control file: {path}") from exc
    if not isinstance(value, dict):
        raise ContainerError(f"live-container control file must contain an object: {path}")
    return value


def _process_is_same(payload: dict[str, Any]) -> bool:
    if str(payload.get("host") or "") != socket.gethostname():
        return True
    try:
        pid = int(payload.get("pid") or 0)
        expected_start = float(payload.get("process_started_at") or 0.0)
    except (TypeError, ValueError):
        return True
    if pid <= 0 or not psutil.pid_exists(pid):
        return False
    try:
        actual_start = psutil.Process(pid).create_time()
    except (psutil.Error, OSError):
        return True
    return abs(actual_start - expected_start) < 1.0


class LocalMountLock:
    """Best-effort single-process lock; it is intentionally not a distributed lease."""

    def __init__(
        self,
        control_dir: Path,
        container_root: Path,
        *,
        purpose: str = "runtime",
    ) -> None:
        self.control_dir = control_dir
        self.container_root = container_root
        self.purpose = purpose
        self.path = control_dir / "mount.lock"
        self.token = uuid.uuid4().hex
        self.held = False

    def acquire(self) -> None:
        self.control_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "token": self.token,
            "pid": os.getpid(),
            "host": socket.gethostname(),
            "process_started_at": psutil.Process(os.getpid()).create_time(),
            "container_root": str(self.container_root),
            "purpose": self.purpose,
            "acquired_at": time.time(),
        }
        for _attempt in range(2):
            try:
                descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                existing = _json_read(self.path)
                if _process_is_same(existing):
                    raise ContainerError(
                        "live container is already mounted by "
                        f"pid {existing.get('pid')} on {existing.get('host')}"
                    )
                stale = self.control_dir / f"mount.lock.stale.{int(time.time())}.{uuid.uuid4().hex}"
                try:
                    self.path.replace(stale)
                except OSError as exc:
                    raise ContainerError("could not quarantine stale live-container lock") from exc
                continue
            try:
                encoded = (json.dumps(payload, sort_keys=True) + "\n").encode("utf-8")
                os.write(descriptor, encoded)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            self.held = True
            return
        raise ContainerError("could not acquire live-container lock")

    def release(self) -> None:
        if not self.held:
            return
        try:
            current = _json_read(self.path)
            if current.get("token") == self.token:
                self.path.unlink(missing_ok=True)
        finally:
            self.held = False


def _control_dir(config: Any) -> Path:
    state = Path(str(config.runtime_cache_dir)).expanduser().resolve()
    return state.parent


def container_control_dir(config: Any) -> Path:
    """Return the external control directory shared by runtime and Studio."""
    return _control_dir(config)


def _write_cache_meta(config: Any, manifest: dict[str, Any]) -> None:
    _json_write(
        _control_dir(config) / "cache-meta.json",
        {
            "container_root": str(Path(config.container_root).resolve()),
            "entity_id": manifest.get("entity_id"),
            "integrity_root": manifest.get("integrity_root"),
            "updated_at": time.time(),
        },
    )


def _write_dirty_marker(config: Any, session_id: str, manifest: dict[str, Any]) -> None:
    path = _control_dir(config) / "dirty.json"
    previous = _json_read(path)
    opened_at = previous.get("opened_at") if previous.get("session_id") == session_id else None
    _json_write(
        path,
        {
            "session_id": session_id,
            "container_root": str(Path(config.container_root).resolve()),
            "base_integrity_root": manifest.get("integrity_root"),
            "opened_at": opened_at or time.time(),
            "last_checkpoint_at": time.time(),
        },
    )


def _cache_matches(config: Any, manifest: dict[str, Any]) -> bool:
    state = Path(config.runtime_cache_dir).expanduser()
    meta = _json_read(_control_dir(config) / "cache-meta.json")
    return (
        (state / "memory.db").is_file()
        and meta.get("container_root") == str(Path(config.container_root).resolve())
        and meta.get("entity_id") == manifest.get("entity_id")
        and meta.get("integrity_root") == manifest.get("integrity_root")
    )


def _replace_cache_from_container(config: Any) -> None:
    state = Path(config.runtime_cache_dir).expanduser()
    stale: Path | None = None
    if state.exists():
        stale = state.with_name(f"state.stale.{int(time.time())}.{uuid.uuid4().hex}")
        state.replace(stale)
    try:
        hydrate_runtime_state(Path(config.container_root), state)
    except BaseException:
        if state.exists():
            shutil.rmtree(state)
        if stale is not None:
            stale.replace(state)
        raise
    if stale is not None:
        shutil.rmtree(stale)


def _changed_paths(root: Path, manifest: dict[str, Any]) -> set[str]:
    expected_block = manifest.get("integrity")
    expected = expected_block.get("files") if isinstance(expected_block, dict) else None
    if not isinstance(expected, dict):
        raise ContainerError("container manifest has no integrity file map")
    actual = collect_file_digests(root)
    names = set(expected) | set(actual)
    return {name for name in names if expected.get(name) != actual.get(name)}


def _is_recoverable_path(path: str) -> bool:
    if any(path == prefix or path.startswith(prefix) for prefix in _RECOVERABLE_PATHS):
        return True
    if path == "manifest.json.tmp":
        return True
    if path.endswith(".tmp"):
        final_path = path.removesuffix(".tmp")
        return any(
            final_path == prefix or final_path.startswith(prefix) for prefix in _RECOVERABLE_PATHS
        )
    return False


def _remove_checkpoint_temporaries(root: Path) -> None:
    for path in root.rglob("*.tmp"):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(root).as_posix()
        if _is_recoverable_path(relative):
            path.unlink()


def recover_interrupted_container(config: Any) -> dict[str, Any]:
    """Explicitly reconcile a dirty cache after a crashed local mount."""
    root = Path(config.container_root).expanduser().resolve()
    control = _control_dir(config)
    dirty_path = control / "dirty.json"
    dirty = _json_read(dirty_path)
    if not dirty:
        raise ContainerError("no interrupted live-container session is recorded")
    if dirty.get("container_root") != str(root):
        raise ContainerError("dirty cache belongs to a different container path")
    state = Path(config.runtime_cache_dir).expanduser()
    if not (state / "memory.db").is_file():
        raise ContainerError("dirty runtime cache has no memory database")
    manifest = load_manifest(root)
    report = verify_container(root)
    if dirty.get("base_integrity_root") != manifest.get("integrity_root") and not report.ok:
        raise ContainerError(
            "manifest changed during an incomplete checkpoint; refusing automatic recovery"
        )
    changed = _changed_paths(root, manifest)
    unsafe = sorted(path for path in changed if not _is_recoverable_path(path))
    if unsafe:
        raise ContainerError(
            "interrupted container has changes outside runtime-managed paths: " + ", ".join(unsafe)
        )

    lock = LocalMountLock(control, root)
    lock.acquire()
    try:
        _remove_checkpoint_temporaries(root)
        updated = checkpoint_runtime_state(
            config,
            root,
            event_type="crash_recovered",
            event_payload={"interrupted_session_id": dirty.get("session_id")},
        )
        _write_cache_meta(config, updated)
        dirty_path.unlink(missing_ok=True)
        return updated
    finally:
        lock.release()


def pack_locked_live_container(config: Any, output_path: Path) -> Path:
    """Archive a clean, idle live container while holding its local process lock."""
    root = Path(config.container_root).expanduser().resolve()
    control = _control_dir(config)
    lock = LocalMountLock(control, root)
    lock.acquire()
    try:
        if _json_read(control / "dirty.json"):
            raise ContainerError(
                "an interrupted live session needs recovery before export; "
                "run 'ngram recover <path>'"
            )
        return pack_archive(root, output_path)
    finally:
        lock.release()


class LiveContainerMount:
    """Own a verified container mount and serialize canonical checkpoints."""

    def __init__(self, config: Any) -> None:
        if not getattr(config, "is_live_container", lambda: False)():
            raise ContainerError("entity config is not backed by a live container")
        self.config = config
        self.root = Path(config.container_root).expanduser().resolve()
        self.control = _control_dir(config)
        self.lock = LocalMountLock(self.control, self.root)
        self.session_id = uuid.uuid4().hex
        self._checkpoint_lock: asyncio.Lock | None = None
        self.opened = False
        atexit.register(self._release_at_exit)

    def prepare(self) -> None:
        self.lock.acquire()
        try:
            report = verify_container(self.root)
            if not report.ok:
                raise ContainerError(
                    "live container failed verification: " + "; ".join(report.errors)
                )
            manifest = load_manifest(self.root)
            dirty = _json_read(self.control / "dirty.json")
            if dirty:
                raise ContainerError(
                    "an interrupted live session needs recovery; run 'ngram recover <path>'"
                )
            if not _cache_matches(self.config, manifest):
                _replace_cache_from_container(self.config)
                _write_cache_meta(self.config, manifest)
            _write_dirty_marker(self.config, self.session_id, manifest)
            mounted = checkpoint_runtime_state(
                self.config,
                self.root,
                event_type="runtime_mounted",
                event_payload={"session_id": self.session_id, "host": socket.gethostname()},
            )
            _write_cache_meta(self.config, mounted)
            _write_dirty_marker(self.config, self.session_id, mounted)
            self.opened = True
        except BaseException:
            self.lock.release()
            raise

    async def checkpoint(
        self,
        event_type: str,
        event_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not self.opened:
            raise ContainerError("live container is not mounted")
        if self._checkpoint_lock is None:
            self._checkpoint_lock = asyncio.Lock()
        async with self._checkpoint_lock:
            manifest = await asyncio.to_thread(
                checkpoint_runtime_state,
                self.config,
                self.root,
                event_type=event_type,
                event_payload={"session_id": self.session_id, **(event_payload or {})},
            )
            _write_cache_meta(self.config, manifest)
            _write_dirty_marker(self.config, self.session_id, manifest)
            return manifest

    async def close(self) -> None:
        if not self.opened:
            self.lock.release()
            return
        try:
            await self.checkpoint("runtime_withdrawn", {"clean": True})
            (self.control / "dirty.json").unlink(missing_ok=True)
        finally:
            self.opened = False
            self.lock.release()

    def abort(self) -> None:
        """Release the process lock but retain the dirty marker for recovery tests/crashes."""
        self.opened = False
        self.lock.release()

    def _release_at_exit(self) -> None:
        self.lock.release()
