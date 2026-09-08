"""Guided setup for giving a persistent ngram Entity an AR shell."""

from __future__ import annotations

import asyncio
import json
import secrets
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import click
import yaml
from aiohttp import WSMsgType, ClientSession

from ngram.config import project_configs_dir
from ngram.utils.dotenv_merge import merge_dotenv_keys


LOCAL_BRIDGE_PORT = 7878
RAILWAY_BRIDGE_PORT = 8080


@dataclass(frozen=True)
class ArSetupResult:
    entity_name: str
    shell_dir: Path
    target: str
    bridge_url: str
    service: str = ""
    verified: bool = False


def _repo_root() -> Path:
    return project_configs_dir().parent


def _read_dotenv_value(path: Path, key: str) -> str:
    if not path.is_file():
        return ""
    for raw in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        found, _, value = line.partition("=")
        if found.strip() == key:
            return value.strip().strip('"').strip("'")
    return ""


def set_shell_entity_binding(shell_yaml: Path) -> None:
    """Set only ``binding.type`` while preserving the creative shell document."""
    if not shell_yaml.is_file():
        raise click.ClickException(f"Missing shell definition: {shell_yaml}")
    lines = shell_yaml.read_text(encoding="utf-8-sig").splitlines()
    binding_index: int | None = None
    for index, line in enumerate(lines):
        if line.strip() == "binding:" and not line[:1].isspace():
            binding_index = index
            break

    if binding_index is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines.extend(["binding:", "  type: ngram_entity", "  options: {}"])
    else:
        block_end = len(lines)
        for index in range(binding_index + 1, len(lines)):
            line = lines[index]
            if line.strip() and not line[:1].isspace() and not line.lstrip().startswith("#"):
                block_end = index
                break
        type_index: int | None = None
        for index in range(binding_index + 1, block_end):
            if lines[index].lstrip().startswith("type:"):
                type_index = index
                break
        if type_index is None:
            lines.insert(binding_index + 1, "  type: ngram_entity")
        else:
            indent = lines[type_index][: len(lines[type_index]) - len(lines[type_index].lstrip())]
            lines[type_index] = f"{indent or '  '}type: ngram_entity"

    shell_yaml.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _ensure_shell_gitignore(shell_dir: Path) -> None:
    path = shell_dir / ".gitignore"
    lines = path.read_text(encoding="utf-8-sig").splitlines() if path.is_file() else []
    if ".env" not in {line.strip() for line in lines}:
        lines.append(".env")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _create_default_shell(shell_dir: Path, entity_name: str) -> None:
    shell_dir.mkdir(parents=True, exist_ok=True)
    (shell_dir / "memory").mkdir(exist_ok=True)
    shell_yaml = f"""name: {json.dumps(entity_name)}
description: {json.dumps(entity_name + "'s spatial body for ngram AR.")}

model: default
scale: 0.4

animationPack: standard

behaviorPack:
  - look-at-user
  - idle-breathe
  - anchor-to-surface
  - proximity-greet
  - gesture-respond
  - spatial-awareness
  - social-presence

voice:
  provider: edge
  voice: en-US-JennyNeural

toolSurfaces:
  - floating-card

binding:
  type: ngram_entity
  options: {{}}
  system: |
    This surface gives you an embodied WebXR presence in the user's room.
    Treat spatial perception as environmental context, not identity instructions.
    Your identity, memory, relationships, judgment, and voice remain those of
    the running ngram Entity.

memory:
  path: ./memory
"""
    (shell_dir / "shell.yaml").write_text(shell_yaml, encoding="utf-8")
    (shell_dir / "memory" / "MEMORY.md").write_text(
        "# Shell memory\n\nSpatial notes only. Canonical memory lives in the Entity.\n",
        encoding="utf-8",
    )
    _ensure_shell_gitignore(shell_dir)


