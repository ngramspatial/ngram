"""CLI entry for runtime, portable-container, setup, and gateway commands."""

from __future__ import annotations

import asyncio
import json
import os
import shlex
import shutil
import signal
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

import click

from ngram.__version__ import __version__ as NGRAM_VERSION
from ngram.config import (
    EntityConfig,
    load_entity_config,
    load_harness_config,
    load_live_container_config,
    project_configs_dir,
    validate_entity_env,
)
from ngram.entity import Entity
from ngram.models import Input
from ngram.presence.platforms.base import Platform
from ngram.utils.gateway_script import (
    gateway_script_available,
)
from ngram.utils.gateway_script import (
    run_gateway_script as _run_gateway_script_subprocess,
)
from ngram.utils.log import setup_logging
from ngram.utils.ollama_bootstrap import shutdown_spawned_ollama
from ngram.utils.repo_dotenv import load_repo_dotenv
from ngram.utils.self_update import perform_self_update

if TYPE_CHECKING:
    from ngram.presence.daemon import PresenceDaemon


def _mask_database_url(url: str) -> str:
    """Redact password for stderr logging."""
    try:
        p = urlsplit(url)
        if p.scheme not in ("postgresql", "postgres"):
            return url
        host = p.hostname or ""
        port = f":{p.port}" if p.port else ""
        user = p.username or ""
        tail = (p.path or "") + (f"?{p.query}" if p.query else "")
        if user:
            return f"{p.scheme}://{user}:***@{host}{port}{tail}"
        return f"{p.scheme}://***@{host}{port}{tail}"
    except Exception:
        return "postgresql://…"


def _apply_cli_database_url(cli_value: str | None) -> None:
    """Ensure DATABASE_URL is set before Entity() when the user passes --database-url."""
    if cli_value and str(cli_value).strip():
        os.environ["DATABASE_URL"] = str(cli_value).strip()


def _echo_cli_database_target(ec: EntityConfig) -> None:
    """Print which store the CLI will use (local SQLite vs Postgres)."""
    u = (ec.database_url() or "").strip()
    if u.startswith("postgresql://") or u.startswith("postgres://"):
        click.echo(f"Database: {_mask_database_url(u)}", err=True)
    else:
        click.echo(f"Database: sqlite {Path(ec.db_path()).expanduser()}", err=True)


def _warn_if_hybrid_without_postgres(ec: EntityConfig) -> None:
    mode = (ec.harness.deployment.mode or "").strip().lower()
    u = (ec.database_url() or "").strip()
    if mode == "hybrid_railway" and not (
        u.startswith("postgresql://") or u.startswith("postgres://")
    ):
        click.echo(
            "Warning: harness deployment.mode is hybrid_railway but DATABASE_URL is not set — "
            "this command uses local SQLite, not Railway Postgres. "
            "Set DATABASE_URL (same as the worker) or pass --database-url.",
            err=True,
        )


