"""Interactive local-first product setup: ``ngram setup``.

The normal paths keep the Entity runtime on this machine and use either Ollama
or a hosted API. The home-gateway + Cloudflare + Railway topology remains an
explicit advanced profile rather than a first-run requirement.
"""

from __future__ import annotations

import asyncio
import os
import secrets
import shutil
import subprocess
from pathlib import Path

import click

from ngram.config import project_configs_dir
from ngram.genesis import creator
from ngram.utils.cloudflared_config import default_cloudflared_config_path
from ngram.utils.gateway_health_probe import print_gateway_health_block
from ngram.utils.setup_preflight import collect_preflight, format_preflight_text
from ngram.utils.tunnel_url_prompt import prompt_public_gateway_base_url
from ngram.utils.dotenv_merge import merge_dotenv_keys
from ngram.utils.gateway_script import gateway_script_available, run_gateway_script


def _repo_root() -> Path:
    return project_configs_dir().parent


def _dotenv_path() -> Path:
    return _repo_root() / ".env"


def _read_dotenv_value(path: Path, key: str) -> str:
    if not path.is_file():
        return ""
    for raw in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, rest = line.partition("=")
        if k.strip() == key:
            return rest.strip().strip('"').strip("'")
    return ""


def _resolve_gateway_token(env_path: Path) -> str:
    for k in ("INFERENCE_GATEWAY_TOKEN", "NGRAM_INFERENCE_GATEWAY_TOKEN"):
        v = _read_dotenv_value(env_path, k)
        if v:
            return v
    return ""


def _activate_process_environment(updates: dict[str, str]) -> None:
    """Keep this setup process aligned with values just persisted to ``.env``."""
    for key, value in updates.items():
        os.environ[key] = value


def _prompt_secret(prompt: str) -> str:
    v = click.prompt(prompt, hide_input=True, confirmation_prompt=False)
    return (v or "").strip()


def _prompt_line(prompt: str, *, default: str | None = None) -> str:
    if default is None:
        v = click.prompt(prompt, show_default=False)
    else:
        v = click.prompt(prompt, default=default, show_default=True)
    return (v or "").strip()


def _list_entity_names() -> list[str]:
    ent_dir = project_configs_dir() / "entities"
    if not ent_dir.is_dir():
        return []
    names: list[str] = []
    for p in sorted(ent_dir.glob("*.yaml")):
        if p.name.startswith("."):
            continue
        if p.name.endswith(".example.yaml"):
            continue
        names.append(p.stem)
    return names


def _railway_bin() -> str | None:
    return shutil.which("railway")


def _railway_ok() -> bool:
    b = _railway_bin()
    if not b:
        return False
    r = subprocess.run(
        [b, "whoami"],
        cwd=_repo_root(),
        capture_output=True,
        text=True,
        timeout=60,
    )
    return r.returncode == 0


def _railway_linked() -> bool:
    b = _railway_bin()
    if not b:
        return False
    r = subprocess.run(
        [b, "status"],
        cwd=_repo_root(),
        capture_output=True,
        text=True,
        timeout=30,
    )
    if r.returncode != 0:
        return False
    return "Project:" in (r.stdout or "")


def _ensure_railway_cli_ready() -> bool:
    """Ensure logged in and linked when the user wants Railway automation."""
    b = _railway_bin()
    if not b:
        return False
    if not _railway_ok():
        click.echo("Railway CLI needs a login.")
        if click.confirm("Run `railway login` now?", default=True):
            subprocess.call([b, "login"], cwd=_repo_root())
        if not _railway_ok():
            click.echo(
                "Still not logged in — run `railway login` from the repo root and retry.", err=True
            )
            return False
    if not _railway_linked():
        click.echo(
            "This folder must be linked to your Railway project (the one with ngram-worker / ngram-api)."
        )
        if click.confirm("Run `railway link` now?", default=True):
            subprocess.call([b, "link"], cwd=_repo_root())
        if not _railway_linked():
            click.echo(
                "Still not linked — from repo root: `railway link` and pick the project with your services.",
                err=True,
            )
            return False
    return True


DEFAULT_GATEWAY_HOST = "127.0.0.1"
DEFAULT_GATEWAY_PORT = 8010

HOSTED_PROVIDERS = (
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
)

