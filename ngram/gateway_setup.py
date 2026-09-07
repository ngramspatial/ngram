"""Interactive gateway + Cloudflare tunnel setup: ``ngram gateway setup``.

Walks through **both** together: shared bearer token + tunnel ingress to the local inference
gateway (Ollama stays behind the gateway). After setup, run ``ngram gateway on`` when the helper script is available.
"""

from __future__ import annotations

import secrets
from pathlib import Path

import click

from ngram.config import project_configs_dir
from ngram.utils.cloudflared_config import (
    default_cloudflared_config_path,
    tunnel_https_url_from_config,
)
from ngram.utils.dotenv_merge import merge_dotenv_keys
from ngram.utils.gateway_health_probe import print_gateway_health_block
from ngram.utils.gateway_script import gateway_script_available, run_gateway_script
from ngram.utils.tunnel_url_prompt import prompt_public_gateway_base_url


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


def _prompt_secret(prompt: str) -> str:
    v = click.prompt(prompt, hide_input=True, confirmation_prompt=False)
    return (v or "").strip()


def _prompt_line(prompt: str, *, default: str | None = None) -> str:
    if default is None:
        v = click.prompt(prompt, show_default=False)
    else:
        v = click.prompt(prompt, default=default, show_default=True)
    return (v or "").strip()


def _print_config_template(*, tunnel_name: str, gateway_host: str, gateway_port: int) -> None:
    svc = f"http://{gateway_host}:{gateway_port}"
    click.echo("")
    click.echo(
        "After `cloudflared tunnel create "
        + tunnel_name
        + "` you get a tunnel UUID and a credentials JSON path.\n"
        "Put something like this in your config (replace placeholders):\n"
    )
    click.echo(
        click.style("# ~/.cloudflared/config.yml (example)", fg="yellow") + "\n"
        "tunnel: <TUNNEL_UUID_FROM_CREATE>\n"
        "credentials-file: <PATH_TO_UUID.json>\n"
        "\n"
        "ingress:\n"
        "  - hostname: <your-public-hostname>\n"
        f"    service: {svc}\n"
        "  - service: http_status:404\n"
    )
    click.echo(
        "The tunnel hostname must forward to the gateway only — "
        + click.style("not", bold=True)
        + " a shared reverse proxy — terminate the tunnel only at this gateway.\n"
    )


