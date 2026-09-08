"""Persistent Blender projects on the existing execution host, never the inference host.

This has the same trust boundary as run_command/run_code. Agent Python is not sandboxed.
The renderer receives only immutable artifacts; it never executes Blender Python.
"""

from __future__ import annotations

import atexit
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import threading
import time
import uuid
from typing import Any

from ngram.presence.tools.blender_visuals import render_options

PROTOCOL = "ngram.blender/1"
MARKER = "NGRAM_BLENDER:"
MAX_PREVIEW_BYTES = 32 * 1024 * 1024
_managers: dict[Path, "BlenderRuntime"] = {}
_manager_lock = threading.Lock()


def project_id(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", value):
        raise ValueError("Invalid Blender project ID")
    return value


def runtime_for(workspace: Path) -> "BlenderRuntime":
    root = workspace.resolve()
    with _manager_lock:
        if root not in _managers:
            _managers[root] = BlenderRuntime(root)
        return _managers[root]


class BlenderRuntime:
    def __init__(self, workspace: Path):
        self.workspace = workspace.resolve()
        self.root = self.workspace / ".ngram" / "blender"
        self.projects: dict[str, _Project] = {}
        self.lock = threading.RLock()

    def directory(self, ident: str) -> Path:
        path = (self.root / project_id(ident)).resolve()
        if not path.is_relative_to(self.workspace):
            raise ValueError("Blender project path escapes workspace")
        return path

    def project(self, ident: str) -> "_Project":
        with self.lock:
            if ident not in self.projects:
                directory = self.directory(ident)
                if not (directory / "project.json").is_file():
                    raise ValueError("Unknown Blender project")
                self.projects[ident] = _Project(directory)
            return self.projects[ident]

    def artifact(self, ident: str, revision: int, name: str) -> Path:
        if type(revision) is not int or revision < 1 or name not in {"preview.glb", "project.blend"}:
            raise ValueError("Invalid Blender artifact")
        directory = self.directory(ident)
        path = (directory / "revisions" / str(revision) / name).resolve()
        if not path.is_relative_to(directory) or not path.is_file():
            raise ValueError("Blender artifact unavailable")
        # Only completed revisions are published, including after interrupted writes.
        if not (path.parent / "snapshot.json").is_file():
            raise ValueError("Blender revision is not published")
        return path

    def command(self, payload: dict[str, Any]) -> dict[str, Any]:
        command = payload.get("command", "capabilities")
        if command == "capabilities":
            projects = []
            candidates = [str(payload.get("executable") or os.environ.get("NGRAM_BLENDER_EXECUTABLE") or "blender")]
            for meta in sorted(self.root.glob("*/project.json"))[:128]:
                try:
                    data = json.loads(meta.read_text(encoding="utf-8"))
                    projects.append({"project_id": meta.parent.name, "name": data.get("name")})
                    if data.get("executable"):
                        candidates.append(str(data["executable"]))
                except (OSError, ValueError):
                    continue
            # Portable installs on the execution host need not be on PATH.
            candidates.extend(str(path) for path in sorted((self.workspace / "tools").glob("blender*/blender")))
            executable = next((resolved for candidate in candidates if (resolved := shutil.which(candidate))), None)
            return {"ok": True, "protocol": PROTOCOL, "installed": bool(executable),
                    "executable": executable, "workspace": str(self.root),
                    "projects": projects,
                    "commands": ["create", "execute", "publish", "render", "status", "stop"],
                    "help": "Install Blender on this execution host with your shell tools if missing. "
                    "create accepts name, optional project_id and workspace-relative blend_file. "
                    "execute accepts project_id, source (Python with bpy), optional executable path, "
                    "request_id, timeout (seconds). One persistent process per active project. "
                    "Successful scripts automatically save .blend and publish GLB. Call publish() "
                    "inside a long script to show intermediate steps; keep units in metres. "
                    "Use result = JSON-compatible-data to return inspection results. "
                    "render accepts project_id, executable?, timeout?, options {objects:[names], camera:name, "
                    "position:[x,y,z], look_at:[x,y,z], orbit:[azimuth,elevation] degrees, distance:metres, "
                    "size:256..1536, samples:1..256, style:scene|studio|clay, projection:perspective|orthographic}. "
                    "It returns actual images without changing the published model. Call render_view(**options) "
                    "inside execute to inspect up to four views of your working scene. Coordinates are Blender Z-up. "
                    "Stop kills running work; the last published checkpoint survives. "
                    "This is trusted execution-host Python, with the same access as shell tools."}
        if command == "create":
            ident = project_id(payload.get("project_id") or uuid.uuid4().hex[:16])
            directory = self.directory(ident)
            with self.lock:
                if directory.exists():
                    raise ValueError("Project already exists; use execute to continue editing it")
                source = payload.get("blend_file")
                path = None
                if source:
                    path = (self.workspace / str(source)).resolve()
                    if not path.is_relative_to(self.workspace) or not path.is_file() or path.suffix.lower() != ".blend":
                        raise ValueError("blend_file must be a .blend file inside the execution workspace")
                directory.mkdir(parents=True)
                if path:
                    shutil.copy2(path, directory / "source.blend")
                (directory / "project.json").write_text(json.dumps({
                    "project_id": ident, "name": str(payload.get("name") or "Untitled")[:120],
                    "protocol": PROTOCOL,
                }), encoding="utf-8")
            return self.project(ident).status()
        if command == "status" and not payload.get("project_id"):
            return {"ok": True, "projects": [self.project(p.parent.name).status()
                    for p in self.root.glob("*/project.json")]}
        p = self.project(project_id(payload.get("project_id")))
        if command == "status":
            return p.status()
        if command == "stop":
            p.stop(job_id=payload.get("request_id"))
            return p.status()
        if command in {"execute", "publish"}:
            return p.execute({**payload, "source": "" if command == "publish" else payload.get("source")})
        if command == "render":
            options = render_options(payload.get("options"))
            return p.execute({**payload, "source": "result = render_view(**" + repr(options) + ")", "publish": False})
        if command == "artifact":
            if payload.get("render_id"):
                ident = payload["render_id"]
                if not isinstance(ident, str) or not re.fullmatch(r"[a-f0-9]{32}", ident):
                    raise ValueError("Invalid render ID")
                path = p.directory / "renders" / ident / "view.jpg"
                if not (path.parent / "render.json").is_file() or not path.is_file():
                    raise ValueError("Render is not complete")
            else:
                path = self.artifact(p.ident, payload.get("revision"), payload.get("name"))
            offset = payload.get("offset", 0)
            if type(offset) is not int or offset < 0:
                raise ValueError("Invalid artifact offset")
            with path.open("rb") as stream:
                stream.seek(offset)
                data = stream.read(512 * 1024)
            return {"ok": True, "size": path.stat().st_size, "offset": offset,
                    "data": base64.b64encode(data).decode("ascii")}
        raise ValueError("Unknown Blender command")

    def close(self):
        for p in list(self.projects.values()):
            p.stop()


class _Project:
    def __init__(self, directory: Path):
        self.directory = directory
        self.meta = json.loads((directory / "project.json").read_text(encoding="utf-8"))
        self.ident = self.meta["project_id"]
        self.process: subprocess.Popen | None = None
        self.lock = threading.RLock()
        self.state = "stopped"
        self.job_id: str | None = None
        self.fingerprint: str | None = None
        self.error: str | None = None
        self.output = ""
        self.result: Any = None
        self.renders: list[dict[str, Any]] = []
        self.generation = 0
        self.updated = time.time()

    def status(self) -> dict[str, Any]:
        with self.lock:
            snapshot = None
            latest = self.directory / "latest.json"
            if latest.is_file():
                snapshot = json.loads(latest.read_text(encoding="utf-8"))
            return {"ok": True, **self.meta, "state": self.state, "job_id": self.job_id,
                    "error": self.error, "output": self.output[-12000:], "result": self.result, "renders": self.renders,
                    "snapshot": snapshot, "updated": self.updated,
                    "project_file": str(self.directory / "revisions" / str(snapshot["revision"]) / "project.blend") if snapshot else None}

    def execute(self, payload: dict[str, Any]) -> dict[str, Any]:
        source = payload.get("source")
        if not isinstance(source, str) or len(source.encode()) > 512 * 1024:
            raise ValueError("source must be Python text under 512 KB")
        timeout = float(payload.get("timeout") or 1800)
        if not 1 <= timeout <= 86400:
            raise ValueError("timeout must be 1..86400 seconds")
        request_id = str(payload.get("request_id") or uuid.uuid4().hex)
        publish_result = payload.get("publish", True) is not False
        fingerprint = hashlib.sha256((source + str(publish_result)).encode()).hexdigest()
        with self.lock:
            if request_id == self.job_id:
                if fingerprint != self.fingerprint:
                    raise ValueError("request_id was already used for different source")
                return self.status()
            if self.state == "working":
                raise ValueError("Blender project is busy; wait for this edit or stop it first")
            executable = str(payload.get("executable") or os.environ.get("NGRAM_BLENDER_EXECUTABLE") or self.meta.get("executable") or "blender")
            resolved = shutil.which(executable)
            if not resolved:
                raise ValueError("Blender is not installed on the execution host. Install it with your shell tools, then retry; alternatively pass executable with its full path.")
            self.state, self.error, self.output, self.result = "working", None, "", None
            self.renders = []
            self.meta["executable"] = resolved
            (self.directory / "project.json").write_text(json.dumps(self.meta), encoding="utf-8")
            self.job_id, self.fingerprint = request_id, fingerprint
            self.generation += 1
            generation = self.generation
            self.updated = time.time()
            scripts = self.directory / "scripts"
            scripts.mkdir(exist_ok=True)
            (scripts / f"{uuid.uuid4().hex}.py").write_text(source, encoding="utf-8")
            threading.Thread(target=self._run, args=(resolved, source, generation, timeout, publish_result), daemon=True).start()
            return self.status()

    def _run(self, executable: str, source: str, generation: int, timeout: float, publish_result: bool = True):
        timer = threading.Timer(timeout, lambda: self.stop("Blender edit timed out", generation))
        timer.daemon = True
        timer.start()
        try:
            with self.lock:
                if generation != self.generation:
                    return
                if self.process is None or self.process.poll() is not None:
                    runner = Path(__file__).with_name("blender_worker.py")
                    options: dict[str, Any] = {"start_new_session": True} if os.name != "nt" else {"creationflags": subprocess.CREATE_NO_WINDOW}
                    self.process = subprocess.Popen(
                        [executable, "--background", "--factory-startup", "--disable-autoexec", "--python", str(runner), "--", str(self.directory)],
                        cwd=self.directory, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", **options,
                    )
                process = self.process
            # A large write can block while Blender starts. Stop must still be
            # able to acquire the lock and terminate that process immediately.
            process.stdin.write(json.dumps({"source": source, "publish": publish_result}) + "\n")
            process.stdin.flush()
            for line in process.stdout:
                with self.lock:
                    if generation != self.generation:
                        return
                    index = line.find(MARKER)
                    if index < 0:
                        self.output = (self.output + line)[-12000:]
                        continue
                    message = json.loads(line[index + len(MARKER):])
                    self.updated = time.time()
                    if message["type"] == "result":
                        self.state = "ready" if message.get("ok") else "failed"
                        self.error, self.result = message.get("error"), message.get("result")
                        self.renders = message.get("renders", [])[-4:]
                        if self.error:
                            # Discard partial, uncheckpointed edits before another script runs.
                            self._kill()
                        return
            raise RuntimeError(f"Blender exited before returning a result ({process.poll()})")
        except Exception as exc:
            with self.lock:
                if generation == self.generation:
                    self.state, self.error = "failed", str(exc)[:2000]
                    self._kill()
        finally:
            timer.cancel()

    def _kill(self):
        process = self.process
        self.process = None
        if process and process.poll() is None:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
            else:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            process.wait(timeout=5)

    def stop(self, error: str | None = None, generation: int | None = None, job_id: str | None = None):
        with self.lock:
            if job_id is not None and job_id != self.job_id:
                return
            if generation is not None and generation != self.generation:
                return
            self.generation += 1
            self._kill()
            self.state, self.error = ("failed" if error else "stopped"), error
            self.updated = time.time()


@atexit.register
def _close_runtimes():
    for manager in list(_managers.values()):
        manager.close()