HOSTED_PROVIDER_DEFAULTS: dict[str, dict[str, str]] = {
    "openai": {
        "key_env": "OPENAI_API_KEY",
        "model": "gpt-5.6-sol",
        "embedding_model": "text-embedding-3-small",
    },
    "venice": {
        "key_env": "VENICE_API_KEY",
        "model": "qwen3-235b-a22b-instruct-2507",
        "embedding_model": "text-embedding-bge-m3",
    },
    "anthropic": {"key_env": "ANTHROPIC_API_KEY"},
    "gemini": {"key_env": "GEMINI_API_KEY"},
    "openrouter": {"key_env": "OPENROUTER_API_KEY"},
    "xai": {"key_env": "XAI_API_KEY"},
    "groq": {"key_env": "GROQ_API_KEY"},
    "together": {"key_env": "TOGETHER_API_KEY"},
    "fireworks": {"key_env": "FIREWORKS_API_KEY"},
    "mistral": {"key_env": "MISTRAL_API_KEY"},
    "deepseek": {"key_env": "DEEPSEEK_API_KEY"},
    "custom": {"key_env": "NGRAM_API_KEY"},
}


def _railway_set(service: str, pairs: list[str]) -> bool:
    b = _railway_bin()
    if not b:
        return False
    r = subprocess.run(
        [b, "variable", "set", "-s", service, *pairs],
        cwd=_repo_root(),
        capture_output=True,
        text=True,
        timeout=120,
    )
    if r.returncode != 0:
        click.echo((r.stderr or r.stdout or "railway variable set failed").strip(), err=True)
    return r.returncode == 0


def _railway_set_stdin(service: str, key: str, value: str) -> bool:
    b = _railway_bin()
    if not b:
        return False
    r = subprocess.run(
        [b, "variable", "set", "-s", service, key, "--stdin"],
        input=value,
        text=True,
        cwd=_repo_root(),
        capture_output=True,
        timeout=120,
    )
    if r.returncode != 0:
        click.echo(
            (r.stderr or r.stdout or "railway variable set --stdin failed").strip(), err=True
        )
    return r.returncode == 0


def _run_npm(script: str) -> int:
    npm = shutil.which("npm")
    if not npm:
        return 127
    return subprocess.call([npm, "run", script], cwd=_repo_root())


def _print_railway_cheat_sheet(entity: str, base_url: str, pg_service: str) -> None:
    click.echo("\nRailway (copy-paste if CLI steps were skipped):\n")
    db_ref = "${{" + pg_service + ".DATABASE_URL}}"
    click.echo(
        f"  railway variable set -s ngram-worker DATABASE_URL={db_ref}\n"
        f"  railway variable set -s ngram-api DATABASE_URL={db_ref}\n"
    )
    common = (
        f"NGRAM_DEPLOYMENT_MODE=hybrid_railway NGRAM_INFERENCE_PROVIDER=remote_gateway "
        f"NGRAM_INFERENCE_BASE_URL={base_url} NGRAM_ENTITY={entity} "
    )
    click.echo(f"  railway variable set -s ngram-worker {common}NGRAM_RAILWAY_ROLE=worker")
    click.echo(
        "  printf '%s' '<token>' | railway variable set -s ngram-worker NGRAM_INFERENCE_GATEWAY_TOKEN --stdin"
    )
    click.echo(
        "  printf '%s' '<access-client-id>' | railway variable set -s ngram-worker NGRAM_CF_ACCESS_CLIENT_ID --stdin"
    )
    click.echo(
        "  printf '%s' '<access-client-secret>' | railway variable set -s ngram-worker NGRAM_CF_ACCESS_CLIENT_SECRET --stdin"
    )
    click.echo(f"  railway variable set -s ngram-api {common}NGRAM_RAILWAY_ROLE=api")
    click.echo(
        "  printf '%s' '<token>' | railway variable set -s ngram-api NGRAM_INFERENCE_GATEWAY_TOKEN --stdin"
    )
    click.echo(
        "  printf '%s' '<access-client-id>' | railway variable set -s ngram-api NGRAM_CF_ACCESS_CLIENT_ID --stdin"
    )
    click.echo(
        "  printf '%s' '<access-client-secret>' | railway variable set -s ngram-api NGRAM_CF_ACCESS_CLIENT_SECRET --stdin"
    )
    click.echo("\n  railway up -s ngram-worker\n  railway up -s ngram-api\n")
    click.echo(
        "(Use the same gateway and Access service credentials as .env; base URL is tunnel root, no path.)\n"
    )