def _async(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


KNOWLEDGE_STARTER_TEMPLATE = """## [locked] about yourself
write anything you want the entity to know about itself.
(locked sections can't be edited by the entity — only by you)

## [locked] about your creator
who made you? what do you know about them?

## things you care about
topics, opinions, facts — whatever matters.
(the entity can edit unlocked sections like this one)
"""


def _app_version() -> str:
    """Harness version (see ``ngram/__version__.py``)."""
    return NGRAM_VERSION


def _load_repo_dotenv() -> None:
    load_repo_dotenv(anchor=Path(__file__))


async def _wait_until_stopped() -> None:
    """Block until SIGINT (Ctrl+C) or SIGTERM where available."""
    loop = asyncio.get_running_loop()
    stop = asyncio.Event()

    def _schedule_stop(*_args: object) -> None:
        try:
            loop.call_soon_threadsafe(stop.set)
        except RuntimeError:
            pass

    prev_sigint: object | None = None
    prev_sigterm: object | None = None
    used_asyncio_sigint = False
    try:
        loop.add_signal_handler(signal.SIGINT, _schedule_stop)
        used_asyncio_sigint = True
    except (NotImplementedError, RuntimeError):
        prev_sigint = signal.signal(signal.SIGINT, _schedule_stop)
    if hasattr(signal, "SIGTERM"):
        try:
            loop.add_signal_handler(signal.SIGTERM, _schedule_stop)
        except (NotImplementedError, RuntimeError):
            try:
                prev_sigterm = signal.signal(signal.SIGTERM, _schedule_stop)
            except ValueError:
                pass
    try:
        await stop.wait()
    finally:
        if used_asyncio_sigint:
            try:
                loop.remove_signal_handler(signal.SIGINT)
            except (NotImplementedError, RuntimeError):
                pass
        elif prev_sigint is not None:
            signal.signal(signal.SIGINT, prev_sigint)
        if hasattr(signal, "SIGTERM"):
            try:
                loop.remove_signal_handler(signal.SIGTERM)
            except (NotImplementedError, RuntimeError, ValueError):
                pass
            if prev_sigterm is not None:
                try:
                    signal.signal(signal.SIGTERM, prev_sigterm)
                except ValueError:
                    pass


async def _graceful_run_shutdown(
    daemon: PresenceDaemon | None,
    platforms: list,
    ent: Entity,
    *,
    ngram_ar_bridge_runner: object | None = None,
) -> None:
    """Best-effort cleanup when the loop is cancelling (Ctrl+C); shield so close() can finish."""
    from ngram.utils.ollama_bootstrap import shutdown_spawned_ollama

    if daemon is not None:
        try:
            await asyncio.shield(daemon.stop())
        except BaseException:
            pass
    for p in platforms:
        try:
            await asyncio.shield(p.disconnect())
        except BaseException:
            pass
    if ngram_ar_bridge_runner is not None:
        try:
            cleanup = getattr(ngram_ar_bridge_runner, "cleanup", None)
            if cleanup is not None:
                await asyncio.shield(cleanup())
        except BaseException:
            pass
    try:
        await asyncio.shield(ent.shutdown())
    except BaseException:
        pass
    shutdown_spawned_ollama()


async def _start_ngram_ar_bridge(ent: Entity) -> object | None:
    """Start WebSocket bridge for ngram AR (Mode B) when NGRAM_AR_ENTITY_BRIDGE_PORT is set."""
    from aiohttp import web

    from ngram.ngram_ar.bridge_server import bridge_host, bridge_port, create_bridge_app

    port = bridge_port()
    if port is None:
        return None
    app = await create_bridge_app(ent)
    runner = web.AppRunner(app)
    await runner.setup()
    try:
        site = web.TCPSite(runner, bridge_host(), port)
        await site.start()
    except BaseException:
        await runner.cleanup()
        raise
    host = bridge_host()
    display = f"[{host}]" if (":" in host and host.count(":") > 1) else host
    click.echo(f"ngram AR entity bridge: ws://{display}:{port}/")
    if (os.environ.get("NGRAM_AR_ENTITY_BRIDGE_TOKEN") or "").strip():
        click.echo(
            "  Bearer-token auth enabled — set NGRAM_AR_ENTITY_BRIDGE_TOKEN in the ngram AR shell environment",
            err=True,
        )
    return runner


class _OneShotCLIPlatform(Platform):
    """Minimal CLI platform for single-turn prompts."""

    def __init__(self, *, person_id: str = "cli_user", person_name: str = "You") -> None:
        self._person_id = person_id
        self._person_name = person_name
        self._cb = None

    async def connect(self) -> None:
        return None

    async def send_message(self, channel: str, content: str) -> None:
        click.echo(content)

    async def send_tool_activity(self, description: str) -> None:
        return None

    async def on_message(self, callback) -> None:
        self._cb = callback

    async def set_presence(self, status: str) -> None:
        return None

    async def disconnect(self) -> None:
        return None

    def get_person_id(self, message: object) -> str:
        return self._person_id

    def get_person_name(self, message: object) -> str:
        return self._person_name


def _prepare_ollama_cli(entity_name: str, with_ollama: bool, pull_models: bool) -> None:
    if not with_ollama and not pull_models:
        return

    from ngram.utils.ollama_bootstrap import bootstrap_stack

    harness = load_harness_config()
    ec = load_entity_config(entity_name, harness)
    if with_ollama:
        bootstrap_stack(ec, info=click.echo, pull_models=pull_models)
    else:
        from ngram.utils.ollama_bootstrap import pull_required_models

        pull_required_models(ec, click.echo)


def _run_gateway_script(action: str, extra_args: list[str] | None = None) -> None:
    rc = _run_gateway_script_subprocess(action, extra_args)
    if rc != 0:
        raise click.ClickException(f"gateway {action} failed with exit code {rc}.")


@click.group()
@click.version_option(version=NGRAM_VERSION, prog_name="ngram")
def cli() -> None:
    # Runs before every subcommand; keep in sync with ``main()`` for entry points that call ``cli`` only.
    _load_repo_dotenv()


@cli.command("setup")
@click.option(
    "--profile",
    type=click.Choice(["ask", "local", "hosted", "hybrid"], case_sensitive=False),
    default="ask",
    show_default=True,
    help=(
        "local = Ollama on this machine; hosted = API brain with the runtime on this "
        "machine; hybrid = advanced home-gateway + Railway deployment."
    ),
)
@click.option(
    "--gateway-host",
    default="127.0.0.1",
    show_default=True,
    hidden=True,
    help="Host the inference gateway listens on (tunnel ingress must point here).",
)
@click.option(
    "--gateway-port",
    default=8010,
    show_default=True,
    type=int,
    hidden=True,
    help="Port for the inference gateway (used when writing cloudflared config).",
)
@click.option(
    "--skip-tunnel-bootstrap",
    is_flag=True,
    default=False,
    hidden=True,
    help="Do not offer automated cloudflared tunnel + DNS + config.yml.",
)
@click.option(
    "--mode",
    "legacy_mode",
    type=click.Choice(["ask", "quick", "full"], case_sensitive=False),
    default=None,
    hidden=True,
    help="Deprecated: use --profile. quick->local, full->hybrid.",
)
def cmd_setup(
    profile: str,
    legacy_mode: str | None,
    gateway_host: str,
    gateway_port: int,
    skip_tunnel_bootstrap: bool,
) -> None:
    """Interactive local-first setup; Cloudflare is needed only for advanced hybrid mode."""
    from ngram.setup_wizard import run_setup_wizard

    resolved = profile
    if legacy_mode == "quick":
        resolved = "local"
    elif legacy_mode == "full":
        resolved = "hybrid"
    elif legacy_mode == "ask":
        resolved = "ask"
    run_setup_wizard(
        profile=resolved,
        gateway_host=gateway_host,
        gateway_port=gateway_port,
        skip_tunnel_bootstrap=skip_tunnel_bootstrap,
    )


@cli.group("ar")
def cmd_ar() -> None:
    """Connect persistent entities to embodied AR shells."""


@cmd_ar.command("setup")
@click.argument("entity_name", required=False)
@click.option(
    "--shell",
    "shell_path",
    type=click.Path(path_type=Path),
    help="Shell directory containing shell.yaml (defaults to ngramAR/shells/<entity>).",
)
@click.option(
    "--target",
    type=click.Choice(["railway", "local"], case_sensitive=False),
    default=None,
    help="Where the canonical Entity process runs.",
)
@click.option("--service", help="Railway worker service (auto-detected when possible).")
@click.option(
    "--bridge-url",
    help="Existing ws(s):// bridge URL; Railway creates a service domain when omitted.",
)
@click.option(
    "--person-id",
    help="Canonical person key shared with Telegram/Discord (Telegram: send /whoami).",
)
@click.option("--person-name", default="You", show_default=True)
@click.option("--port", type=click.IntRange(1, 65535), default=None)
@click.option("--no-deploy", is_flag=True, help="Write configuration without redeploying Railway.")
@click.option(
    "-y", "--yes", "assume_yes", is_flag=True, help="Accept safe defaults non-interactively."
)
def cmd_ar_setup(
    entity_name: str | None,
    shell_path: Path | None,
    target: str | None,
    service: str | None,
    bridge_url: str | None,
    person_id: str | None,
    person_name: str,
    port: int | None,
    no_deploy: bool,
    assume_yes: bool,
) -> None:
    """Pair one Entity, one human relationship, and one replaceable AR body."""
    from ngram.ngram_ar.setup import run_ar_setup

    run_ar_setup(
        entity_name=entity_name,
        shell_path=shell_path,
        target=target,
        service=service,
        bridge_url=bridge_url,
        person_id=person_id,
        person_name=person_name,
        port=port,
        deploy=not no_deploy,
        assume_yes=assume_yes,
    )


@cli.command("create")
def cmd_create() -> None:
    """Launch interactive entity wizard."""
    from ngram.genesis import creator

    creator.run_wizard()


@cli.group("entity")
def cmd_entity() -> None:
    """Model-free entity factory and lifecycle inspection."""


@cmd_entity.command("create")
@click.argument("name")
@click.option(
    "--destination",
    type=click.Path(path_type=Path),
    help="YAML path or directory; defaults to configs/entities.",
)
@click.option(
    "--archetype",
    type=click.Choice(["curious", "gentle", "guardian", "sardonic"]),
    default="curious",
    show_default=True,
)
@click.option("--model", help="Model identifier; defaults to the harness deliberate model.")
@click.option(
    "--surface",
    type=click.Choice(["cli", "telegram", "discord"]),
    default="cli",
    show_default=True,
)
@click.option("--force", is_flag=True, help="Replace an existing declaration at the target path.")
def cmd_entity_create(
    name: str,
    destination: Path | None,
    archetype: str,
    model: str | None,
    surface: str,
    force: bool,
) -> None:
    """Create a safe entity declaration without initializing inference."""
    from ngram.entity_factory import EntityFactoryError, create_entity_config

    target = destination or (project_configs_dir() / "entities")
    try:
        result = create_entity_config(
            name,
            target,
            archetype=archetype,
            model=model,
            surface=surface,
            force=force,
        )
    except EntityFactoryError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Created entity declaration: {result}")
    click.echo(f"Next: ngram entity validate {result}")


@cmd_entity.command("validate")
@click.argument("entity_ref")
@click.option("--json", "as_json", is_flag=True, help="Emit a machine-readable report.")
def cmd_entity_validate(entity_ref: str, as_json: bool) -> None:
    """Validate schema, naming, surfaces, and secret hygiene."""
    from ngram.entity_factory import validate_entity
    from ngram.entity_factory.service import dumps

    report = validate_entity(entity_ref)
    if as_json:
        click.echo(dumps(report.as_dict()))
    else:
        for warning in report.warnings:
            click.echo(f"Warning: {warning}", err=True)
        for error in report.errors:
            click.echo(f"Error: {error}", err=True)
        if report.ok:
            click.echo(f"Valid entity {report.entity_key}: {report.display_name}")
    if not report.ok:
        raise click.ClickException("entity validation failed")


@cmd_entity.command("manifest")
@click.argument("entity_ref")
def cmd_entity_manifest(entity_ref: str) -> None:
    """Print the secret-free canonical entity declaration."""
    from ngram.entity_factory import EntityFactoryError, build_entity_manifest
    from ngram.entity_factory.service import dumps

    try:
        click.echo(dumps(build_entity_manifest(entity_ref)))
    except EntityFactoryError as exc:
        raise click.ClickException(str(exc)) from exc


@cmd_entity.command("plan")
@click.argument("entity_ref")
@click.option(
    "--provider",
    type=click.Choice(["railway"]),
    default="railway",
    show_default=True,
)
def cmd_entity_plan(entity_ref: str, provider: str) -> None:
    """Print a secret-free provider plan without mutating infrastructure."""
    from ngram.entity_factory import EntityFactoryError, build_deployment_plan
    from ngram.entity_factory.service import dumps

    try:
        click.echo(dumps(build_deployment_plan(entity_ref, provider=provider)))
    except EntityFactoryError as exc:
        raise click.ClickException(str(exc)) from exc


@cmd_entity.command("status")
@click.argument("entity_ref")
def cmd_entity_status(entity_ref: str) -> None:
    """Inspect declaration readiness without loading a model or contacting a provider."""
    from ngram.entity_factory import entity_status
    from ngram.entity_factory.service import dumps

    status = entity_status(entity_ref)
    click.echo(dumps(status))
    if not status["validation"]["ok"]:
        raise click.ClickException("entity is not ready")


def _entity_change_protocol(entity_ref: str):
    from ngram.governance import ChangeProtocol

    harness = load_harness_config()
    return ChangeProtocol.from_config(load_entity_config(entity_ref, harness))


@cli.group("change")
def cmd_change() -> None:
    """Propose and inspect durable entity changes without loading a model."""


@cmd_change.command("propose")
@click.argument("entity_ref")
@click.argument("changes_file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--title", required=True)
@click.option("--reason", required=True)
@click.option("--expected-effect", required=True)
@click.option("--rollback-plan", required=True)
@click.option("--tier", type=click.Choice(["routine", "material", "fundamental"]))
@click.option("--operator", "operator_id", required=True, help="Auditable operator identity.")
def cmd_change_propose(
    entity_ref: str,
    changes_file: Path,
    title: str,
    reason: str,
    expected_effect: str,
    rollback_plan: str,
    tier: str | None,
    operator_id: str,
) -> None:
    """Create an immutable proposal from a JSON array of field deltas."""
    from ngram.governance import ChangeProtocolError

    try:
        changes = json.loads(changes_file.read_text(encoding="utf-8"))
        if not isinstance(changes, list):
            raise ChangeProtocolError("changes file must contain a JSON array")
        state = _entity_change_protocol(entity_ref).propose(
            title=title,
            reason=reason,
            expected_effect=expected_effect,
            rollback_plan=rollback_plan,
            changes=changes,
            operator_id=operator_id,
            requested_tier=tier,
        )
    except (OSError, json.JSONDecodeError, ChangeProtocolError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(state.as_dict(), ensure_ascii=False, sort_keys=True, indent=2))


@cmd_change.command("list")
@click.argument("entity_ref")
@click.option("--status", default="")
def cmd_change_list(entity_ref: str, status: str) -> None:
    """List newest change proposals and their derived blocking state."""
    from ngram.governance import ChangeProtocolError

    try:
        rows = _entity_change_protocol(entity_ref).list(status=status)
    except ChangeProtocolError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(
        json.dumps(
            [row.as_dict(include_events=False) for row in rows],
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
    )


@cmd_change.command("show")
@click.argument("entity_ref")
@click.argument("proposal_id")
def cmd_change_show(entity_ref: str, proposal_id: str) -> None:
    """Show the exact proposal and its complete hash-linked event history."""
    from ngram.governance import ChangeProtocolError

    try:
        state = _entity_change_protocol(entity_ref).inspect(proposal_id)
    except ChangeProtocolError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(state.as_dict(), ensure_ascii=False, sort_keys=True, indent=2))


@cmd_change.command("respond")
@click.argument("entity_ref")
@click.argument("proposal_id")
@click.argument("response")
@click.option("--operator", "operator_id", required=True, help="Auditable operator identity.")
def cmd_change_respond(
    entity_ref: str,
    proposal_id: str,
    response: str,
    operator_id: str,
) -> None:
    """Durably answer an entity objection; this does not erase or clear it."""
    from ngram.governance import ChangeProtocolError

    try:
        state = _entity_change_protocol(entity_ref).respond(
            proposal_id,
            response,
            operator_id=operator_id,
        )
    except ChangeProtocolError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(state.as_dict(), ensure_ascii=False, sort_keys=True, indent=2))


