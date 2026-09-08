"""Canonical hosted intelligence + Railway worker onboarding."""

from __future__ import annotations

import json
import re
import secrets
from pathlib import Path

import click
import yaml

from ngram.config import load_entity_config, load_harness_config
from ngram.ngram_ar.setup import (
    RailwayCli, _collect_service_names, _create_default_shell, configure_shell, verify_bridge,
)


def cloud_variables(entity: str, database_service: str, hosted: dict[str, str]) -> dict[str, str]:
    """Explicit provider and embedding settings; never inherit a home gateway."""
    return {
        **hosted,
        "NGRAM_DEPLOYMENT_MODE": "cloud",
        "NGRAM_ENTITY": entity,
        "NGRAM_RAILWAY_ROLE": "worker",
        "NGRAM_EXECUTION_WORKSPACE_DIR": "/app/data",
        "NGRAM_EXECUTION_REQUIRE_RAILWAY": "true",
        "NGRAM_CANONICAL_LEASE_REQUIRED": "true",
        "NGRAM_ENTITY_ID": "ng1:" + secrets.token_hex(16),
        "DATABASE_URL": "${{" + database_service + ".DATABASE_URL}}",
        "NGRAM_INFERENCE_PASS_NUM_CTX": "false",
    }


def _step(number: int, title: str) -> None:
    click.echo(click.style(f"\n  {number:02d} / {title}\n", fg="bright_blue", bold=True))


def _service_id(railway: RailwayCli, name: str) -> str:
    for edge in railway.status().get("services", {}).get("edges", []):
        node = edge.get("node", {})
        if node.get("name") == name and node.get("id"):
            return str(node["id"])
    raise click.ClickException(f"Railway did not return service {name!r}. Refresh the project and retry.")


def _ensure_volume(railway: RailwayCli, service: str, mount: str) -> None:
    service_id = _service_id(railway, service)
    payload = json.loads(railway.run(["volume", "list", "--json"]))
    # The CLI lists the entire environment even with a service selector.
    def matches(value) -> bool:
        if isinstance(value, dict):
            same_service = value.get("serviceId") == service_id or value.get("serviceName") == service
            if same_service and value.get("mountPath") == mount and not value.get("isPendingDeletion"):
                return True
            return any(matches(child) for child in value.values())
        return isinstance(value, list) and any(matches(child) for child in value)
    if not matches(payload):
        railway.run(["volume", "--service", service_id, "add", "--mount-path", mount, "--json"])


