"""Shared prompts for public gateway URL + optional automated Cloudflare tunnel (used by setup wizards)."""

from __future__ import annotations

from pathlib import Path

import click

from ngram.utils.cloudflared_bootstrap import run_interactive_tunnel_bootstrap
from ngram.utils.cloudflared_config import find_cloudflared_executable, tunnel_https_url_from_config


def _line(prompt: str, *, default: str | None = None) -> str:
    if default is None:
        v = click.prompt(prompt, show_default=False)
    else:
        v = click.prompt(prompt, default=default, show_default=True)
    return (v or "").strip()


def prompt_public_gateway_base_url(
    *,
    cf_path: Path,
    gateway_host: str,
    gateway_port: int,
    skip_tunnel_bootstrap: bool,
    required: bool = True,
) -> str:
    """
    Resolve ``https://…`` for ``NGRAM_INFERENCE_BASE_URL`` (no trailing slash).

    If *required* is False (e.g. ``gateway setup`` only), returns ``\"\"`` when the user skips.
    """
    detected = tunnel_https_url_from_config(cf_path)
    base_url = ""

    if detected:
        click.echo(f"Tunnel URL from {cf_path}: {detected}")
        if click.confirm("Use this as NGRAM_INFERENCE_BASE_URL?", default=True):
            base_url = detected.rstrip("/")
        elif not skip_tunnel_bootstrap and find_cloudflared_executable():
            if click.confirm(
                "Run automatic tunnel setup instead (creates or reuses a tunnel, DNS, and config.yml)?",
                default=True,
            ):
                got = run_interactive_tunnel_bootstrap(
                    gateway_host=gateway_host,
                    gateway_port=gateway_port,
                    config_path=cf_path,
                )
                if got:
                    base_url = got.rstrip("/")
            if not base_url:
                if required:
                    base_url = _line("Public gateway URL (https://…, no path)").rstrip("/")
                else:
                    base_url = _line(
                        "Public tunnel URL (https://…, no path) — leave empty to set later",
                        default="",
                    ).rstrip("/")
    else:
        click.echo(
            f"No public hostname in {cf_path} yet — the wizard can run `cloudflared` for you "
            "(tunnel + DNS + config), or you can paste a URL if you already have one."
        )
        if not skip_tunnel_bootstrap and find_cloudflared_executable():
            if click.confirm("Run automatic Cloudflare Tunnel setup (recommended)?", default=True):
                got = run_interactive_tunnel_bootstrap(
                    gateway_host=gateway_host,
                    gateway_port=gateway_port,
                    config_path=cf_path,
                )
                if got:
                    base_url = got.rstrip("/")
        if not base_url:
            if required:
                base_url = _line("Public gateway URL (https://…, tunnel → gateway only)").rstrip("/")
            else:
                base_url = _line(
                    "Public tunnel URL (https://…, no path) — leave empty to set later",
                    default="",
                ).rstrip("/")
        if not base_url:
            detected = tunnel_https_url_from_config(cf_path)
            if detected:
                click.echo(f"Read tunnel URL after your edit: {detected}")
                if click.confirm("Use this?", default=True):
                    base_url = detected.rstrip("/")

    if not base_url and required:
        raise click.ClickException("NGRAM_INFERENCE_BASE_URL is required for hybrid mode.")
    return base_url