@cmd_change.command("withdraw")
@click.argument("entity_ref")
@click.argument("proposal_id")
@click.argument("reason")
@click.option("--operator", "operator_id", required=True, help="Auditable operator identity.")
def cmd_change_withdraw(
    entity_ref: str,
    proposal_id: str,
    reason: str,
    operator_id: str,
) -> None:
    """Withdraw a pending proposal without changing the entity."""
    from ngram.governance import ChangeProtocolError

    try:
        state = _entity_change_protocol(entity_ref).withdraw(
            proposal_id,
            reason,
            operator_id=operator_id,
        )
    except ChangeProtocolError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(state.as_dict(), ensure_ascii=False, sort_keys=True, indent=2))


@cmd_change.command("verify")
@click.argument("entity_ref")
def cmd_change_verify(entity_ref: str) -> None:
    """Verify the entity ID, hashes, links, and state-machine history."""
    from ngram.governance import ChangeProtocolError

    try:
        report = _entity_change_protocol(entity_ref).verify()
    except ChangeProtocolError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))


@cli.command("init")
@click.argument("entity_name")
@click.argument("destination", required=False, type=click.Path(path_type=Path))
def cmd_init(entity_name: str, destination: Path | None) -> None:
    """Migrate an existing runtime entity into an open portable ngram directory."""
    from ngram.container import ContainerError, migrate_legacy_entity

    harness = load_harness_config()
    ec = load_entity_config(entity_name, harness)
    target = destination or (Path.cwd() / f"{entity_name}.ngram")
    if target.suffix.lower() != ".ngram":
        target = target.with_name(target.name + ".ngram")
    source_yaml = project_configs_dir() / "entities" / f"{entity_name}.yaml"
    try:
        result = migrate_legacy_entity(
            ec,
            source_yaml,
            target,
            runtime_version=NGRAM_VERSION,
        )
    except ContainerError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Initialized portable ngram directory: {result}")
    click.echo(
        "Warning: this development profile is integrity-checked but not encrypted or signed.",
        err=True,
    )


@cli.command("manifest")
@click.argument("artifact", type=click.Path(exists=True, path_type=Path))
def cmd_manifest(artifact: Path) -> None:
    """Print the manifest from an open directory or .ngram archive."""
    from ngram.container import ContainerError, load_artifact_manifest

    try:
        manifest = load_artifact_manifest(artifact)
    except ContainerError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2))


@cli.command("verify")
@click.argument("artifact", type=click.Path(exists=True, path_type=Path))
def cmd_verify(artifact: Path) -> None:
    """Verify domain structure and deterministic integrity for an ngram artifact."""
    from ngram.container import ContainerError, verify_artifact

    try:
        report = verify_artifact(artifact)
    except ContainerError as exc:
        raise click.ClickException(str(exc)) from exc
    for warning in report.warnings:
        click.echo(f"Warning: {warning}", err=True)
    if not report.ok:
        for error in report.errors:
            click.echo(f"Error: {error}", err=True)
        raise click.ClickException("ngram verification failed")
    click.echo(f"Verified {report.files_checked} files: {report.actual_root}")