def run_cloud_setup(root: Path, env_path: Path) -> None:
    from ngram.setup_wizard import _entity_section, _read_dotenv_value, _run_hosted_flow

    _step(1, "Someone to meet")
    click.echo("Create a new Entity, or connect one you already know.")
    entity = _entity_section()
    if not entity:
        click.echo("Setup paused. Run `uv run ngram setup --profile cloud` when you have an Entity.")
        return
    slug = re.sub(r"[^a-z0-9]+", "-", entity.lower()).strip("-")
    if not slug or len(slug) > 48:
        raise click.ClickException("Use an Entity name that produces a 1–48 character service name.")

    _step(2, "A mind that remembers")
    # This collects and verifies the complete hosted route but leaves deployment
    # credentials in the local ignored file until the explicit deploy review.
    _run_hosted_flow(env_path, cloud=True)
    keys = ["NGRAM_INFERENCE_PROVIDER", "NGRAM_INFERENCE_BASE_URL", "NGRAM_INFERENCE_API_KEY_ENV",
            "NGRAM_INFERENCE_MODEL", "NGRAM_EMBEDDING_MODEL", "NGRAM_EMBEDDING_DIMENSIONS"]
    hosted = {key: _read_dotenv_value(env_path, key) for key in keys}
    key_env = hosted["NGRAM_INFERENCE_API_KEY_ENV"]
    api_key = _read_dotenv_value(env_path, key_env)

    _step(3, "A home in the cloud")
    click.echo(
        "Railway runs the Entity and its Linux tools. A dedicated Postgres database stores\n"
        "memory and relationships. A volume keeps its knowledge, journal, and workspace.\n\n"
        "You need a linked Railway project. If this is your first one, run:\n"
        "  npx @railway/cli@latest login\n"
        "  npx @railway/cli@latest init\n"
        "Then return here. For an existing project, use `npx @railway/cli@latest link`.\n"
    )
    click.confirm("Railway project linked and ready?", default=True, abort=True)
    railway = RailwayCli.discover(cwd=root)
    names = _collect_service_names(railway.status())
    service = click.prompt("Worker service", default=f"{slug}-worker").strip()
    database = click.prompt("Dedicated memory database service", default=f"{slug}-memory").strip()
    if any(not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9-]{0,62}", name) for name in (service, database)):
        raise click.ClickException("Service names must contain only letters, numbers, and hyphens.")
    if service == database:
        raise click.ClickException("Worker and database need different service names.")
    if database in names:
        database_env = json.loads(railway.run(["variable", "list", "--service", database, "--json"]))
        database_url = database_env.get("DATABASE_URL")
        if not database_url:
            raise click.ClickException("The selected memory service has no DATABASE_URL. Choose a Postgres database.")
        for other_service in names:
            if other_service in {database, service}:
                continue
            other = json.loads(railway.run(["variable", "list", "--service", other_service, "--json"]))
            if other.get("NGRAM_ENTITY") and other.get("DATABASE_URL") == database_url:
                raise click.ClickException("That database belongs to another Entity. Use a new database service name to keep their memories separate.")
    # Do not repurpose a live Entity or switch its embedding space by re-running setup.
    if service in names:
        existing = json.loads(railway.run(["variable", "list", "--service", service, "--json"]))
        if existing.get("NGRAM_ENTITY"):
            click.echo(
                "This service already runs an Entity. Its provider and memory settings are preserved.\n"
                "To pair it, run:\n"
                f"  uv run ngram ar setup {entity} --target railway --service {service}\n"
                "For a new Entity with hosted memory, rerun cloud setup with a new worker name."
            )
            return

    config = load_entity_config(entity, load_harness_config())
    variables = cloud_variables(entity, database, hosted)
    click.echo(click.style("\nYour setup", bold=True))
    click.echo(f"  Entity       {config.name}\n  Worker       {service}\n  Memory       {database} + /app/data")
    click.echo(f"  Thinking     {hosted['NGRAM_INFERENCE_PROVIDER']} / {hosted['NGRAM_INFERENCE_MODEL']}")
    click.echo(f"  Embeddings   {hosted['NGRAM_EMBEDDING_MODEL']} / {hosted['NGRAM_EMBEDDING_DIMENSIONS']} dimensions")
    click.echo("  Credentials  Sent privately to the worker; never printed or added to shell YAML.")
    click.confirm("Create missing resources and deploy? Railway hosting and API usage are billed.", default=True, abort=True)

    if database not in names:
        # A dedicated database prevents separate Entities sharing memory tables.
        railway.run(["add", "--image", "postgres:16", "--service", database, "--json"])
        _ensure_volume(railway, database, "/var/lib/postgresql/data")
        railway.run(["variable", "set", "--service", database, "--skip-deploys", "POSTGRES_PASSWORD", "--stdin"],
                    stdin=secrets.token_urlsafe(36))
        railway.run(["variable", "set", "--service", database,
                     "POSTGRES_USER=postgres", "POSTGRES_DB=ngram", "PGDATA=/var/lib/postgresql/data/pgdata",
                     "DATABASE_URL=postgresql://postgres:${{POSTGRES_PASSWORD}}@${{RAILWAY_PRIVATE_DOMAIN}}:5432/ngram"])
        railway.wait_for_deployment(service=database)
    if service not in names:
        railway.run(["add", "--service", service, "--json"])
    _ensure_volume(railway, service, "/app/data")
    railway.run(["variable", "set", "--service", service, "--skip-deploys",
                 *[f"{key}={value}" for key, value in variables.items()]])
    for key, value in {
        key_env: api_key,
        # Runtime config survives ignored personal YAML and the Docker example copy.
        "NGRAM_ENTITY_CONFIG_YAML": yaml.safe_dump(config.raw, sort_keys=False, allow_unicode=True),
    }.items():
        railway.run(["variable", "set", "--service", service, "--skip-deploys", key, "--stdin"], stdin=value)

    _step(4, "A place in your world")
    shell_dir = root / "ngramAR" / "shells" / slug
    if not (shell_dir / "shell.yaml").is_file():
        _create_default_shell(shell_dir, config.name)
    token = _read_dotenv_value(shell_dir / ".env", "NGRAM_AR_ENTITY_BRIDGE_TOKEN") or secrets.token_urlsafe(48)
    railway.configure_bridge(service=service, port=8080, token=token, person_id="ar_user", person_name="You")
    bridge_url = railway.ensure_domain(service=service, port=8080)
    configure_shell(shell_dir, bridge_url=bridge_url, token=token, preserve_worker_settings=True)
    railway.deploy(service=service)
    railway.wait_for_deployment(service=service)
    verify_bridge(bridge_url, token)
    click.echo(click.style("\n  Connected. Your ngram has a home.\n", fg="bright_blue", bold=True))
    click.echo("Hosted thinking and embeddings are configured. Postgres and the worker volume persist across deployments.")
    click.echo(f'\nOpen your world:\n  npm --prefix ngramAR run ngram-ar -- dev "{shell_dir}"')
    click.echo("In the app, New ngram guides you through pairing, appearance, and voice.")