def _entity_section() -> str | None:
    """Returns entity name if picked or created; ``None`` if skipped or still none."""
    click.echo("\n--- Entity ---\n")
    names = _list_entity_names()
    if not names:
        click.echo("No entities in configs/entities/ yet.")
        if click.confirm("Run the entity creator wizard now?", default=True):
            return creator.run_wizard()
        click.echo("Later: ngram create")
        return None
    click.echo("Entities: " + ", ".join(names))
    choice = click.prompt(
        "[c]reate new  /  [p]ick existing  /  [s]kip",
        type=click.Choice(["c", "p", "s"], case_sensitive=False),
        default="p",
    )
    if choice == "c":
        return creator.run_wizard()
    if choice == "p":
        pick = click.prompt(
            "Entity name",
            type=click.Choice(names, case_sensitive=False),
        )
        click.echo(f"Selected {pick}.")
        return pick
    return None


def _resolve_entity_name_for_railway(chosen: str | None) -> str:
    if chosen and chosen.strip():
        return chosen.strip()
    names = _list_entity_names()
    if len(names) == 1:
        return names[0]
    if len(names) > 1:
        return click.prompt(
            "Entity name for Railway (NGRAM_ENTITY)",
            type=click.Choice(names, case_sensitive=False),
            default=names[0],
        )
    typed = _prompt_line(
        "Entity name for Railway (must match configs/entities/<name>.yaml — Enter to skip)"
    )
    return typed.strip()


def _run_local_flow(env_path: Path) -> None:
    click.echo("\n--- Local Ollama (no cloud account, tunnel, or remote worker) ---\n")
    click.echo(
        "The Entity runtime, memory, tools, and inference all stay on this machine.\n"
        "The default Ollama URL works for most installs; change it only if yours is remote.\n"
    )
    updates: dict[str, str] = {
        "NGRAM_DEPLOYMENT_MODE": "local",
        "NGRAM_INFERENCE_PROVIDER": "local",
        "NGRAM_INFERENCE_BASE_URL": "",
        "NGRAM_INFERENCE_PASS_NUM_CTX": "true",
    }
    if click.confirm("Use the default Ollama URL (http://127.0.0.1:11434)?", default=True):
        updates["OLLAMA_HOST"] = "http://127.0.0.1:11434"
    else:
        updates["OLLAMA_HOST"] = _prompt_line("Ollama URL").rstrip("/")
    if click.confirm("Set TELEGRAM_TOKEN?", default=False):
        t = _prompt_secret("TELEGRAM_TOKEN")
        if t:
            updates["TELEGRAM_TOKEN"] = t
    if click.confirm("Set DISCORD_TOKEN?", default=False):
        t = _prompt_secret("DISCORD_TOKEN")
        if t:
            updates["DISCORD_TOKEN"] = t
    merge_dotenv_keys(env_path, updates)
    _activate_process_environment(updates)
    click.echo(f"\nUpdated {env_path} ({len(updates)} key(s))")


