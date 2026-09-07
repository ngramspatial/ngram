"""Automate Cloudflare Tunnel creation + DNS route + ``config.yml`` for the inference gateway."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import click
import yaml

from ngram.utils.cloudflared_config import find_cloudflared_executable, tunnel_https_url_from_config


def _strip_json_blob(stdout: str) -> str:
    s = stdout.strip()
    if s.startswith("["):
        return s
    # cloudflared may print a leading JSON object
    m = re.search(r"(\{.*\}|\[.*\])\s*$", s, re.DOTALL)
    if m:
        return m.group(1).strip()
    return s


def _parse_tunnel_id_from_create(stdout: str) -> str | None:
    try:
        data: Any = json.loads(_strip_json_blob(stdout))
    except json.JSONDecodeError:
        return None
    if isinstance(data, dict):
        tid = data.get("id") or data.get("tunnel_id")
        if isinstance(tid, str) and len(tid) > 10:
            return tid
    return None


def _cf_run(cmd: list[str], *, timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _tunnel_id_by_name(exe: str, name: str) -> str | None:
    r = _cf_run(
        [exe, "tunnel", "list", "-n", name, "-o", "json"],
        timeout=60,
    )
    if r.returncode != 0 or not (r.stdout or "").strip():
        return None
    try:
        data = json.loads((r.stdout or "").strip())
    except json.JSONDecodeError:
        return None
    if not isinstance(data, list) or not data:
        return None
    tid = data[0].get("id")
    return tid if isinstance(tid, str) else None


def _credentials_path(tunnel_id: str) -> Path:
    return Path.home() / ".cloudflared" / f"{tunnel_id}.json"


def _backup_config_if_needed(path: Path) -> None:
    if not path.is_file():
        return
    text = path.read_text(encoding="utf-8", errors="replace").strip()
    if not text:
        return
    ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    bak = path.with_name(f"{path.name}.bak.{ts}")
    shutil.copy2(path, bak)
    click.echo(f"Backed up existing config to {bak}")


def write_tunnel_config(
    *,
    config_path: Path,
    tunnel_id: str,
    hostname: str,
    gateway_host: str,
    gateway_port: int,
) -> None:
    cred = _credentials_path(tunnel_id)
    if not cred.is_file():
        raise click.ClickException(f"Missing credentials file (expected): {cred}")

    service = f"http://{gateway_host}:{gateway_port}"
    # Use forward slashes in YAML for Windows compatibility with cloudflared
    cred_str = str(cred).replace("\\", "/")

    body: dict[str, Any] = {
        "tunnel": tunnel_id,
        "credentials-file": cred_str,
        "ingress": [
            {"hostname": hostname, "service": service},
            {"service": "http_status:404"},
        ],
    }

    config_path.parent.mkdir(parents=True, exist_ok=True)
    _backup_config_if_needed(config_path)
    config_path.write_text(
        yaml.safe_dump(body, sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )


def run_interactive_tunnel_bootstrap(
    *,
    gateway_host: str,
    gateway_port: int,
    config_path: Path | None = None,
) -> str | None:
    """Create or reuse a tunnel, route DNS, write ``config.yml``. Returns ``https://hostname`` or ``None`` if skipped."""
    exe = find_cloudflared_executable()
    if not exe:
        click.echo(
            click.style("cloudflared not found.", fg="yellow")
            + " Install it, then re-run setup or use `ngram gateway setup`.\n"
        )
        return None

    cert = Path.home() / ".cloudflared" / "cert.pem"
    if not cert.is_file():
        click.echo(
            "You need a one-time Cloudflare login on this machine.\n"
            "A browser window will open to authorize tunnel management for your account.\n"
        )
        if not click.confirm("Run `cloudflared tunnel login` now?", default=True):
            return None
        rc = subprocess.call([exe, "tunnel", "login"])
        if rc != 0:
            click.echo("Login failed or cancelled. Run `cloudflared tunnel login` and try again.", err=True)
            return None

    cf_path = config_path if config_path is not None else Path.home() / ".cloudflared" / "config.yml"
    existing_url = tunnel_https_url_from_config(cf_path)
    if existing_url and click.confirm(
        f"Found existing tunnel URL in config ({existing_url}). Keep it and skip tunnel automation?",
        default=True,
    ):
        return existing_url.rstrip("/")

    click.echo(
        "\n"
        + click.style("Automatic Cloudflare Tunnel", fg="cyan", bold=True)
        + "\n"
        "This will: create a named tunnel (or reuse it), create a DNS record in your Cloudflare zone,\n"
        "and write ~/.cloudflared/config.yml so traffic hits only the inference gateway.\n"
        "You need a hostname whose DNS is on Cloudflare (e.g. ngram.yourdomain.com).\n"
    )

    default_name = "ngram-inference"
    tunnel_name = click.prompt("Tunnel name", default=default_name, show_default=True).strip()
    if not tunnel_name:
        raise click.ClickException("Tunnel name is required.")

    tid = _tunnel_id_by_name(exe, tunnel_name)
    if tid:
        click.echo(f"Reusing existing tunnel {tunnel_name!r} ({tid}).")
    else:
        click.echo(f"Creating tunnel {tunnel_name!r}…")
        r = _cf_run([exe, "tunnel", "create", "-o", "json", tunnel_name], timeout=120)
        if r.returncode != 0:
            click.echo((r.stderr or r.stdout or "tunnel create failed").strip(), err=True)
            return None
        tid = _parse_tunnel_id_from_create(r.stdout or "")
        if not tid:
            tid = _tunnel_id_by_name(exe, tunnel_name)
        if not tid:
            click.echo("Could not read tunnel id from cloudflared output. Try `cloudflared tunnel list`.", err=True)
            return None
        click.echo(f"Tunnel id: {tid}")

    if not _credentials_path(tid).is_file():
        raise click.ClickException(
            f"Missing credentials at {_credentials_path(tid)} — try `cloudflared tunnel delete {tunnel_name}` "
            "and re-run, or fix files under ~/.cloudflared/."
        )

    hostname = click.prompt(
        "Public hostname (FQDN on your Cloudflare zone, e.g. inference.example.com)",
        default="",
        show_default=False,
    ).strip()
    if not hostname:
        raise click.ClickException("Hostname is required for DNS routing.")

    click.echo(f"Creating DNS route: {hostname} → tunnel {tunnel_name}…")
    r = _cf_run(
        [exe, "tunnel", "route", "dns", "--overwrite-dns", tunnel_name, hostname],
        timeout=120,
    )
    if r.returncode != 0:
        msg = (r.stderr or r.stdout or "").strip()
        click.echo(
            "DNS route failed — check that the domain is on Cloudflare and the name is correct.\n" + msg,
            err=True,
        )
        if not click.confirm("Continue anyway and write config (you can fix DNS in the dashboard)?", default=False):
            return None

    write_tunnel_config(
        config_path=cf_path,
        tunnel_id=tid,
        hostname=hostname,
        gateway_host=gateway_host,
        gateway_port=gateway_port,
    )
    click.echo(click.style(f"Wrote {cf_path}", fg="green"))

    base = f"https://{hostname}"
    click.echo(f"Public gateway base URL: {click.style(base, fg='green', bold=True)}")
    click.echo(
        f"On Windows, start the stack with the same tunnel name, e.g. "
        f"{click.style('ngram gateway on --tunnel-name ' + tunnel_name, fg='yellow')}"
    )
    return base