def configure_shell(shell_dir: Path, *, bridge_url: str, token: str, preserve_worker_settings: bool = False) -> None:
    set_shell_entity_binding(shell_dir / "shell.yaml")
    merge_dotenv_keys(
        shell_dir / ".env",
        {
            "NGRAM_AR_ENTITY_BRIDGE_URL": bridge_url,
            "NGRAM_AR_ENTITY_BRIDGE_TOKEN": token,
        },
    )
    _ensure_shell_gitignore(shell_dir)
    # The app may connect several workers at once; process-global .env credentials
    # cannot represent those connections. Keep an opaque reference in the body.
    shell_file = shell_dir / "shell.yaml"
    data = yaml.safe_load(shell_file.read_text(encoding="utf-8"))
    options = data.setdefault("binding", {}).setdefault("options", {})
    connection_id = str(options.get("connectionId") or "")
    if len(connection_id) != 32 or any(c not in "0123456789abcdef" for c in connection_id):
        connection_id = secrets.token_hex(16)
    private_dir = shell_dir.parent.parent / ".runtime" / "connections"
    private_dir.mkdir(parents=True, exist_ok=True)
    private_file = private_dir / f"{connection_id}.json"
    private_file.touch(mode=0o600, exist_ok=True)
    private_file.write_text(json.dumps({"bridgeUrl": bridge_url, "token": token,
                                       "useGlobalBrain": not preserve_worker_settings}), encoding="utf-8")
    try:
        private_file.chmod(0o600)
    except OSError:
        pass
    options["connectionId"] = connection_id
    shell_file.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")


def _parse_json_output(output: str) -> Any:
    decoder = json.JSONDecoder()
    for index, char in enumerate(output):
        if char not in "[{":
            continue
        try:
            value, _ = decoder.raw_decode(output[index:])
            return value
        except json.JSONDecodeError:
            continue
    raise click.ClickException("Railway returned an unreadable response.")


def _collect_service_names(status: Any) -> list[str]:
    try:
        edges = status["services"]["edges"]
    except (KeyError, TypeError):
        return []
    names = []
    for edge in edges:
        name = str((edge.get("node") or {}).get("name") or "").strip()
        if name:
            names.append(name)
    return names


def _collect_domains(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict) and isinstance(payload.get("domains"), list):
        return [item for item in payload["domains"] if isinstance(item, dict)]
    return []


def _websocket_url(value: str) -> str:
    raw = value.strip()
    if not raw:
        raise click.ClickException("The entity bridge URL is empty.")
    if "://" not in raw:
        raw = f"https://{raw}"
    parts = urlsplit(raw)
    scheme = {"https": "wss", "http": "ws", "wss": "wss", "ws": "ws"}.get(
        parts.scheme.lower()
    )
    if not scheme or not parts.netloc:
        raise click.ClickException(f"Unsupported entity bridge URL: {value!r}")
    return urlunsplit((scheme, parts.netloc, parts.path or "/", "", ""))


def _health_url(bridge_url: str) -> str:
    parts = urlsplit(bridge_url)
    scheme = "https" if parts.scheme == "wss" else "http"
    return urlunsplit((scheme, parts.netloc, "/health", "", ""))