def _run_hybrid_env_and_home(
    env_path: Path,
    *,
    gateway_host: str,
    gateway_port: int,
    skip_tunnel_bootstrap: bool,
) -> tuple[str, str]:
    click.echo("\n--- Hybrid (home brain + Railway body) ---\n")
    click.echo(
        "What you are setting up:\n"
        "  • Home: Ollama + the inference gateway + a Cloudflare Tunnel (your GPU stays here).\n"
        "  • Cloud: Railway worker + API + Postgres (always-on presence and storage).\n"
        "The tunnel must expose only the gateway URL — not a generic reverse proxy.\n"
    )

    updates: dict[str, str] = {}

    tok = _resolve_gateway_token(env_path)
    if not tok:
        if click.confirm("Generate a new shared gateway token (home + Railway)?", default=True):
            tok = secrets.token_urlsafe(32)
        else:
            tok = _prompt_secret("Paste INFERENCE_GATEWAY_TOKEN (same value Railway will use)")
    if not tok:
        raise click.ClickException("A gateway token is required for the hybrid stack.")
    updates["INFERENCE_GATEWAY_TOKEN"] = tok
    updates["NGRAM_INFERENCE_GATEWAY_TOKEN"] = tok

    click.echo(
        "\nCloudflare Access is required before the tunnel can open. Create a self-hosted "
        "application for the tunnel hostname, add a Service Auth policy, and generate a service token.\n"
    )
    access_team_domain = _read_dotenv_value(env_path, "CF_ACCESS_TEAM_DOMAIN")
    access_aud = _read_dotenv_value(env_path, "CF_ACCESS_AUD")
    access_client_id = _read_dotenv_value(env_path, "NGRAM_CF_ACCESS_CLIENT_ID")
    access_client_secret = _read_dotenv_value(env_path, "NGRAM_CF_ACCESS_CLIENT_SECRET")
    if not access_team_domain:
        access_team_domain = _prompt_line(
            "Cloudflare Access team domain",
            default="https://your-team.cloudflareaccess.com",
        )
    if not access_aud:
        access_aud = _prompt_line("Access application AUD tag")
    if not access_client_id:
        access_client_id = _prompt_line("Access service-token Client ID")
    if not access_client_secret:
        access_client_secret = _prompt_secret("Access service-token Client Secret")
    if not all((access_team_domain, access_aud, access_client_id, access_client_secret)):
        raise click.ClickException("All Cloudflare Access settings are required for hybrid setup.")
    updates["CF_ACCESS_TEAM_DOMAIN"] = access_team_domain
    updates["CF_ACCESS_AUD"] = access_aud
    updates["NGRAM_CF_ACCESS_CLIENT_ID"] = access_client_id
    updates["NGRAM_CF_ACCESS_CLIENT_SECRET"] = access_client_secret

    cf_path = default_cloudflared_config_path()
    base_url = prompt_public_gateway_base_url(
        cf_path=cf_path,
        gateway_host=gateway_host,
        gateway_port=gateway_port,
        skip_tunnel_bootstrap=skip_tunnel_bootstrap,
        required=True,
    )
    updates["NGRAM_DEPLOYMENT_MODE"] = "hybrid_railway"
    updates["NGRAM_INFERENCE_PROVIDER"] = "remote_gateway"
    updates["NGRAM_INFERENCE_BASE_URL"] = base_url

    ollama = _prompt_line("Ollama URL for home stack", default="http://127.0.0.1:11434")
    if ollama:
        updates["OLLAMA_HOST"] = ollama.rstrip("/")

    if click.confirm("Optional: set FIRECRAWL_API_KEY in .env?", default=False):
        fc = _prompt_secret("FIRECRAWL_API_KEY")
        if fc:
            updates["FIRECRAWL_API_KEY"] = fc

    if click.confirm(
        "Optional: DATABASE_URL in .env (local worker test against Postgres)?", default=False
    ):
        db = _prompt_line("DATABASE_URL")
        if db:
            updates["DATABASE_URL"] = db

    if click.confirm("Optional: S3 attachment vars in .env (hybrid durable blobs)?", default=False):
        s3: dict[str, str] = {}
        for key, label in (
            ("NGRAM_S3_ENDPOINT_URL", "NGRAM_S3_ENDPOINT_URL"),
            ("NGRAM_S3_BUCKET", "NGRAM_S3_BUCKET"),
            ("NGRAM_S3_ACCESS_KEY", "NGRAM_S3_ACCESS_KEY"),
            ("NGRAM_S3_SECRET_KEY", "NGRAM_S3_SECRET_KEY"),
        ):
            v = _prompt_secret(label)
            if v:
                s3[key] = v
        updates.update(s3)
        if s3:
            updates["NGRAM_ATTACHMENTS_BACKEND"] = "object_s3_compat"

    merge_dotenv_keys(env_path, updates)
    _activate_process_environment(updates)
    click.echo(f"\nUpdated {env_path} ({len(updates)} key(s))")

    if click.confirm(
        "Run npm run ollama:reset (model pulls / Ollama defaults on Windows)?", default=False
    ):
        rc = _run_npm("ollama:reset")
        if rc != 0:
            click.echo(
                f"npm run ollama:reset exited {rc} (install Node/npm or run scripts/ollama-reset.ps1).",
                err=True,
            )

    gateway_rc: int | None = None
    if gateway_script_available():
        if click.confirm(
            "Start home stack now (ollama + inference gateway + cloudflared)?", default=True
        ):
            gateway_rc = run_gateway_script("on")
            if gateway_rc != 0:
                click.echo(
                    "gateway on failed — fix cloudflared config (~/.cloudflared/config.yml) and run: ngram gateway on",
                    err=True,
                )
        elif click.confirm("Show gateway status only?", default=False):
            run_gateway_script("status")
    else:
        click.echo(
            "\nHome stack: run `ngram gateway on` after cloudflared is configured "
            "(requires `scripts/gateway.ps1` on Windows or `scripts/gateway.sh` on macOS/Linux).\n"
            "Else: ollama serve, then INFERENCE_GATEWAY_TOKEN=… python -m ngram.inference_gateway, "
            "then cloudflared tunnel run …\n"
        )

    if click.confirm(
        "Run gateway health checks (GET /health with your token — local"
        + (" + tunnel" if base_url else "")
        + ")?",
        default=(gateway_rc is None or gateway_rc == 0),
    ):
        print_gateway_health_block(
            gateway_host=gateway_host,
            gateway_port=gateway_port,
            public_base_url=base_url,
            token=tok,
            access_client_id=access_client_id,
            access_client_secret=access_client_secret,
        )

    return base_url, tok