def run_gateway_setup_wizard(
    *,
    tunnel_name: str,
    cloudflared_config: str,
    gateway_host: str,
    gateway_port: int,
    skip_tunnel_bootstrap: bool = False,
) -> None:
    root = _repo_root()
    env_path = _dotenv_path()
    cf_path = (
        Path(cloudflared_config.strip())
        if cloudflared_config.strip()
        else default_cloudflared_config_path()
    )

    click.echo("")
    click.echo(click.style("ngram gateway + tunnel setup", fg="cyan", bold=True))
    click.echo(
        "You will wire two things together:\n"
        "  • A bearer token — used by the inference gateway and by the gateway helper script health checks.\n"
        "  • Cloudflare Tunnel — public HTTPS → this machine’s gateway only "
        f"(`http://{gateway_host}:{gateway_port}`).\n"
    )
    click.echo(f"Repo: {root}")
    click.echo(f".env: {env_path}")
    click.echo(f"cloudflared config: {cf_path}\n")

    if not env_path.is_file():
        if click.confirm(".env not found — create it?", default=True):
            env_path.write_text(
                "# ngram — created by `ngram gateway setup`\n",
                encoding="utf-8",
            )
            click.echo(f"Created {env_path}")

    click.echo(click.style("— Step 1 — Bearer token (gateway + tunnel probes)", fg="green"))
    click.echo(
        "Set this once in `.env`. The same value must be used anywhere that calls your tunnel "
        "(e.g. Railway `NGRAM_INFERENCE_GATEWAY_TOKEN`).\n"
    )
    tok = _resolve_gateway_token(env_path)
    if tok:
        click.echo("Found an existing gateway token in .env.")
        if not click.confirm("Keep it?", default=True):
            tok = ""
    if not tok:
        if click.confirm("Generate a new random token?", default=True):
            tok = secrets.token_urlsafe(32)
        else:
            tok = _prompt_secret("Paste INFERENCE_GATEWAY_TOKEN")
    if not tok:
        raise click.ClickException("A gateway token is required.")

    click.echo(click.style("— Step 2 — Cloudflare Access (required, fail closed)", fg="green"))
    click.echo(
        "Create a self-hosted Access application for the tunnel hostname and a Service Auth "
        "policy with a service token. ngram verifies the Access JWT again at the origin.\n"
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
        raise click.ClickException(
            "All Cloudflare Access settings are required for a public tunnel."
        )

    click.echo(click.style("— Step 3 — Cloudflare Tunnel (public HTTPS → gateway)", fg="green"))
    click.echo(
        "Same automation as `ngram setup` hybrid: optional `cloudflared tunnel` + DNS + config.yml.\n"
        "Manual template below only if you skip automation and have no config yet.\n"
    )
    base_url = prompt_public_gateway_base_url(
        cf_path=cf_path,
        gateway_host=gateway_host,
        gateway_port=gateway_port,
        skip_tunnel_bootstrap=skip_tunnel_bootstrap,
        required=False,
    )
    if not base_url and not cf_path.is_file():
        click.echo(click.style(f"Config missing: {cf_path}", fg="yellow"))
        _print_config_template(
            tunnel_name=tunnel_name,
            gateway_host=gateway_host,
            gateway_port=gateway_port,
        )
        click.echo(
            "When the file exists, `ngram gateway on` runs:\n"
            f"  cloudflared tunnel run {tunnel_name}\n"
        )
    if click.confirm("Show the manual config template again?", default=False):
        _print_config_template(
            tunnel_name=tunnel_name,
            gateway_host=gateway_host,
            gateway_port=gateway_port,
        )

    click.echo(click.style("— Step 4 — Local gateway bind + Ollama URL", fg="green"))
    click.echo(
        f"The inference gateway should listen on {gateway_host}:{gateway_port} "
        "(default). Tunnel ingress must target that URL.\n"
        "Install extras if needed: pip install 'ngram[gateway]'\n"
    )
    ollama = _prompt_line(
        "Ollama base URL for the gateway (OLLAMA_HOST)", default="http://127.0.0.1:11434"
    )
    ollama = ollama.rstrip("/")

    click.echo(click.style("— Step 5 — Write .env", fg="green"))
    if not base_url and cf_path.is_file():
        detected = tunnel_https_url_from_config(cf_path)
        if detected and click.confirm(
            f"Use tunnel URL from config as NGRAM_INFERENCE_BASE_URL? ({detected})", default=True
        ):
            base_url = detected.rstrip("/")

    updates: dict[str, str] = {
        "INFERENCE_GATEWAY_TOKEN": tok,
        "NGRAM_INFERENCE_GATEWAY_TOKEN": tok,
        "OLLAMA_HOST": ollama,
        "CF_ACCESS_TEAM_DOMAIN": access_team_domain,
        "CF_ACCESS_AUD": access_aud,
        "NGRAM_CF_ACCESS_CLIENT_ID": access_client_id,
        "NGRAM_CF_ACCESS_CLIENT_SECRET": access_client_secret,
    }
    if base_url:
        updates["NGRAM_INFERENCE_BASE_URL"] = base_url

    if click.confirm(
        "Also set hybrid-oriented keys NGRAM_DEPLOYMENT_MODE=hybrid_railway "
        "and NGRAM_INFERENCE_PROVIDER=remote_gateway?",
        default=False,
    ):
        updates["NGRAM_DEPLOYMENT_MODE"] = "hybrid_railway"
        updates["NGRAM_INFERENCE_PROVIDER"] = "remote_gateway"

    merge_dotenv_keys(env_path, updates)
    click.echo(f"Updated {env_path} ({len(updates)} key(s))")

    click.echo(click.style("— Step 6 — Start the stack", fg="green"))
    if gateway_script_available():
        click.echo(
            "`ngram gateway on` starts Ollama (if needed), the inference gateway, "
            "and cloudflared using your .env token (via `scripts/gateway.ps1` or `scripts/gateway.sh`).\n"
        )
        if click.confirm("Run it now?", default=True):
            args = [
                "-TunnelName",
                tunnel_name,
                "-GatewayHost",
                gateway_host,
                "-GatewayPort",
                str(gateway_port),
            ]
            if str(cf_path) != str(default_cloudflared_config_path()):
                args += ["-CloudflaredConfig", str(cf_path)]
            rc = run_gateway_script("on", args)
            if rc != 0:
                click.echo(
                    "Startup failed — fix cloudflared config or install Ollama, then: ngram gateway on",
                    err=True,
                )
        elif click.confirm("Show gateway status only?", default=False):
            args = [
                "-TunnelName",
                tunnel_name,
                "-GatewayHost",
                gateway_host,
                "-GatewayPort",
                str(gateway_port),
            ]
            if str(cf_path) != str(default_cloudflared_config_path()):
                args += ["-CloudflaredConfig", str(cf_path)]
            run_gateway_script("status", args)
    else:
        click.echo(
            "Gateway helper script not found (run from repo root on Windows for `gateway.ps1`, "
            "or macOS/Linux for `gateway.sh`).\n"
            "Otherwise, in separate terminals:\n"
            "  ollama serve\n"
            f"  INFERENCE_GATEWAY_TOKEN=<token> python -m ngram.inference_gateway\n"
            f"  cloudflared tunnel run {tunnel_name}   # with --config pointing at your config.yml\n"
        )

    click.echo("\n--- Done ---\n")
    click.echo("Verify locally:")
    click.echo(
        f'  curl -sS -H "Authorization: Bearer <token>" http://{gateway_host}:{gateway_port}/health\n'
    )
    if base_url:
        click.echo("Verify through the tunnel (after cloudflared is up):")
        click.echo(f'  curl -sS -H "Authorization: Bearer <token>" {base_url}/health\n')
    click.echo("Hybrid: README.md (Hybrid deployment), .env.example, configs/default.yaml.\n")

    if click.confirm("Run gateway health checks now?", default=True):
        print_gateway_health_block(
            gateway_host=gateway_host,
            gateway_port=gateway_port,
            public_base_url=base_url,
            token=tok,
            access_client_id=access_client_id,
            access_client_secret=access_client_secret,
        )