@cli.command("open")
@click.argument("archive", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.argument("destination", required=False, type=click.Path(path_type=Path))
def cmd_open(archive: Path, destination: Path | None) -> None:
    """Open a portable .ngram archive into a verified canonical directory."""
    from ngram.container import ContainerError, extract_archive

    target = destination or archive.with_name(f"{archive.stem}.opened.ngram")
    if target.suffix.lower() != ".ngram":
        target = target.with_name(target.name + ".ngram")
    try:
        result = extract_archive(archive, target)
    except ContainerError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Opened portable ngram directory: {result}")
    click.echo(
        "Warning: contents are not encrypted; filesystem permissions control access.",
        err=True,
    )


@cli.command("recover")
@click.argument(
    "container_path",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
)
def cmd_recover(container_path: Path) -> None:
    """Reconcile a derived cache after an interrupted local container session."""
    from ngram.config import load_live_container_config
    from ngram.container import ContainerError, recover_interrupted_container

    try:
        config = load_live_container_config(container_path, verify=False)
        manifest = recover_interrupted_container(config)
    except ContainerError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(
        f"Recovered interrupted container and advanced lineage: {manifest.get('integrity_root')}"
    )


@cli.command("studio")
@click.argument(
    "container_path",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
)
@click.option("--port", type=int, default=7314, show_default=True)
@click.option(
    "--host",
    type=click.Choice(["127.0.0.1", "localhost"], case_sensitive=False),
    default="127.0.0.1",
    show_default=True,
    help="Studio is intentionally restricted to a loopback interface.",
)
@click.option("--no-open", is_flag=True, help="Do not open Studio in the default browser.")
def cmd_studio(container_path: Path, port: int, host: str, no_open: bool) -> None:
    """Open a verified ngram directory in the model-free local Studio."""
    try:
        import uvicorn
    except ImportError as exc:
        raise click.ClickException(
            "Studio requires FastAPI and Uvicorn. Install: pip install 'ngram[studio]'"
        ) from exc

    from ngram.container import ContainerError
    from ngram.studio import create_studio_app
    from ngram.studio.service import StudioError

    try:
        app = create_studio_app(container_path)
    except (ContainerError, StudioError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc

    url = f"http://{host}:{port}"
    click.echo(f"ngram Studio: {url}")
    click.echo(f"Container: {container_path.expanduser().resolve()}")
    click.echo("Model-free local mode; no inference provider will be initialized.")
    if not no_open:
        import threading
        import webbrowser

        threading.Timer(0.7, webbrowser.open, args=(url,)).start()
    uvicorn.run(app, host=host, port=port, log_level="warning")


@cli.group("gateway")
def cmd_gateway() -> None:
    """Inference gateway + Cloudflare tunnel: setup wizard; on/off/status/restart (gateway.ps1 or gateway.sh)."""


@cmd_gateway.command("setup")
@click.option("--tunnel-name", default="ngram-inference", show_default=True)
@click.option(
    "--cloudflared-config",
    default="",
    help="Path to cloudflared config.yml (default: ~/.cloudflared/config.yml).",
)
@click.option("--gateway-host", default="127.0.0.1", show_default=True)
@click.option("--gateway-port", default=8010, show_default=True, type=int)
@click.option(
    "--skip-tunnel-bootstrap",
    is_flag=True,
    default=False,
    help="Do not offer automated cloudflared tunnel + DNS + config.yml.",
)
def cmd_gateway_setup(
    tunnel_name: str,
    cloudflared_config: str,
    gateway_host: str,
    gateway_port: int,
    skip_tunnel_bootstrap: bool,
) -> None:
    """Interactive wizard: bearer token, tunnel config, .env, then optional gateway on (local script)."""
    from ngram.gateway_setup import run_gateway_setup_wizard

    run_gateway_setup_wizard(
        tunnel_name=tunnel_name,
        cloudflared_config=cloudflared_config,
        gateway_host=gateway_host,
        gateway_port=gateway_port,
        skip_tunnel_bootstrap=skip_tunnel_bootstrap,
    )


@cmd_gateway.command("on")
@click.option("--tunnel-name", default="ngram-inference", show_default=True)
@click.option("--cloudflared-config", default="", help="Path to cloudflared config.yml.")
@click.option("--tunnel-url", default="", help="Optional tunnel URL override for probes.")
@click.option("--gateway-host", default="127.0.0.1", show_default=True)
@click.option("--gateway-port", default=8010, show_default=True, type=int)
def cmd_gateway_on(
    tunnel_name: str,
    cloudflared_config: str,
    tunnel_url: str,
    gateway_host: str,
    gateway_port: int,
) -> None:
    """Start/validate ollama + inference gateway + cloudflared tunnel."""
    args = [
        "-TunnelName",
        tunnel_name,
        "-GatewayHost",
        gateway_host,
        "-GatewayPort",
        str(gateway_port),
    ]
    if cloudflared_config.strip():
        args += ["-CloudflaredConfig", cloudflared_config.strip()]
    if tunnel_url.strip():
        args += ["-TunnelUrl", tunnel_url.strip()]
    _run_gateway_script("on", args)


@cmd_gateway.command("off")
@click.option("--tunnel-name", default="ngram-inference", show_default=True)
@click.option("--leave-ollama-running", is_flag=True)
def cmd_gateway_off(tunnel_name: str, leave_ollama_running: bool) -> None:
    """Stop cloudflared + gateway (+ ollama unless --leave-ollama-running)."""
    args = ["-TunnelName", tunnel_name]
    if leave_ollama_running:
        args.append("-LeaveOllamaRunning")
    _run_gateway_script("off", args)


@cmd_gateway.command("status")
@click.option("--tunnel-name", default="ngram-inference", show_default=True)
@click.option("--cloudflared-config", default="", help="Path to cloudflared config.yml.")
@click.option("--tunnel-url", default="", help="Optional tunnel URL override for probes.")
@click.option("--gateway-host", default="127.0.0.1", show_default=True)
@click.option("--gateway-port", default=8010, show_default=True, type=int)
def cmd_gateway_status(
    tunnel_name: str,
    cloudflared_config: str,
    tunnel_url: str,
    gateway_host: str,
    gateway_port: int,
) -> None:
    """Show gateway process + health status."""
    args = [
        "-TunnelName",
        tunnel_name,
        "-GatewayHost",
        gateway_host,
        "-GatewayPort",
        str(gateway_port),
    ]
    if cloudflared_config.strip():
        args += ["-CloudflaredConfig", cloudflared_config.strip()]
    if tunnel_url.strip():
        args += ["-TunnelUrl", tunnel_url.strip()]
    _run_gateway_script("status", args)


@cmd_gateway.command("restart")
@click.option("--tunnel-name", default="ngram-inference", show_default=True)
@click.option("--cloudflared-config", default="", help="Path to cloudflared config.yml.")
@click.option("--tunnel-url", default="", help="Optional tunnel URL override for probes.")
@click.option("--gateway-host", default="127.0.0.1", show_default=True)
@click.option("--gateway-port", default=8010, show_default=True, type=int)
@click.option("--leave-ollama-running", is_flag=True)
def cmd_gateway_restart(
    tunnel_name: str,
    cloudflared_config: str,
    tunnel_url: str,
    gateway_host: str,
    gateway_port: int,
    leave_ollama_running: bool,
) -> None:
    """Stop then start the gateway stack (same as ``off`` then ``on``)."""
    args = [
        "-TunnelName",
        tunnel_name,
        "-GatewayHost",
        gateway_host,
        "-GatewayPort",
        str(gateway_port),
    ]
    if cloudflared_config.strip():
        args += ["-CloudflaredConfig", cloudflared_config.strip()]
    if tunnel_url.strip():
        args += ["-TunnelUrl", tunnel_url.strip()]
    if leave_ollama_running:
        args.append("-LeaveOllamaRunning")
    _run_gateway_script("restart", args)


@cli.command("talk")
@click.argument("entity_name")
@click.option(
    "--ollama",
    "with_ollama",
    is_flag=True,
    help="Start local Ollama if needed (does not pull models; use --pull-models to fetch).",
)
@click.option(
    "--pull-models",
    is_flag=True,
    help="Run ollama pull for configured models (optional; combine with --ollama).",
)
def cmd_talk(entity_name: str, with_ollama: bool, pull_models: bool) -> None:
    """CLI-only conversation (no daemon, no other platforms)."""
    _prepare_ollama_cli(entity_name, with_ollama, pull_models)
    try:
        asyncio.run(_talk(entity_name))
    except KeyboardInterrupt:
        click.echo("\nStopped.", err=True)
    finally:
        shutdown_spawned_ollama()


@cli.command("ask")
@click.argument("entity_name")
@click.argument("message_parts", nargs=-1, required=True)
@click.option(
    "--ollama",
    "with_ollama",
    is_flag=True,
    help="Start local Ollama if needed (does not pull models; use --pull-models to fetch).",
)
@click.option(
    "--pull-models",
    is_flag=True,
    help="Run ollama pull for configured models (optional; combine with --ollama).",
)
def cmd_ask(
    entity_name: str,
    message_parts: tuple[str, ...],
    with_ollama: bool,
    pull_models: bool,
) -> None:
    """Single-turn CLI prompt (one reply, no daemon)."""
    message = " ".join(message_parts).strip()
    if not message:
        raise click.ClickException("Message cannot be empty.")
    _prepare_ollama_cli(entity_name, with_ollama, pull_models)
    try:
        asyncio.run(_ask_once(entity_name, message))
    except KeyboardInterrupt:
        click.echo("\nStopped.", err=True)
    finally:
        shutdown_spawned_ollama()


async def _ask_once(entity_name: str, message: str) -> None:
    harness = load_harness_config()
    ec = load_entity_config(entity_name, harness)
    setup_logging(
        ec.name,
        ec.harness.logging.level,
        ec.log_path(),
        ec.harness.logging.format,
        immersive=True,
    )
    ent = Entity(ec)
    cli_p = _OneShotCLIPlatform()
    await cli_p.connect()
    ent.register_platform("cli", cli_p)
    inp = Input(
        text=message,
        person_id=cli_p.get_person_id(None),
        person_name=cli_p.get_person_name(None),
        channel="cli",
        platform="cli",
    )
    try:
        reply, _ = await ent.perceive(inp, reply_platform=cli_p)
        click.echo(reply)
    finally:
        try:
            await asyncio.shield(cli_p.disconnect())
        except BaseException:
            pass
        try:
            await asyncio.shield(ent.shutdown())
        except BaseException:
            pass


async def _talk(entity_name: str) -> None:
    from ngram.cognition.reply_heuristics import format_user_visible_failure
    from ngram.entity import Entity
    from ngram.presence.platforms.cli import CLIPlatform

    harness = load_harness_config()
    ec = load_entity_config(entity_name, harness)
    setup_logging(
        ec.name,
        ec.harness.logging.level,
        ec.log_path(),
        ec.harness.logging.format,
        immersive=True,
    )
    ent = Entity(ec)
    cli_p = CLIPlatform(ent, app_version=_app_version(), immersive=True)
    await cli_p.connect()
    ent.register_platform("cli", cli_p)

    async def on_inp(inp):
        try:
            reply, _ = await ent.perceive(inp, stream=cli_p.stream_delta, reply_platform=cli_p)
            return reply
        except Exception as e:
            return format_user_visible_failure(e)

    await cli_p.on_message(on_inp)
    try:
        await cli_p.run_repl()
    finally:
        try:
            await asyncio.shield(ent.shutdown())
        except BaseException:
            pass


@cli.group("lab")
def cmd_lab() -> None:
    """Configure and run a trusted-LAN Quest/WebXR lab."""


def _lab_setup_options(function):
    options = [
        click.option(
            "--provider",
            type=click.Choice(
                [
                    "openai",
                    "venice",
                    "anthropic",
                    "gemini",
                    "openrouter",
                    "xai",
                    "groq",
                    "together",
                    "fireworks",
                    "mistral",
                    "deepseek",
                    "custom",
                ]
            ),
            default=None,
            help="Reuse the configured hosted provider; defaults to OpenAI on a fresh checkout.",
        ),
        click.option(
            "--model", default="", help="Exact hosted chat model ID (prompted on first setup)."
        ),
        click.option(
            "--embedding-model",
            default="",
            help="Hosted embedding model ID (OpenAI has a safe default).",
        ),
        click.option(
            "--base-url",
            default="",
            help="Custom OpenAI-compatible API prefix; HTTPS is required off-device.",
        ),
        click.option(
            "--api-key-env",
            default="",
            help="Name of the environment variable that stores the provider key.",
        ),
        click.option(
            "--person-id",
            default="ar_user",
            show_default=True,
            help="Cross-surface identity to pair with this shell.",
        ),
        click.option("--person-name", default="You", show_default=True),
        click.option("--shell", "shell_path", type=click.Path(path_type=Path)),
        click.option(
            "--bridge-port", type=click.IntRange(1024, 65535), default=7878, show_default=True
        ),
        click.option(
            "--surface-port", type=click.IntRange(1024, 65535), default=3000, show_default=True
        ),
        click.option(
            "--skip-install",
            is_flag=True,
            help="Use the existing pinned Node dependency installation.",
        ),
        click.option(
            "--skip-provider-check",
            is_flag=True,
            help="Skip the live chat-catalog and embedding readiness probe.",
        ),
        click.option(
            "-y",
            "assume_yes",
            is_flag=True,
            help="Do not prompt; the API key must already be in the environment or .env.",
        ),
    ]
    for option in reversed(options):
        function = option(function)
    return function


@cmd_lab.command("setup")
@click.argument("entity_name")
@_lab_setup_options
def cmd_lab_setup(
    entity_name: str,
    provider: str | None,
    model: str,
    embedding_model: str,
    base_url: str,
    api_key_env: str,
    person_id: str,
    person_name: str,
    shell_path: Path | None,
    bridge_port: int,
    surface_port: int,
    skip_install: bool,
    skip_provider_check: bool,
    assume_yes: bool,
) -> None:
    """Configure a hosted brain and authenticated local Entity/AR bridge."""
    from ngram.lab import setup_lab

    try:
        setup_lab(
            entity_name,
            provider=provider,
            model=model,
            embedding_model=embedding_model,
            base_url=base_url,
            api_key_env=api_key_env,
            person_id=person_id,
            person_name=person_name,
            shell_path=shell_path,
            bridge_port=bridge_port,
            surface_port=surface_port,
            install_node=not skip_install,
            check_provider=not skip_provider_check,
            assume_yes=assume_yes,
        )
    except subprocess.CalledProcessError as exc:
        raise click.ClickException(
            f"Lab dependency/build command failed (code {exc.returncode})."
        ) from exc


@cmd_lab.command("doctor")
@click.argument("entity_name")
def cmd_lab_doctor(entity_name: str) -> None:
    """Check the configured Entity, secrets, bridge, build, Node, and ports."""
    from ngram.lab import doctor_lab, platform_summary

    manifest = doctor_lab(entity_name)
    click.echo(f"[ok] Lab is ready: {manifest.entity} / {manifest.provider} / {platform_summary()}")
    click.echo("[ok] Entity bridge is loopback-only; no tunnel is configured by this workflow.")


@cmd_lab.command("run")
@click.argument("entity_name")
@click.option(
    "--lan/--local-only",
    default=False,
    help="Expose only the WebXR surface to a trusted LAN for Quest.",
)
@click.option(
    "-y", "assume_yes", is_flag=True, help="Accept the trusted-LAN warning non-interactively."
)
def cmd_lab_run(entity_name: str, lan: bool, assume_yes: bool) -> None:
    """Run Entity and WebXR together; Ctrl+C shuts down both process trees."""
    from ngram.lab import run_lab

    run_lab(entity_name, expose_lan=lan, assume_yes=assume_yes)


@cmd_lab.command("up")
@click.argument("entity_name")
@_lab_setup_options
@click.option(
    "--lan/--local-only",
    default=False,
    help="Expose only the WebXR surface to a trusted LAN for Quest.",
)
@click.option("--configure", is_flag=True, help="Replace the existing lab profile before starting.")
def cmd_lab_up(
    entity_name: str,
    provider: str | None,
    model: str,
    embedding_model: str,
    base_url: str,
    api_key_env: str,
    person_id: str,
    person_name: str,
    shell_path: Path | None,
    bridge_port: int,
    surface_port: int,
    skip_install: bool,
    skip_provider_check: bool,
    assume_yes: bool,
    lan: bool,
    configure: bool,
) -> None:
    """One command: configure when needed, verify, then run the complete lab."""
    from ngram.lab import load_lab_manifest, run_lab, setup_lab

    current = load_lab_manifest()
    if configure or current is None or current.entity.casefold() != entity_name.casefold():
        try:
            setup_lab(
                entity_name,
                provider=provider,
                model=model,
                embedding_model=embedding_model,
                base_url=base_url,
                api_key_env=api_key_env,
                person_id=person_id,
                person_name=person_name,
                shell_path=shell_path,
                bridge_port=bridge_port,
                surface_port=surface_port,
                install_node=not skip_install,
                check_provider=not skip_provider_check,
                assume_yes=assume_yes,
            )
        except subprocess.CalledProcessError as exc:
            raise click.ClickException(
                f"Lab dependency/build command failed (code {exc.returncode})."
            ) from exc
    run_lab(entity_name, expose_lan=lan, assume_yes=assume_yes)


@cli.command("run")
@click.argument("entity_name")
@click.option(
    "--ollama",
    "with_ollama",
    is_flag=True,
    help="Start local Ollama if needed (does not pull models; use --pull-models to fetch).",
)
@click.option(
    "--pull-models",
    is_flag=True,
    help="Run ollama pull for configured models (optional; combine with --ollama).",
)
def cmd_run(entity_name: str, with_ollama: bool, pull_models: bool) -> None:
    """Start entity with daemon and configured platforms."""
    _prepare_ollama_cli(entity_name, with_ollama, pull_models)
    try:
        asyncio.run(_run(entity_name, worker_mode=False))
    except KeyboardInterrupt:
        click.echo("\nStopped.", err=True)
    finally:
        shutdown_spawned_ollama()


@cli.command("stop")
@click.option(
    "--skip-gateway",
    is_flag=True,
    help="Do not run the gateway helper script off (home stack: cloudflared + gateway + Ollama).",
)
@click.option("--tunnel-name", default="ngram-inference", show_default=True)
@click.option(
    "--leave-ollama-running",
    is_flag=True,
    help="Do not terminate Ollama OS processes; forward to gateway off script when applicable.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="List processes that would be stopped; do not kill or run gateway off.",
)
def cmd_stop(
    skip_gateway: bool,
    tunnel_name: str,
    leave_ollama_running: bool,
    dry_run: bool,
) -> None:
    """Stop local ngram processes, optional home gateway stack, and all Ollama OS processes."""
    from ngram.utils.stack_stop import stop_all_ollama_processes, stop_local_ngram_processes

    stop_local_ngram_processes(dry_run=dry_run, log=click.echo)
    if dry_run:
        if skip_gateway:
            click.echo("Dry run: would skip gateway off (--skip-gateway).")
        elif gateway_script_available():
            click.echo(
                f"Dry run: would run gateway off (tunnel={tunnel_name!r}"
                f"{', leave Ollama running (script only)' if leave_ollama_running else ''})."
            )
        else:
            click.echo("Dry run: gateway helper script not available — would skip gateway off.")
        if leave_ollama_running:
            click.echo("Dry run: would leave Ollama OS processes running (--leave-ollama-running).")
        else:
            stop_all_ollama_processes(dry_run=True, log=click.echo)
        return

    if not skip_gateway and gateway_script_available():
        args = ["-TunnelName", tunnel_name]
        if leave_ollama_running:
            args.append("-LeaveOllamaRunning")
        rc = _run_gateway_script_subprocess("off", args)
        if rc != 0:
            click.echo(
                f"gateway off exited with code {rc} (nothing running or script reported an error).",
                err=True,
            )
        else:
            click.echo("Home gateway stack stopped (gateway off).")
    elif not skip_gateway:
        click.echo(
            "Gateway helper script not available (install bash + scripts/gateway.sh on macOS/Linux, "
            "or scripts/gateway.ps1 on Windows). Skipping gateway off; stopping ngram + Ollama processes only.",
            err=True,
        )

    if skip_gateway:
        click.echo("Left gateway script untouched (--skip-gateway).")

    if leave_ollama_running:
        click.echo("Left Ollama running (--leave-ollama-running).")
    else:
        stop_all_ollama_processes(dry_run=False, log=click.echo)


async def _run(entity_name: str, *, worker_mode: bool = False) -> None:
    from ngram.cognition.reply_heuristics import format_user_visible_failure
    from ngram.entity import Entity
    from ngram.health import check_inference
    from ngram.inference.factory import effective_inference_provider_name
    from ngram.presence.daemon import PresenceDaemon
    from ngram.presence.platforms.cli import CLIPlatform
    from ngram.presence.platforms.discord_platform import DiscordPlatform, token_from_config
    from ngram.presence.platforms.telegram_platform import (
        TelegramPlatform,
        merge_telegram_allowed_user_ids,
        merge_telegram_operator_user_ids,
    )

    harness = load_harness_config()
    ec = load_entity_config(entity_name, harness)
    for w in validate_entity_env(ec):
        click.echo(f"Warning: {w}", err=True)
    _worker_quiet = (
        (
            "ngram.presence.telegram",
            "ngram.presence.discord",
            "ngram.inference_gateway",
            "telegram",
            "httpx",
            "httpcore",
        )
        if worker_mode
        else ()
    )
    setup_logging(
        ec.name,
        ec.harness.logging.level,
        ec.log_path(),
        ec.harness.logging.format,
        console_quiet=_worker_quiet,
    )
    ent = Entity(ec)
    daemon: PresenceDaemon | None = None
    platforms: list = []
    ngram_ar_bridge_runner: object | None = None

    try:
        hosted_inference = effective_inference_provider_name(ec.harness) != "local"
        try:
            inf = await asyncio.wait_for(
                check_inference(ec, ent.client), timeout=30.0 if hosted_inference else 8.0
            )
            if not inf.get("ok"):
                if hosted_inference:
                    raise RuntimeError(f"Hosted inference is not ready: {inf}")
                click.echo(f"Warning: inference not healthy: {inf}", err=True)
        except TimeoutError as exc:
            if hosted_inference:
                raise RuntimeError("Hosted inference readiness check timed out.") from exc
            click.echo("Warning: inference check timed out (continuing startup).", err=True)
        except Exception as e:
            if hosted_inference:
                raise
            click.echo(f"Warning: inference check failed: {e}", err=True)

        daemon = PresenceDaemon(ent)

        try:
            ngram_ar_bridge_runner = await _start_ngram_ar_bridge(ent)
        except Exception as e:
            click.echo(f"Warning: ngram AR entity bridge failed to start: {e}", err=True)

        discord_p: DiscordPlatform | None = None
        telegram_p: TelegramPlatform | None = None

        for pl in ec.presence.platforms:
            t = (pl.get("type") or "").lower()
            if t == "discord":
                tok = token_from_config(pl.get("token_env", "DISCORD_TOKEN"))
                if not tok:
                    click.echo("Discord token missing; skipping Discord.", err=True)
                    continue
                chans = pl.get("channels") or []
                proactive_raw = pl.get("proactive_channel_id")
                proactive_cid: int | None = None
                if proactive_raw is not None:
                    try:
                        proactive_cid = int(proactive_raw)
                    except (TypeError, ValueError):
                        proactive_cid = None
                discord_p = DiscordPlatform(
                    tok,
                    [str(c) for c in chans],
                    entity=ent,
                    proactive_channel_id=proactive_cid,
                )
                platforms.append(discord_p)
                ent.register_platform("discord", discord_p)
            elif t == "telegram":
                tok = token_from_config(pl.get("token_env", "TELEGRAM_TOKEN"))
                if not tok:
                    click.echo("Telegram token missing; skipping Telegram.", err=True)
                    continue
                allow_users = merge_telegram_allowed_user_ids(pl.get("allowed_user_ids"))
                raw_chats = pl.get("allowed_chat_ids")
                allow_chats: set[int] | None = None
                if isinstance(raw_chats, list) and len(raw_chats) > 0:
                    allow_chats = {int(x) for x in raw_chats}
                op_users = merge_telegram_operator_user_ids(pl.get("operator_user_ids"))
                raw_cu = pl.get("concurrent_updates", 1)
                try:
                    tg_concurrent_updates = max(1, min(256, int(raw_cu)))
                except (TypeError, ValueError):
                    tg_concurrent_updates = 1
                raw_pt = pl.get("poll_timeout", 20.0)
                try:
                    tg_poll_timeout = max(1.0, min(30.0, float(raw_pt)))
                except (TypeError, ValueError):
                    tg_poll_timeout = 20.0
                raw_pi = pl.get("poll_interval", 0.35)
                try:
                    tg_poll_interval = max(0.0, min(2.0, float(raw_pi)))
                except (TypeError, ValueError):
                    tg_poll_interval = 0.35
                telegram_p = TelegramPlatform(
                    tok,
                    entity=ent,
                    app_version=_app_version(),
                    allowed_user_ids=allow_users,
                    allowed_chat_ids=allow_chats,
                    operator_user_ids=op_users,
                    concurrent_updates=tg_concurrent_updates,
                    poll_timeout=tg_poll_timeout,
                    poll_interval=tg_poll_interval,
                    bot_name=str(pl.get("bot_name") or "").strip() or None,
                    bot_short_description=(
                        str(pl.get("bot_short_description") or "").strip() or None
                    ),
                    bot_description=str(pl.get("bot_description") or "").strip() or None,
                )
                platforms.append(telegram_p)
                ent.register_platform("telegram", telegram_p)

        cli_p = CLIPlatform(ent, app_version=_app_version(), immersive=False)
        platforms.append(cli_p)
        ent.register_platform("cli", cli_p)
        has_cli = (
            any((p.get("type") or "").lower() == "cli" for p in ec.presence.platforms)
            and not worker_mode
        )

        async def proactive_fanout(message: str) -> None:
            sent = False
            if discord_p:
                try:
                    await discord_p.send_proactive_default(message)
                    sent = True
                except Exception:
                    pass
            if telegram_p:
                try:
                    await telegram_p.send_proactive_default(message)
                    sent = True
                except Exception:
                    pass
            if not sent:
                click.echo(f"\n[{ec.name}]: {message}\n")

        ent.register_proactive_sink(proactive_fanout)

        async def route_reply(inp, text: str):
            meta = ent.voice_ctl.meta_for_response(
                ent.emotions.get_state(),
                len(text),
                inp.platform,
            )
            delay = min(3.0, meta.typing_delay_seconds)
            if inp.platform == "telegram" and telegram_p:
                await telegram_p.send_typing(int(inp.channel))
                await asyncio.sleep(delay)
            else:
                await asyncio.sleep(delay)
            if inp.platform == "discord" and discord_p:
                await discord_p.send_plain_chunks(inp.channel, text, chunk_pause=meta.chunk_pause)
                await discord_p.sync_emotion_presence(ent.emotions.get_state().primary)
            elif inp.platform == "telegram" and telegram_p:
                await telegram_p.send_plain_chunks(inp.channel, text, pause=meta.chunk_pause)

        async def _telegram_typing_worker(chat_id: int, stop: asyncio.Event) -> None:
            while not stop.is_set():
                await telegram_p.send_typing(chat_id)
                try:
                    await asyncio.wait_for(stop.wait(), timeout=4.5)
                except TimeoutError:
                    continue

        async def on_inp(inp):
            stream = cli_p.stream_delta if inp.platform == "cli" else None
            reply_pf = None
            if inp.platform == "cli":
                reply_pf = cli_p
            elif inp.platform == "discord":
                reply_pf = discord_p
            elif inp.platform == "telegram":
                reply_pf = telegram_p
            stop_typing = asyncio.Event()
            stop_busy = asyncio.Event()
            typing_task: asyncio.Task[None] | None = None
            busy_task: asyncio.Task[None] | None = None
            if inp.platform == "telegram" and telegram_p:
                typing_task = asyncio.create_task(
                    _telegram_typing_worker(int(inp.channel), stop_typing)
                )
                reply_to_mid = None
                meta = inp.metadata if isinstance(inp.metadata, dict) else {}
                raw_mid = meta.get("telegram_message_id")
                if raw_mid is not None:
                    try:
                        reply_to_mid = int(raw_mid)
                    except (TypeError, ValueError):
                        reply_to_mid = None
                if telegram_p.busy_indicator_enabled_for(int(inp.channel)):
                    busy_task = asyncio.create_task(
                        telegram_p.run_busy_indicator(
                            int(inp.channel),
                            reply_to_message_id=reply_to_mid,
                            stop=stop_busy,
                        )
                    )
            try:
                try:
                    try:
                        reply, needs_platform_route = await ent.perceive(
                            inp,
                            stream=stream,
                            reply_platform=reply_pf,
                            defer_turn_activity_finish=True,
                        )
                    except asyncio.CancelledError:
                        # New message arrived — this task was interrupted. Clean up and exit quietly.
                        raise
                    except Exception as e:
                        reply = format_user_visible_failure(e)
                        needs_platform_route = True
                finally:
                    stop_typing.set()
                    if typing_task is not None:
                        typing_task.cancel()
                        try:
                            await typing_task
                        except asyncio.CancelledError:
                            pass
                    stop_busy.set()
                    if busy_task is not None:
                        try:
                            await busy_task
                        except Exception:
                            pass
                if inp.platform == "cli":
                    return reply
                try:
                    if needs_platform_route and reply:
                        await route_reply(inp, reply)
                except Exception as e:
                    click.echo(f"Error: {e}", err=True)
                return ""
            finally:
                # Keep cross-surface embodiment active through the actual
                # typing delay/chunk delivery, not merely through inference.
                await asyncio.shield(ent.finish_turn_activity(inp))

        for p in platforms:
            if p == cli_p:
                continue
            await p.on_message(on_inp)
            await p.connect()

        # After messaging platforms are live — avoids aiosqlite + PTB fighting the loop at startup.
        await daemon.start()

        if has_cli:
            await cli_p.connect()
            await cli_p.on_message(on_inp)
            await cli_p.run_repl()
        else:
            click.echo(f"{ec.name} running (no CLI). Ctrl+C to stop.")
            try:
                await _wait_until_stopped()
            except asyncio.CancelledError:
                pass
    finally:
        await _graceful_run_shutdown(
            daemon,
            platforms,
            ent,
            ngram_ar_bridge_runner=ngram_ar_bridge_runner,
        )


@cli.command("status")
@click.argument("entity_name")
def cmd_status(entity_name: str) -> None:
    """Show emotional state, drives, paths."""
    harness = load_harness_config()
    ec = load_entity_config(entity_name, harness)
    setup_logging(ec.name, ec.harness.logging.level, None, "console")
    from ngram.entity import Entity

    ent = Entity(ec)

    async def _show():
        s = ent.emotions.get_state()
        click.echo(f"Entity: {ec.name}")
        click.echo(f"Emotion: {s.primary.value} ({s.intensity:.2f})")
        click.echo("Drives:")
        for d in ent.drives.all_drives():
            click.echo(f"  {d.name}: {d.level:.2f} (threshold {d.threshold})")
        du = ec.database_url()
        click.echo(f"DB: {du if du else ec.db_path()}")
        click.echo(f"Dormant: {ent.dormant}")
        await ent.shutdown()

    asyncio.run(_show())


@cli.command("evolve")
@click.argument("entity_name")
def cmd_evolve(entity_name: str) -> None:
    """Force one trait evolution cycle."""
    harness = load_harness_config()
    ec = load_entity_config(entity_name, harness)
    setup_logging(ec.name, ec.harness.logging.level, None, "console")
    from ngram.entity import Entity

    ent = Entity(ec)

    async def _go():
        c = await ent.evolution.run_cycle({"anxiety_trend": 0.15, "deep_conversations": 5})
        click.echo(json.dumps(c, indent=2))
        await ent.shutdown()

    asyncio.run(_go())


@cli.command("knowledge")
@click.argument("entity_name")
def cmd_knowledge(entity_name: str) -> None:
    """Create (if missing) and open the entity's knowledge.md in $EDITOR (default notepad / nano)."""
    harness = load_harness_config()
    ec = load_entity_config(entity_name, harness)
    kpath = Path(ec.knowledge_path()).expanduser()
    kpath.parent.mkdir(parents=True, exist_ok=True)
    if not kpath.is_file():
        kpath.write_text(KNOWLEDGE_STARTER_TEMPLATE, encoding="utf-8", newline="\n")
    mount = None
    if ec.is_live_container():
        from ngram.container import ContainerError, LiveContainerMount

        try:
            mount = LiveContainerMount(ec)
            mount.prepare()
        except ContainerError as exc:
            raise click.ClickException(str(exc)) from exc
        kpath = Path(ec.knowledge_path()).expanduser()
        if not kpath.is_file():
            kpath.write_text(KNOWLEDGE_STARTER_TEMPLATE, encoding="utf-8", newline="\n")
    editor = (os.environ.get("EDITOR") or "").strip()
    try:
        if not editor:
            if os.name == "nt":
                subprocess.run(["notepad", str(kpath)], check=False)
            else:
                nano = shutil.which("nano") or "nano"
                subprocess.run([nano, str(kpath)], check=False)
        else:
            try:
                parts = shlex.split(editor, posix=os.name != "nt") + [str(kpath)]
            except ValueError as e:
                raise click.ClickException(f"Could not parse EDITOR: {e}") from e
            subprocess.run(parts, check=False)
    finally:
        if mount is not None:
            asyncio.run(mount.close())


@cli.command("journal")
@click.argument("entity_name")
def cmd_journal(entity_name: str) -> None:
    """Open the entity's journal.md in $EDITOR (append-only reflections from routines)."""
    harness = load_harness_config()
    ec = load_entity_config(entity_name, harness)
    jpath = Path(ec.journal_path()).expanduser()
    jpath.parent.mkdir(parents=True, exist_ok=True)
    if not jpath.is_file():
        jpath.write_text("# journal\n\n", encoding="utf-8", newline="\n")
    mount = None
    if ec.is_live_container():
        from ngram.container import ContainerError, LiveContainerMount

        try:
            mount = LiveContainerMount(ec)
            mount.prepare()
        except ContainerError as exc:
            raise click.ClickException(str(exc)) from exc
        jpath = Path(ec.journal_path()).expanduser()
        if not jpath.is_file():
            jpath.write_text("# journal\n\n", encoding="utf-8", newline="\n")
    editor = (os.environ.get("EDITOR") or "").strip()
    try:
        if not editor:
            if os.name == "nt":
                subprocess.run(["notepad", str(jpath)], check=False)
            else:
                nano = shutil.which("nano") or "nano"
                subprocess.run([nano, str(jpath)], check=False)
        else:
            try:
                parts = shlex.split(editor, posix=os.name != "nt") + [str(jpath)]
            except ValueError as e:
                raise click.ClickException(f"Could not parse EDITOR: {e}") from e
            subprocess.run(parts, check=False)
    finally:
        if mount is not None:
            asyncio.run(mount.close())


@cli.command("recall")
@click.argument("entity_name")
@click.argument("query")
@click.option(
    "--database-url",
    "cli_database_url",
    envvar="DATABASE_URL",
    default=None,
    help="Postgres URL (same as Railway worker DATABASE_URL). Without it, CLI uses local SQLite.",
)
def cmd_recall(entity_name: str, query: str, cli_database_url: str | None) -> None:
    """Search episodic memories by semantic similarity."""
    harness = load_harness_config()
    ec = load_entity_config(entity_name, harness)
    _apply_cli_database_url(cli_database_url)
    _echo_cli_database_target(ec)
    _warn_if_hybrid_without_postgres(ec)
    from ngram.entity import Entity

    ent = Entity(ec)

    async def _recall():
        try:
            async with ent.store.session() as db:
                qe = await ent.client.embed(ec.harness.models.embedding, query)
                if not qe:
                    click.echo("Could not embed query (inference backend unavailable?).")
                    return
                pairs = await ent.store.search_episodes_by_embedding(db, qe, limit=8)
                for eid, score in pairs:
                    click.echo(f"{score:.3f}  {eid}")
        finally:
            try:
                await asyncio.shield(ent.shutdown())
            except BaseException:
                pass

    asyncio.run(_recall())


@cli.command("seeds")
@click.argument("entity_name")
@click.option("--last", default=50, show_default=True, type=int, help="Max recent seed_log rows.")
@click.option(
    "--database-url",
    "cli_database_url",
    envvar="DATABASE_URL",
    default=None,
    help="Postgres URL (same as Railway worker DATABASE_URL). Without it, CLI uses local SQLite.",
)
def cmd_seeds(entity_name: str, last: int, cli_database_url: str | None) -> None:
    """Show recent exogenous noise seeds and whether GEN consumed them."""
    harness = load_harness_config()
    ec = load_entity_config(entity_name, harness)
    _apply_cli_database_url(cli_database_url)
    _echo_cli_database_target(ec)
    _warn_if_hybrid_without_postgres(ec)
    from ngram.entity import Entity

    ent = Entity(ec)

    async def _show():
        from ngram.memory.seed_log import list_recent_seeds

        try:
            async with ent.store.session() as db:
                rows = await list_recent_seeds(db, entity_name=ec.name, limit=max(1, last))
            for r in rows:
                click.echo(
                    f"{r['tick_timestamp']:.0f}  {r['source_type']}  consumed={r['consumed']}  "
                    f"{r['seed_text'][:120]!r}"
                )
                if r.get("fragment_produced"):
                    click.echo(f"    -> fragments: {r['fragment_produced'][:200]}")
        finally:
            try:
                await asyncio.shield(ent.shutdown())
            except BaseException:
                pass

    asyncio.run(_show())


@cli.command("trace")
@click.argument("entity_name")
@click.option(
    "--last", default=30, show_default=True, type=int, help="Max rows with produced fragments."
)
@click.option(
    "--database-url",
    "cli_database_url",
    envvar="DATABASE_URL",
    default=None,
    help="Postgres URL (same as Railway worker DATABASE_URL). Without it, CLI uses local SQLite.",
)
def cmd_trace(entity_name: str, last: int, cli_database_url: str | None) -> None:
    """Show seed-to-fragment audit rows (organism-test trail)."""
    harness = load_harness_config()
    ec = load_entity_config(entity_name, harness)
    _apply_cli_database_url(cli_database_url)
    _echo_cli_database_target(ec)
    _warn_if_hybrid_without_postgres(ec)
    from ngram.entity import Entity

    ent = Entity(ec)

    async def _show():
        from ngram.memory.seed_log import list_trace_rows

        try:
            async with ent.store.session() as db:
                rows = await list_trace_rows(db, entity_name=ec.name, limit=max(1, last))
            for r in rows:
                click.echo(
                    f"trace={r['trace_id']}  {r['source_type']}  seed={r['seed_text'][:100]!r}"
                )
                click.echo(f"  fragments: {r.get('fragment_produced', '')[:240]}")
                click.echo(f"  tags: {r.get('fragment_tags', [])}")
        finally:
            try:
                await asyncio.shield(ent.shutdown())
            except BaseException:
                pass

    asyncio.run(_show())


@cli.command("worker")
@click.argument("entity_name")
def cmd_worker(entity_name: str) -> None:
    """Run platforms + daemon without CLI (use on Railway as ngram-worker)."""
    if not (entity_name or "").strip():
        raise click.ClickException(
            "Missing entity name (got empty string). For Railway: set NGRAM_ENTITY "
            "(e.g. canary) on the worker service."
        )
    try:
        asyncio.run(_run(entity_name, worker_mode=True))
    except KeyboardInterrupt:
        click.echo("\nStopped.", err=True)


@cli.command("api")
@click.option("--host", default="0.0.0.0", show_default=True)
@click.option("--port", default=8080, show_default=True, type=int)
def cmd_api(host: str, port: int) -> None:
    """HTTP health service (Railway ngram-api). Requires: pip install 'ngram[api]'"""
    try:
        import uvicorn
    except ImportError as e:
        raise click.ClickException(
            "Missing uvicorn/fastapi. Install: pip install 'ngram[api]'"
        ) from e
    from ngram.apps.api import create_api_app

    uvicorn.run(create_api_app(), host=host, port=port, log_level="info")


@cli.command("hands")
@click.option("--host", default="127.0.0.1", show_default=True)
@click.option("--port", default=8020, show_default=True, type=int)
def cmd_hands(host: str, port: int) -> None:
    """Run local RPC Execution backend (requires 'playwright' & 'fastapi')."""
    try:
        import uvicorn
    except ImportError as e:
        raise click.ClickException(
            "Missing uvicorn/fastapi. Install: pip install 'ngram[api]'"
        ) from e
    try:
        from playwright.async_api import async_playwright  # noqa
    except ImportError as e:
        raise click.ClickException(
            "Missing playwright. Install: pip install 'ngram[browser]' and run: playwright install chromium"
        ) from e

    from ngram.apps.hands import create_hands_app

    uvicorn.run(create_hands_app(), host=host, port=port, log_level="info")


@cli.command("wipe")
@click.argument("entity_name")
@click.option(
    "--yes",
    "skip_confirm",
    is_flag=True,
    help="Skip confirmation (non-interactive).",
)
def cmd_wipe(entity_name: str, skip_confirm: bool) -> None:
    """Delete in-memory chat turns and all SQLite memory (episodes, people, beliefs, narrative, etc.)."""
    harness = load_harness_config()
    ec = load_entity_config(entity_name, harness)
    db_path = ec.db_path()
    if not skip_confirm:
        click.echo(
            f"This clears conversation context and wipes the memory database for {ec.name!r}:\n  {db_path}",
            err=True,
        )
        if not click.confirm("Proceed?"):
            raise click.Abort()
    setup_logging(ec.name, ec.harness.logging.level, None, "console")
    from ngram.entity import Entity

    ent = Entity(ec)

    async def _wipe():
        await ent.wipe_experiential_state()
        try:
            await asyncio.shield(ent.shutdown())
        except BaseException:
            pass

    asyncio.run(_wipe())
    click.echo(
        f"Wiped {ec.name} (DB + rolling chat + inner voice; YAML traits restored from file)."
    )


@cli.command("soma-reset")
@click.argument("entity_name")
@click.option(
    "--yes",
    "skip_confirm",
    is_flag=True,
    help="Skip confirmation (non-interactive / Railway).",
)
def cmd_soma_reset(entity_name: str, skip_confirm: bool) -> None:
    """Reset soma bars to YAML initials, clear GEN noise + affects; write DB + soma-state.json + body.md.

    Hybrid / Railway: run once against the same DATABASE_URL and workspace as the worker
    (e.g. ``railway run --service ngram-worker -- ngram soma-reset YOUR_ENTITY --yes``),
    then restart the worker so the running process reloads tonic state.
    """
    harness = load_harness_config()
    ec = load_entity_config(entity_name, harness)
    if not skip_confirm:
        click.echo(
            f"Reset soma (bars, noise, affects) for {ec.name!r} to harness YAML baseline "
            f"and persist to DB + {ec.soma_dir()}.",
            err=True,
        )
        if not click.confirm("Proceed?"):
            raise click.Abort()
    setup_logging(ec.name, ec.harness.logging.level, None, "console")
    from ngram.entity import Entity

    ent = Entity(ec)

    async def _reset():
        await ent.reset_soma_baseline()
        try:
            await asyncio.shield(ent.shutdown())
        except BaseException:
            pass

    asyncio.run(_reset())
    click.echo(f"Soma baseline reset for {ec.name}. Restart the worker if it is already running.")


@cli.command("export")
@click.argument("entity_name")
@click.argument("destination", type=click.Path(path_type=Path))
def cmd_export(entity_name: str, destination: Path) -> None:
    """Export a complete runtime entity as an integrity-checked .ngram archive."""
    from ngram.container import (
        ContainerError,
        PostgresLeaseManager,
        export_hybrid_entity,
        export_legacy_entity,
        pack_locked_live_container,
        resolve_entity_identity,
    )

    candidate = Path(entity_name).expanduser()
    if candidate.is_dir() and (candidate / "manifest.json").is_file():
        target = destination.expanduser()
        if target.exists() and target.is_dir():
            target = target / f"{candidate.stem}.ngram"
        elif target.suffix.lower() != ".ngram":
            target.mkdir(parents=True, exist_ok=True)
            target = target / f"{candidate.stem}.ngram"
        try:
            config = load_live_container_config(candidate)
            result = pack_locked_live_container(config, target)
        except ContainerError as exc:
            raise click.ClickException(str(exc)) from exc
        click.echo(f"Exported portable ngram archive: {result}")
        click.echo(
            "Warning: portable and integrity-checked, but not encrypted or signed.",
            err=True,
        )
        return

    harness = load_harness_config()
    ec = load_entity_config(entity_name, harness)
    src_yaml = project_configs_dir() / "entities" / f"{entity_name}.yaml"
    target = destination.expanduser()
    if target.exists() and target.is_dir():
        target = target / f"{entity_name}.ngram"
    elif target.suffix.lower() != ".ngram":
        target.mkdir(parents=True, exist_ok=True)
        target = target / f"{entity_name}.ngram"
    try:
        database_url = str(ec.database_url() or "").strip()
        if database_url.startswith(("postgresql://", "postgres://")):

            async def _export_hybrid() -> Path:
                identity = resolve_entity_identity(ec)
                manager = PostgresLeaseManager(database_url)
                lease_record = await manager.inspect(identity["entity_id"])
                if lease_record is None:
                    raise ContainerError(
                        "hybrid export requires an active canonical writer lease; "
                        "start the authoritative worker with NGRAM_CANONICAL_LEASE_REQUIRED=true"
                    )
                return await export_hybrid_entity(
                    ec,
                    src_yaml,
                    target,
                    runtime_version=NGRAM_VERSION,
                    lease_record=lease_record,
                )

            result = asyncio.run(_export_hybrid())
        else:
            result = export_legacy_entity(
                ec,
                src_yaml,
                target,
                runtime_version=NGRAM_VERSION,
            )
    except ContainerError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Exported portable ngram archive: {result}")
    click.echo(
        "Warning: portable and integrity-checked, but not encrypted or signed.",
        err=True,
    )


@cli.command("update")
@click.option(
    "--no-pip",
    "skip_pip",
    is_flag=True,
    help="After a git fast-forward, skip ``pip install -e .`` (git checkouts only).",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Print what would run without fetching, merging, or installing.",
)
def cmd_update(skip_pip: bool, dry_run: bool) -> None:
    """Update this install from the public GitHub repository (git or pip)."""

    def _log(msg: str) -> None:
        click.echo(msg)

    res = perform_self_update(
        pip_reinstall=not skip_pip,
        dry_run=dry_run,
        log=_log,
    )
    if res.get("ok"):
        if dry_run:
            click.echo("Dry run finished.")
        elif res.get("method") == "git":
            for m in res.get("messages") or ():
                click.echo(m)
            pip_r = res.get("pip")
            if isinstance(pip_r, dict) and pip_r.get("ok") and (pip_r.get("output") or "").strip():
                click.echo((pip_r.get("output") or "").strip())
            click.echo("Update complete. Restart running workers or daemons to load new code.")
        else:
            out = (res.get("output") or "").strip()
            if out:
                click.echo(out)
            click.echo("Update complete. Restart running workers or daemons to load new code.")
        return

    err = str(res.get("error") or "update failed")
    raise click.ClickException(err)


@cli.command("version")
def cmd_version() -> None:
    """Print the ngram harness version (same as ``ngram --version``)."""
    click.echo(NGRAM_VERSION)


@cli.command("import")
@click.argument("bundle_dir", type=click.Path(exists=True))
@click.option(
    "--activate-hybrid",
    is_flag=True,
    help="Restore into PostgreSQL/object storage and acquire the target writer lease.",
)
@click.option(
    "--database-url",
    envvar="DATABASE_URL",
    help="Fresh target PostgreSQL URL (required with --activate-hybrid).",
)
@click.option(
    "--state-root",
    type=click.Path(path_type=Path),
    help="Fresh durable workspace directory for the activated entity.",
)
@click.option(
    "--lease-holder",
    envvar="NGRAM_CANONICAL_LEASE_HOLDER",
    help="Stable identity of the target authoritative worker.",
)
def cmd_import(
    bundle_dir: str,
    activate_hybrid: bool,
    database_url: str | None,
    state_root: Path | None,
    lease_holder: str | None,
) -> None:
    """Restore a portable ngram artifact or a legacy YAML + memory.db bundle."""
    b = Path(bundle_dir)
    is_portable = b.is_file() or (b / "manifest.json").is_file()
    if is_portable:
        from ngram.container import (
            ContainerError,
            load_artifact_manifest,
            restore_artifact,
            restore_hybrid_artifact,
            runtime_entity_key,
        )

        try:
            manifest = load_artifact_manifest(b)
            entity_key = runtime_entity_key(str(manifest.get("display_name") or "entity"))
            entity_yaml = project_configs_dir() / "entities" / f"{entity_key}.yaml"
            harness = load_harness_config()
            if activate_hybrid:
                target_url = str(database_url or "").strip()
                holder = str(lease_holder or "").strip()
                if not target_url.startswith(("postgresql://", "postgres://")):
                    raise ContainerError("--activate-hybrid requires a PostgreSQL --database-url")
                if not holder:
                    raise ContainerError(
                        "--activate-hybrid requires --lease-holder naming the authoritative worker"
                    )
                if state_root is None:
                    raise ContainerError("--activate-hybrid requires a fresh durable --state-root")
                attachment_store = None
                if harness.attachments.backend.strip().lower() == "object_s3_compat":
                    from ngram.config import entity_from_dict
                    from ngram.storage.attachments import build_attachment_store

                    attachment_store = build_attachment_store(
                        entity_from_dict(
                            harness,
                            {"name": str(manifest.get("display_name") or entity_key)},
                        )
                    )
                asyncio.run(
                    restore_hybrid_artifact(
                        b,
                        entity_yaml,
                        state_root,
                        database_url=target_url,
                        lease_holder=holder,
                        attachment_store=attachment_store,
                    )
                )
                click.echo(f"Activated portable entity {entity_key} with canonical writer lease")
                click.echo(
                    "Credentials were not carried by the artifact; configure target secrets separately.",
                    err=True,
                )
                return
            database_path = Path(
                harness.memory.database_path.replace("{entity_name}", entity_key)
            ).expanduser()
            restore_artifact(
                b,
                entity_yaml,
                database_path.parent,
                database_filename=database_path.name,
            )
        except ContainerError as exc:
            raise click.ClickException(str(exc)) from exc
        click.echo(f"Imported portable entity {entity_key}")
        click.echo(
            "Credentials were not carried by the artifact; configure platform and tool secrets locally.",
            err=True,
        )
        return

    yamls = list(b.glob("*.yaml"))
    if not yamls:
        raise click.ClickException("No YAML in bundle")
    y = yamls[0]
    name = y.stem
    ent_dir = project_configs_dir() / "entities"
    ent_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(y, ent_dir / f"{name}.yaml")
    mem = b / "memory.db"
    if mem.exists():
        harness = load_harness_config()
        ec = load_entity_config(name, harness)
        dest = Path(ec.db_path())
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(mem, dest)
    click.echo(f"Imported entity {name}")


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