def _hosted_prompt(
    label: str,
    *,
    default: str = "",
) -> str:
    if default:
        return _prompt_line(label, default=default)
    return _prompt_line(label)


def _run_hosted_flow(env_path: Path) -> None:
    """Configure a provider API while keeping the full Entity runtime local."""
    click.echo("\n--- Hosted API brain (no Cloudflare, tunnel, or remote worker) ---\n")
    click.echo(
        "Model prompts and embedding inputs go to the provider you choose. Identity, memory,\n"
        "relationships, tool execution, and the AR bridge remain in the local ngram runtime.\n"
        "Configured web or messaging tools may make their own network calls. The API key is\n"
        "written only to the gitignored .env file and is never sent to the browser.\n"
    )
    provider = click.prompt(
        "Provider",
        type=click.Choice(HOSTED_PROVIDERS, case_sensitive=False),
        default="openai",
    ).lower()
    defaults = HOSTED_PROVIDER_DEFAULTS[provider]
    key_env = defaults["key_env"]
    if provider == "custom":
        base_url = _prompt_line("OpenAI-compatible API prefix (for example https://host/v1)")
        key_env = _prompt_line("API key environment variable", default=key_env)
    else:
        base_url = ""

    chat_model = _hosted_prompt("Chat model ID", default=defaults.get("model", ""))
    embedding_model = _hosted_prompt(
        "Embedding model ID (must support 768 output dimensions)",
        default=defaults.get("embedding_model", ""),
    )
    existing_key = _read_dotenv_value(env_path, key_env)
    api_key = existing_key or _prompt_secret(key_env)

    from ngram.lab import _probe_provider, validate_api_base_url, validate_secret

    api_key = validate_secret(api_key)
    base_url = validate_api_base_url(base_url, required=provider == "custom")
    if click.confirm("Verify the model catalog and 768-dimensional memory now?", default=True):
        click.echo("Checking hosted inference and memory embeddings…")
        model_found, dimensions = asyncio.run(
            _probe_provider(
                provider_name=provider,
                model=chat_model,
                embedding_model=embedding_model,
                base_url=base_url,
                api_key=api_key,
            )
        )
        if not model_found:
            click.echo(
                "Warning: the provider responded, but that chat model was not in its catalog.",
                err=True,
            )
        click.echo(f"[ok] Hosted memory returned {dimensions} dimensions.")

    updates = {
        "NGRAM_DEPLOYMENT_MODE": "local",
        "NGRAM_INFERENCE_PROVIDER": provider,
        "NGRAM_INFERENCE_BASE_URL": base_url,
        "NGRAM_INFERENCE_API_KEY_ENV": key_env,
        "NGRAM_INFERENCE_MODEL": chat_model,
        "NGRAM_INFERENCE_PASS_NUM_CTX": "false",
        "NGRAM_EMBEDDING_MODEL": embedding_model,
        "NGRAM_EMBEDDING_DIMENSIONS": "768",
        key_env: api_key,
    }
    merge_dotenv_keys(env_path, updates)
    _activate_process_environment(updates)
    try:
        env_path.chmod(0o600)
    except OSError:
        pass
    click.echo(f"\n[ok] {provider} is configured. No tunnel was created or started.")