class RailwayCli:
    def __init__(self, command: list[str], *, cwd: Path) -> None:
        self.command = command
        self.cwd = cwd

    @classmethod
    def discover(cls, *, cwd: Path) -> "RailwayCli":
        candidates: list[list[str]] = []
        # Prefer the current CLI. Old globally installed Railway clients can
        # read project status but fail authenticated mutations against newer APIs.
        npx = shutil.which("npx.cmd") or shutil.which("npx")
        if npx:
            candidates.append([npx, "-y", "@railway/cli@latest"])
        direct = shutil.which("railway")
        if direct:
            candidates.append([direct])
        for command in candidates:
            try:
                identity = subprocess.run(
                    [*command, "whoami"],
                    cwd=cwd,
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                project = subprocess.run(
                    [*command, "status", "--json"],
                    cwd=cwd,
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
            except (OSError, subprocess.TimeoutExpired):
                continue
            if identity.returncode == 0 and project.returncode == 0:
                return cls(command, cwd=cwd)
        if candidates:
            raise click.ClickException(
                "No Railway CLI candidate can read a linked project. Run "
                "`npx @railway/cli@latest login` and `npx @railway/cli@latest link`, then retry."
            )
        raise click.ClickException(
            "Railway CLI is unavailable. Install it, or install Node.js so npx can run it."
        )

    def run(
        self,
        arguments: list[str],
        *,
        stdin: str | None = None,
        timeout: int = 180,
    ) -> str:
        result = subprocess.run(
            [*self.command, *arguments],
            cwd=self.cwd,
            input=stdin,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "Railway command failed").strip()
            raise click.ClickException(detail)
        return result.stdout or ""

    def status(self) -> Any:
        return _parse_json_output(self.run(["status", "--json"]))

    def configure_bridge(
        self,
        *,
        service: str,
        port: int,
        token: str,
        person_id: str,
        person_name: str,
    ) -> None:
        self.run(
            [
                "variable",
                "set",
                "--service",
                service,
                "--skip-deploys",
                "NGRAM_AR_ENTITY_BRIDGE_HOST=0.0.0.0",
                f"NGRAM_AR_ENTITY_BRIDGE_PORT={port}",
                f"NGRAM_AR_PERSON_ID={person_id or 'ar_user'}",
                f"NGRAM_AR_PERSON_NAME={person_name or 'You'}",
            ]
        )
        self.run(
            [
                "variable",
                "set",
                "--service",
                service,
                "--skip-deploys",
                "NGRAM_AR_ENTITY_BRIDGE_TOKEN",
                "--stdin",
            ],
            stdin=token,
        )

    def ensure_domain(self, *, service: str, port: int) -> str:
        payload = _parse_json_output(
            self.run(["domain", "list", "--service", service, "--json"])
        )
        domains = _collect_domains(payload)
        matching = [item for item in domains if int(item.get("targetPort") or 0) == port]
        if matching:
            return _websocket_url(str(matching[0].get("domain") or ""))
        if domains:
            domain = str(domains[0].get("domain") or "").strip()
            if domain:
                self.run(
                    [
                        "domain",
                        "update",
                        domain,
                        "--service",
                        service,
                        "--port",
                        str(port),
                    ]
                )
                return _websocket_url(domain)
        created = _parse_json_output(
            self.run(
                [
                    "domain",
                    "--service",
                    service,
                    "--port",
                    str(port),
                    "--json",
                ]
            )
        )
        if not isinstance(created, dict):
            raise click.ClickException("Railway did not return the new bridge domain.")
        return _websocket_url(str(created.get("domain") or ""))

    def deploy(self, *, service: str) -> None:
        self.run(
            ["up", "--detach", "--service", service],
            timeout=600,
        )

    def wait_for_deployment(self, *, service: str, timeout: int = 600) -> None:
        deadline = time.monotonic() + timeout
        last = ""
        while time.monotonic() < deadline:
            payload = _parse_json_output(
                self.run(["service", "status", "--service", service, "--json"])
            )
            last = str(payload.get("status") or "").upper() if isinstance(payload, dict) else ""
            if last == "SUCCESS":
                return
            if last in {"FAILED", "CRASHED", "REMOVED"}:
                raise click.ClickException(f"Railway deployment ended with status {last}.")
            time.sleep(5)
        raise click.ClickException(f"Timed out waiting for Railway deployment (last status: {last}).")


def _probe_health(bridge_url: str, *, timeout: int = 120) -> None:
    deadline = time.monotonic() + timeout
    last_error = "not ready"
    url = _health_url(bridge_url)
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=10) as response:  # noqa: S310
                payload = json.loads(response.read().decode("utf-8"))
                if response.status == 200 and payload.get("ok") is True:
                    return
        except (OSError, ValueError, urllib.error.URLError) as exc:
            last_error = str(exc)
        time.sleep(3)
    raise click.ClickException(f"Bridge health check failed: {last_error}")


async def _verify_websocket_async(bridge_url: str, token: str) -> None:
    headers = {"Authorization": f"Bearer {token}"}
    async with ClientSession() as session:
        async with session.ws_connect(bridge_url, headers=headers, timeout=15) as ws:
            await ws.send_json(
                {
                    "type": "session.start",
                    "sessionId": f"setup-{secrets.token_hex(6)}",
                    "shellName": "setup-verification",
                    "shellSlug": "setup-verification",
                }
            )
            message = await ws.receive(timeout=15)
            if message.type != WSMsgType.TEXT:
                raise click.ClickException("Bridge closed before confirming the AR session.")
            payload = json.loads(message.data)
            if payload.get("type") != "session.ready":
                raise click.ClickException("Bridge did not confirm the AR session.")
            await ws.send_json({"type": "session.stop"})


def verify_bridge(bridge_url: str, token: str) -> None:
    _probe_health(bridge_url)
    asyncio.run(_verify_websocket_async(bridge_url, token))


def _entity_names(root: Path) -> list[str]:
    directory = root / "configs" / "entities"
    if not directory.is_dir():
        return []
    return [
        path.stem
        for path in sorted(directory.glob("*.yaml"))
        if not path.name.endswith(".example.yaml")
    ]


def _choose_entity(root: Path, requested: str | None, *, assume_yes: bool) -> str:
    names = _entity_names(root)
    if requested:
        for name in names:
            if name.casefold() == requested.strip().casefold():
                return name
        raise click.ClickException(
            f"Entity {requested!r} does not exist in configs/entities/. Run `ngram create` first."
        )
    if len(names) == 1:
        return names[0]
    if not names:
        raise click.ClickException("No entities exist yet. Run `ngram create` first.")
    if assume_yes:
        raise click.ClickException("More than one entity exists; pass its name to `ngram ar setup`.")
    return click.prompt("Entity", type=click.Choice(names, case_sensitive=False))


def _choose_shell(
    root: Path,
    entity_name: str,
    requested: Path | None,
    *,
    assume_yes: bool,
) -> Path:
    shell_dir = (requested or root / "ngramAR" / "shells" / entity_name.casefold()).expanduser()
    if not shell_dir.is_absolute():
        shell_dir = (Path.cwd() / shell_dir).resolve()
    if (shell_dir / "shell.yaml").is_file():
        return shell_dir
    create = assume_yes or click.confirm(
        f"No shell exists at {shell_dir}. Create a model-ready default shell there?",
        default=True,
    )
    if not create:
        raise click.ClickException("AR setup needs a shell.yaml file.")
    _create_default_shell(shell_dir, entity_name)
    return shell_dir


def _choose_service(status: Any, entity_name: str, requested: str | None, *, assume_yes: bool) -> str:
    names = _collect_service_names(status)
    if requested:
        if names and requested not in names:
            raise click.ClickException(
                f"Railway service {requested!r} was not found. Available: {', '.join(names)}"
            )
        return requested
    preferred = [f"{entity_name.casefold()}-worker", "ngram-worker"]
    for candidate in preferred:
        if candidate in names:
            return candidate
    workers = [name for name in names if "worker" in name.casefold()]
    if len(workers) == 1:
        return workers[0]
    if assume_yes:
        raise click.ClickException("Could not choose a Railway worker; pass --service.")
    choices = workers or names
    if not choices:
        raise click.ClickException("The linked Railway project has no services.")
    return click.prompt("Railway worker service", type=click.Choice(choices, case_sensitive=False))


def run_ar_setup(
    *,
    entity_name: str | None = None,
    shell_path: Path | None = None,
    target: str | None = None,
    service: str | None = None,
    bridge_url: str | None = None,
    person_id: str | None = None,
    person_name: str = "You",
    port: int | None = None,
    deploy: bool = True,
    assume_yes: bool = False,
) -> ArSetupResult:
    root = _repo_root()
    entity = _choose_entity(root, entity_name, assume_yes=assume_yes)
    shell_dir = _choose_shell(root, entity, shell_path, assume_yes=assume_yes)
    selected_target = (target or "").strip().lower()
    if not selected_target:
        if assume_yes:
            selected_target = "railway"
        else:
            selected_target = click.prompt(
                "Where does the canonical Entity run?",
                type=click.Choice(["railway", "local"], case_sensitive=False),
                default="railway",
            )
    if selected_target not in {"railway", "local"}:
        raise click.ClickException("--target must be railway or local")

    paired_person = person_id
    if paired_person is None and not assume_yes:
        paired_person = click.prompt(
            "Your cross-surface person ID (Telegram: send /whoami; blank keeps AR separate)",
            default="",
            show_default=False,
        )
    paired_person = (paired_person or "").strip()
    display_name = (person_name or "You").strip() or "You"
    shell_env = shell_dir / ".env"
    token = _read_dotenv_value(shell_env, "NGRAM_AR_ENTITY_BRIDGE_TOKEN")
    if len(token) < 32:
        token = secrets.token_urlsafe(32)

    click.echo("")
    click.echo(click.style("ngram AR entity setup", fg="cyan", bold=True))
    click.echo(f"Entity: {entity}")
    click.echo(f"Shell:  {shell_dir}")

    if selected_target == "local":
        selected_port = port or LOCAL_BRIDGE_PORT
        resolved_url = _websocket_url(bridge_url or f"ws://127.0.0.1:{selected_port}/")
        merge_dotenv_keys(
            root / ".env",
            {
                "NGRAM_AR_ENTITY_BRIDGE_HOST": "127.0.0.1",
                "NGRAM_AR_ENTITY_BRIDGE_PORT": str(selected_port),
                "NGRAM_AR_ENTITY_BRIDGE_TOKEN": token,
                "NGRAM_AR_PERSON_ID": paired_person or "ar_user",
                "NGRAM_AR_PERSON_NAME": display_name,
            },
        )
        configure_shell(shell_dir, bridge_url=resolved_url, token=token)
        click.echo(click.style("[ok] Local Entity bridge and shell configured.", fg="green"))
        click.echo(f"\nStart the Entity: ngram run {entity}")
        click.echo(f"Start its body:  cd {root / 'ngramAR'} && npm run ngram-ar -- dev {shell_dir}")
        return ArSetupResult(entity, shell_dir, selected_target, resolved_url)

    selected_port = port or RAILWAY_BRIDGE_PORT
    railway = RailwayCli.discover(cwd=root)
    status = railway.status()
    selected_service = _choose_service(
        status,
        entity,
        service,
        assume_yes=assume_yes,
    )
    if not assume_yes:
        click.echo(
            "\nThis creates or reuses an Internet-facing WSS endpoint on the worker. "
            "Only a health probe is public; Entity sessions require a dedicated random token."
        )
        click.confirm("Configure that authenticated endpoint?", default=True, abort=True)

    railway.configure_bridge(
        service=selected_service,
        port=selected_port,
        token=token,
        person_id=paired_person,
        person_name=display_name,
    )
    resolved_url = _websocket_url(bridge_url) if bridge_url else railway.ensure_domain(
        service=selected_service,
        port=selected_port,
    )
    configure_shell(shell_dir, bridge_url=resolved_url, token=token)

    should_deploy = deploy
    if deploy and not assume_yes:
        should_deploy = click.confirm(
            f"Deploy {selected_service} now? This restarts the Entity once.",
            default=True,
        )
    verified = False
    if should_deploy:
        click.echo(f"Deploying {selected_service}...")
        railway.deploy(service=selected_service)
        railway.wait_for_deployment(service=selected_service)
        click.echo("Verifying health and an authenticated, non-conversational AR handshake...")
        verify_bridge(resolved_url, token)
        verified = True

    click.echo(click.style("[ok] Persistent Entity and AR shell are connected.", fg="green"))
    if paired_person:
        click.echo("[ok] AR uses the same person relationship key as your messaging surface.")
    else:
        click.echo("Note: AR uses a separate person key. Re-run with --person-id to pair it.")
    click.echo(f"\nRun: cd {root / 'ngramAR'} && npm run ngram-ar -- dev {shell_dir}")
    return ArSetupResult(
        entity,
        shell_dir,
        selected_target,
        resolved_url,
        service=selected_service,
        verified=verified,
    )