def _prepare_local_models(entity_name: str | None) -> None:
    if not entity_name:
        return
    if not shutil.which("ollama"):
        click.echo(
            "\nOllama is not on PATH. Install it, then run:\n"
            f"  uv run ngram talk {entity_name} --ollama --pull-models\n"
        )
        return
    if not click.confirm(
        "Start Ollama and download this Entity's configured models now? (downloads may be large)",
        default=True,
    ):
        return
    from ngram.config import load_entity_config, load_harness_config
    from ngram.utils.ollama_bootstrap import bootstrap_stack

    entity = load_entity_config(entity_name, load_harness_config())
    bootstrap_stack(entity, click.echo, pull_models=True)


def _railway_volume_exists(service: str, mount: str) -> bool | None:
    """Check whether *service* has a volume at *mount*. Returns ``None`` when the check cannot run."""
    b = _railway_bin()
    if not b:
        return None
    r = subprocess.run(
        [b, "volume", "list"],
        cwd=_repo_root(),
        capture_output=True,
        text=True,
        timeout=60,
    )
    if r.returncode != 0:
        return None
    return mount in (r.stdout or "")


def _railway_variable_get(service: str, key: str) -> str | None:
    """Read a single Railway variable. Returns ``None`` if the CLI fails."""
    b = _railway_bin()
    if not b:
        return None
    r = subprocess.run(
        [b, "variable", "list", "-s", service],
        cwd=_repo_root(),
        capture_output=True,
        text=True,
        timeout=60,
    )
    if r.returncode != 0:
        return None
    for line in (r.stdout or "").splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            if k.strip() == key:
                return v.strip()
    return ""


def _run_hybrid_volume_setup() -> None:
    """Ensure the ngram-worker has a persistent volume at /app/data."""
    click.echo("\n--- Persistent volume (worker data that survives redeploys) ---\n")
    click.echo(
        "The worker needs a Railway volume mounted at /app/data for knowledge.md,\n"
        "journal.md, soma state, and any files the entity creates. Without it,\n"
        "those files are lost every time the container redeploys.\n"
    )

    has_volume = _railway_volume_exists("ngram-worker", "/app/data")
    if has_volume is None:
        click.echo("Could not check volumes (Railway CLI unavailable or not linked).")
        click.echo("Manual step: railway volume add --mount-path /app/data -s ngram-worker\n")
    elif has_volume:
        click.echo("Volume at /app/data detected — good.\n")
    else:
        click.echo("No volume at /app/data found on ngram-worker.")
        if click.confirm("Create one now?", default=True):
            b = _railway_bin()
            if b:
                r = subprocess.run(
                    [b, "volume", "add", "--mount-path", "/app/data", "-s", "ngram-worker"],
                    cwd=_repo_root(),
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                if r.returncode == 0:
                    click.echo("Volume created at /app/data.")
                else:
                    click.echo(
                        (r.stderr or r.stdout or "railway volume add failed").strip(),
                        err=True,
                    )
                    click.echo(
                        "Create manually: railway volume add --mount-path /app/data -s ngram-worker"
                    )
        else:
            click.echo(
                "Skipped. Create later: railway volume add --mount-path /app/data -s ngram-worker\n"
            )

    ws_val = _railway_variable_get("ngram-worker", "NGRAM_EXECUTION_WORKSPACE_DIR")
    if ws_val is None:
        click.echo(
            "Could not check variables. Ensure NGRAM_EXECUTION_WORKSPACE_DIR=/app/data is set.\n"
        )
    elif ws_val == "/app/data":
        click.echo("NGRAM_EXECUTION_WORKSPACE_DIR already set to /app/data.\n")
    else:
        click.echo(
            "NGRAM_EXECUTION_WORKSPACE_DIR tells the harness where to store knowledge,\n"
            "journal, soma state, and workspace files. It should point at the volume mount.\n"
        )
        if click.confirm(
            "Set NGRAM_EXECUTION_WORKSPACE_DIR=/app/data on ngram-worker?", default=True
        ):
            _railway_set("ngram-worker", ["NGRAM_EXECUTION_WORKSPACE_DIR=/app/data"])
            click.echo("Set.\n")
        else:
            click.echo(
                "Skipped. Set later: railway variable set -s ngram-worker NGRAM_EXECUTION_WORKSPACE_DIR=/app/data\n"
            )

    click.echo("See config/examples/hybrid_railway.yaml for a deployment example.\n")


def _run_hybrid_railway(entity_choice: str | None, base_url: str, tok: str) -> None:
    entity = _resolve_entity_name_for_railway(entity_choice)
    if not entity:
        click.echo("Skipping Railway CLI — set NGRAM_ENTITY after you add an entity YAML.")
        click.echo(
            "Inference: tunnel must end at the gateway only; see .env.example and ngram/inference_gateway/."
        )
        return

    env_path = _dotenv_path()
    access_client_id = _read_dotenv_value(env_path, "NGRAM_CF_ACCESS_CLIENT_ID")
    access_client_secret = _read_dotenv_value(env_path, "NGRAM_CF_ACCESS_CLIENT_SECRET")
    if not access_client_id or not access_client_secret:
        raise click.ClickException(
            "Cloudflare Access service-token credentials are missing from .env."
        )

    pg_service = _prompt_line(
        "Railway Postgres plugin service name (for DATABASE_URL reference)", default="Postgres"
    )

    railway_ok = False
    if _railway_bin():
        if _ensure_railway_cli_ready():
            railway_ok = True
            if click.confirm(
                "Apply hybrid variables on Railway (ngram-worker + ngram-api)?", default=True
            ):
                db_tok = "${{" + pg_service + ".DATABASE_URL}}"
                _railway_set("ngram-worker", [f"DATABASE_URL={db_tok}"])
                _railway_set("ngram-api", [f"DATABASE_URL={db_tok}"])
                wvars = [
                    "NGRAM_DEPLOYMENT_MODE=hybrid_railway",
                    "NGRAM_INFERENCE_PROVIDER=remote_gateway",
                    f"NGRAM_INFERENCE_BASE_URL={base_url}",
                    f"NGRAM_ENTITY={entity}",
                    "NGRAM_RAILWAY_ROLE=worker",
                ]
                avs = [
                    "NGRAM_DEPLOYMENT_MODE=hybrid_railway",
                    "NGRAM_INFERENCE_PROVIDER=remote_gateway",
                    f"NGRAM_INFERENCE_BASE_URL={base_url}",
                    f"NGRAM_ENTITY={entity}",
                    "NGRAM_RAILWAY_ROLE=api",
                ]
                _railway_set("ngram-worker", wvars)
                _railway_set("ngram-api", avs)
                _railway_set_stdin("ngram-worker", "NGRAM_INFERENCE_GATEWAY_TOKEN", tok)
                _railway_set_stdin("ngram-api", "NGRAM_INFERENCE_GATEWAY_TOKEN", tok)
                _railway_set_stdin("ngram-worker", "NGRAM_CF_ACCESS_CLIENT_ID", access_client_id)
                _railway_set_stdin(
                    "ngram-worker", "NGRAM_CF_ACCESS_CLIENT_SECRET", access_client_secret
                )
                _railway_set_stdin("ngram-api", "NGRAM_CF_ACCESS_CLIENT_ID", access_client_id)
                _railway_set_stdin(
                    "ngram-api", "NGRAM_CF_ACCESS_CLIENT_SECRET", access_client_secret
                )
                if click.confirm("Set TELEGRAM_TOKEN on ngram-worker (from stdin)?", default=False):
                    tg = _prompt_secret("TELEGRAM_TOKEN")
                    if tg:
                        _railway_set_stdin("ngram-worker", "TELEGRAM_TOKEN", tg)
                if click.confirm("Set DISCORD_TOKEN on ngram-worker (from stdin)?", default=False):
                    dc = _prompt_secret("DISCORD_TOKEN")
                    if dc:
                        _railway_set_stdin("ngram-worker", "DISCORD_TOKEN", dc)
    else:
        click.echo("Railway CLI not on PATH — install it, then use the cheat sheet below.")

    if railway_ok:
        _run_hybrid_volume_setup()

    if railway_ok and click.confirm(
        "Deploy worker + API now (`railway up` — publishes to your Railway project)?",
        default=True,
    ):
        rb = _railway_bin()
        if rb:
            subprocess.call([rb, "up", "-s", "ngram-worker", "-d"], cwd=_repo_root())
            subprocess.call([rb, "up", "-s", "ngram-api", "-d"], cwd=_repo_root())

    _print_railway_cheat_sheet(entity, base_url, pg_service)

    click.echo(
        "Inference: tunnel must end at the gateway only; see .env.example and ngram/inference_gateway/."
    )


def run_setup_wizard(
    *,
    profile: str = "ask",
    gateway_host: str = DEFAULT_GATEWAY_HOST,
    gateway_port: int = DEFAULT_GATEWAY_PORT,
    skip_tunnel_bootstrap: bool = False,
) -> None:
    root = _repo_root()
    env_path = _dotenv_path()

    click.echo("")
    click.echo(click.style("ngram setup", fg="cyan", bold=True))
    click.echo(
        "Start locally in minutes. Cloudflare, Railway, and public networking are not required.\n"
        "Choose hosted for the fastest path on any computer, local for Ollama and full privacy,\n"
        "or hybrid only when you intentionally want an always-on cloud body using a home GPU.\n"
    )
    click.echo(f"Repo: {root}")
    click.echo(f".env: {env_path}\n")

    if not env_path.is_file():
        if click.confirm(".env not found — create it?", default=True):
            env_path.write_text(
                "# ngram — created by `ngram setup`\n",
                encoding="utf-8",
            )
            click.echo(f"Created {env_path}")

    p = profile.strip().lower()
    if p == "ask":
        click.echo(
            "Setup modes:\n"
            "  hosted  Fastest: provider API + complete Entity runtime on this machine\n"
            "  local   Private: Ollama + complete Entity runtime on this machine\n"
            "  hybrid  Advanced: home inference gateway + Cloudflare Access + Railway\n"
        )
        p = click.prompt(
            "Choose a setup mode",
            type=click.Choice(["hosted", "local", "hybrid"], case_sensitive=False),
            default="hosted",
        ).lower()

    hybrid_base: str | None = None
    hybrid_tok: str | None = None
    if p == "hybrid":
        click.echo(click.style("\nReadiness (fix any ✗ before continuing)\n", fg="cyan", bold=True))
        click.echo(format_preflight_text(collect_preflight(repo_root=root)))
        click.echo("")
        gh = gateway_host.strip() or DEFAULT_GATEWAY_HOST
        gp = gateway_port if gateway_port > 0 else DEFAULT_GATEWAY_PORT
        hybrid_base, hybrid_tok = _run_hybrid_env_and_home(
            env_path,
            gateway_host=gh,
            gateway_port=gp,
            skip_tunnel_bootstrap=skip_tunnel_bootstrap,
        )
    elif p == "local":
        _run_local_flow(env_path)
    elif p == "hosted":
        _run_hosted_flow(env_path)
    else:
        raise click.ClickException(f"Unknown profile: {profile!r}")

    entity_choice = _entity_section()
    if p == "local":
        _prepare_local_models(entity_choice)
    if p == "hybrid" and hybrid_base is not None and hybrid_tok is not None:
        click.echo("\n--- Railway (cloud body) ---\n")
        _run_hybrid_railway(entity_choice, hybrid_base, hybrid_tok)

    click.echo("\n--- Done ---\n")
    if entity_choice:
        ollama_flag = " --ollama" if p == "local" else ""
        click.echo("You are ready:")
        click.echo(f"  • Talk now:       uv run ngram talk {entity_choice}{ollama_flag}")
        click.echo(f"  • Run presence:   uv run ngram run {entity_choice}{ollama_flag}")
        click.echo(f"  • Add a body:     uv run ngram lab up {entity_choice} --lan")
    else:
        click.echo(
            "Next: create an Entity with `uv run ngram create`, then run "
            "`uv run ngram talk <entity>`."
        )
    if p == "hybrid":
        click.echo(
            "Keep the protected home gateway running whenever the Railway worker should think."
        )
    else:
        click.echo("No Cloudflare tunnel or Railway service is part of this setup.")
    click.echo("")
