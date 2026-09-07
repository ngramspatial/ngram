"""Safe bootstrap and foreground supervisor for the thin-host WebXR lab."""

from __future__ import annotations

import asyncio
import json
import os
import platform
import secrets
import shutil
import socket
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlsplit

import click
import psutil

from ngram.config import load_entity_config, load_harness_config, project_configs_dir
from ngram.inference import build_inference_provider
from ngram.ngram_ar.setup import ArSetupResult, run_ar_setup
from ngram.utils.dotenv_merge import merge_dotenv_keys


LAB_MANIFEST_VERSION = 2
LAB_PROVIDERS = (
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
PROVIDER_DEFAULTS = {
    "openai": {
        "base_url": "",
        "key_env": "OPENAI_API_KEY",
        "model": "gpt-5.6-sol",
        "embedding_model": "text-embedding-3-small",
    },
    "venice": {
        "base_url": "",
        "key_env": "VENICE_API_KEY",
        "model": "qwen3-235b-a22b-instruct-2507",
        "embedding_model": "text-embedding-bge-m3",
    },
    "anthropic": {
        "base_url": "",
        "key_env": "ANTHROPIC_API_KEY",
        "model": "",
        "embedding_model": "",
    },
    "gemini": {"base_url": "", "key_env": "GEMINI_API_KEY", "model": "", "embedding_model": ""},
    "openrouter": {
        "base_url": "",
        "key_env": "OPENROUTER_API_KEY",
        "model": "",
        "embedding_model": "",
    },
    "xai": {"base_url": "", "key_env": "XAI_API_KEY", "model": "", "embedding_model": ""},
    "groq": {"base_url": "", "key_env": "GROQ_API_KEY", "model": "", "embedding_model": ""},
    "together": {"base_url": "", "key_env": "TOGETHER_API_KEY", "model": "", "embedding_model": ""},
    "fireworks": {
        "base_url": "",
        "key_env": "FIREWORKS_API_KEY",
        "model": "",
        "embedding_model": "",
    },
    "mistral": {"base_url": "", "key_env": "MISTRAL_API_KEY", "model": "", "embedding_model": ""},
    "deepseek": {"base_url": "", "key_env": "DEEPSEEK_API_KEY", "model": "", "embedding_model": ""},
    "custom": {
        "base_url": "",
        "key_env": "NGRAM_LAB_API_KEY",
        "model": "",
        "embedding_model": "",
    },
}


@dataclass(frozen=True)
class LabManifest:
    version: int
    entity: str
    shell_path: str
    provider: str
    model: str
    embedding_model: str
    bridge_port: int
    surface_port: int


def repo_root() -> Path:
    return project_configs_dir().parent


def manifest_path(root: Path | None = None) -> Path:
    return (root or repo_root()) / ".runtime" / "lab.json"


def validate_source_checkout(root: Path) -> None:
    if not (root / "ngramAR" / "package-lock.json").is_file():
        raise click.ClickException(
            "The Quest lab command requires a source checkout containing ngramAR/."
        )


def read_dotenv_value(path: Path, key: str) -> str:
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


def validate_secret(value: str) -> str:
    secret = (value or "").strip()
    if not secret:
        raise click.ClickException("The hosted provider requires an API key.")
    if any(char in secret for char in "\r\n\0"):
        raise click.ClickException("The API key contains an invalid control character.")
    return secret


def validate_api_base_url(value: str, *, required: bool) -> str:
    raw = (value or "").strip().rstrip("/")
    if not raw:
        if required:
            raise click.ClickException("A custom OpenAI-compatible API base URL is required.")
        return ""
    try:
        parsed = urlsplit(raw)
    except ValueError as exc:
        raise click.ClickException("The hosted API base URL is invalid.") from exc
    loopback = (parsed.hostname or "").lower() in {"localhost", "127.0.0.1", "::1"}
    if parsed.scheme != "https" and not (parsed.scheme == "http" and loopback):
        raise click.ClickException(
            "Hosted API endpoints must use HTTPS; HTTP is allowed only on loopback."
        )
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise click.ClickException(
            "The API base URL cannot contain credentials, a query, or a fragment."
        )
    if not parsed.hostname:
        raise click.ClickException("The hosted API base URL must include a hostname.")
    return raw


def _clean_setting(value: str, label: str) -> str:
    cleaned = (value or "").strip()
    if not cleaned:
        raise click.ClickException(f"{label} is required.")
    if any(char in cleaned for char in "\r\n\0"):
        raise click.ClickException(f"{label} contains an invalid control character.")
    return cleaned


def load_lab_manifest(root: Path | None = None) -> LabManifest | None:
    path = manifest_path(root)
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return LabManifest(**raw)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _write_manifest(manifest: LabManifest, root: Path | None = None) -> None:
    path = manifest_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(manifest), indent=2) + "\n", encoding="utf-8")


def _relative_shell_path(shell_dir: Path, root: Path) -> str:
    """Return a portable shell path without recording a workstation path."""
    try:
        return shell_dir.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise click.ClickException("The lab shell must be inside this ngram checkout.") from exc


def _resolve_shell_path(shell_path: str, root: Path) -> Path:
    """Resolve and confine a manifest shell path to the current checkout."""
    checkout = root.resolve()
    candidate = (checkout / shell_path).resolve()
    if candidate != checkout and checkout not in candidate.parents:
        raise click.ClickException("The lab profile contains an unsafe shell path. Run lab setup again.")
    return candidate


async def _probe_provider(
    *, provider_name: str, model: str, embedding_model: str, base_url: str, api_key: str
) -> tuple[bool, int]:
    harness = load_harness_config()
    harness.inference.provider = provider_name
    harness.inference.base_url = base_url
    harness.inference.pass_num_ctx = False
    provider = build_inference_provider(harness, bearer_token=api_key)
    try:
        catalog = await asyncio.wait_for(provider.list_models(), timeout=30.0)
        vector = await asyncio.wait_for(
            provider.embed(embedding_model, "ngram lab memory readiness probe"), timeout=30.0
        )
        if not vector:
            raise click.ClickException(
                f"The provider returned no embedding for {embedding_model!r}."
            )
        expected = int(harness.memory.embedding_dimensions)
        if len(vector) != expected:
            raise click.ClickException(
                f"The embedding model returned {len(vector)} dimensions; ngram requires {expected}."
            )
        return model in set(catalog), len(vector)
    except click.ClickException:
        raise
    except Exception as exc:
        status = getattr(exc, "status", None)
        if status in (401, 403):
            raise click.ClickException("The hosted provider rejected the API key.") from None
        raise click.ClickException(
            f"Hosted inference readiness check failed: {str(exc)[:240]}"
        ) from None
    finally:
        await provider.close()


def setup_lab(
    entity_name: str,
    *,
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
    install_node: bool,
    check_provider: bool,
    assume_yes: bool,
) -> LabManifest:
    """Configure one persistent Entity, one shell, and one hosted model substrate."""
    root = repo_root()
    validate_source_checkout(root)
    env_path = root / ".env"
    stored_provider = read_dotenv_value(env_path, "NGRAM_INFERENCE_PROVIDER").lower()
    provider_name = provider or (stored_provider if stored_provider in LAB_PROVIDERS else "openai")
    provider_name = provider_name.strip().lower()
    if provider_name not in LAB_PROVIDERS:
        raise click.ClickException(
            "Lab inference must be a hosted provider or custom OpenAI-compatible API."
        )
    defaults = PROVIDER_DEFAULTS[provider_name]
    reuse_stored = stored_provider == provider_name
    selected_model = (
        model
        or (read_dotenv_value(env_path, "NGRAM_INFERENCE_MODEL") if reuse_stored else "")
        or defaults["model"]
        or ""
    ).strip()
    if not selected_model and not assume_yes:
        selected_model = click.prompt("Hosted chat model ID")
    chat_model = _clean_setting(selected_model, "Chat model ID")
    selected_embedding = (
        embedding_model
        or (read_dotenv_value(env_path, "NGRAM_EMBEDDING_MODEL") if reuse_stored else "")
        or defaults["embedding_model"]
        or ""
    ).strip()
    if not selected_embedding and not assume_yes:
        selected_embedding = click.prompt("Hosted embedding model ID")
    embed_model = _clean_setting(selected_embedding, "Embedding model ID")
    stored_base = read_dotenv_value(env_path, "NGRAM_INFERENCE_BASE_URL") if reuse_stored else ""
    resolved_base = validate_api_base_url(
        base_url or stored_base or defaults["base_url"], required=provider_name == "custom"
    )
    stored_key_env = (
        read_dotenv_value(env_path, "NGRAM_INFERENCE_API_KEY_ENV") if reuse_stored else ""
    )
    key_env = _clean_setting(
        api_key_env or stored_key_env or defaults["key_env"], "API key environment name"
    )
    if not key_env.replace("_", "A").isalnum() or not key_env[0].isalpha():
        raise click.ClickException("The API key environment name is invalid.")

    api_key = os.environ.get(key_env, "").strip() or read_dotenv_value(env_path, key_env)
    if not api_key:
        if assume_yes:
            raise click.ClickException(
                f"Set {key_env} in the environment or .env before using --yes."
            )
        api_key = click.prompt(
            f"{provider_name} API key", hide_input=True, confirmation_prompt=False
        )
    api_key = validate_secret(api_key)
    surface_token = read_dotenv_value(env_path, "NGRAM_AR_SURFACE_TOKEN")
    if len(surface_token) < 32:
        surface_token = secrets.token_urlsafe(32)

    # Finish every non-mutating gate before touching the operator's profile.
    load_entity_config(entity_name, load_harness_config())
    if check_provider:
        click.echo("Checking hosted chat and memory embeddings…")
        model_found, dimensions = asyncio.run(
            _probe_provider(
                provider_name=provider_name,
                model=chat_model,
                embedding_model=embed_model,
                base_url=resolved_base,
                api_key=api_key,
            )
        )
        if not model_found:
            click.echo(
                "Warning: the provider responded, but the chat model was not in its catalog.",
                err=True,
            )
        click.echo(f"[ok] Hosted memory returned {dimensions} dimensions.")

    if install_node:
        npm = npm_executable()
        click.echo("\nInstalling the pinned WebXR workspace dependencies…")
        subprocess.run([npm, "ci", "--prefix", str(root / "ngramAR")], cwd=root, check=True)
    click.echo("Building the WebXR surface…")
    subprocess.run(
        [npm_executable(), "--prefix", str(root / "ngramAR"), "run", "build"],
        cwd=root,
        check=True,
    )

    ar_result: ArSetupResult = run_ar_setup(
        entity_name=entity_name,
        shell_path=shell_path,
        target="local",
        person_id=person_id,
        person_name=person_name,
        port=bridge_port,
        assume_yes=True,
    )

    updates = {
        "NGRAM_DEPLOYMENT_MODE": "local",
        "NGRAM_INFERENCE_PROVIDER": provider_name,
        "NGRAM_INFERENCE_BASE_URL": resolved_base,
        "NGRAM_INFERENCE_API_KEY_ENV": key_env,
        "NGRAM_INFERENCE_MODEL": chat_model,
        "NGRAM_INFERENCE_PASS_NUM_CTX": "false",
        "NGRAM_EMBEDDING_MODEL": embed_model,
        "NGRAM_EMBEDDING_DIMENSIONS": "768",
        "NGRAM_AR_HOST": "127.0.0.1",
        "NGRAM_AR_PORT": str(surface_port),
        "NGRAM_AR_SURFACE_TOKEN": surface_token,
        key_env: api_key,
    }
    merge_dotenv_keys(env_path, updates)
    # The CLI loaded .env before entering this command; keep the current
    # process consistent for validation without ever putting secrets in argv.
    os.environ.update(updates)
    for secret_path in (env_path, ar_result.shell_dir / ".env"):
        try:
            secret_path.chmod(0o600)
        except OSError:
            pass

    manifest = LabManifest(
        version=LAB_MANIFEST_VERSION,
        entity=ar_result.entity_name,
        shell_path=_relative_shell_path(ar_result.shell_dir, root),
        provider=provider_name,
        model=chat_model,
        embedding_model=embed_model,
        bridge_port=bridge_port,
        surface_port=surface_port,
    )
    _write_manifest(manifest, root)
    click.echo("[ok] Lab configured. No tunnel was created or started.")
    return manifest


def npm_executable() -> str:
    candidates = ("npm.cmd", "npm") if os.name == "nt" else ("npm",)
    for candidate in candidates:
        found = shutil.which(candidate)
        if found:
            return found
    raise click.ClickException("Node.js/npm is required (Node 22 or newer).")


def _port_available(host: str, port: int) -> bool:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    try:
        with socket.socket(family, socket.SOCK_STREAM) as sock:
            sock.bind((host, port))
        return True
    except OSError:
        return False


def doctor_lab(entity_name: str, *, require_free_ports: bool = True) -> LabManifest:
    root = repo_root()
    validate_source_checkout(root)
    manifest = load_lab_manifest(root)
    if manifest is None or manifest.entity.casefold() != entity_name.strip().casefold():
        raise click.ClickException(
            f"No valid lab profile exists for {entity_name!r}. Run lab setup."
        )
    if manifest.version != LAB_MANIFEST_VERSION:
        raise click.ClickException("The lab profile version is not supported. Run lab setup again.")
    load_entity_config(manifest.entity, load_harness_config())
    shell_dir = _resolve_shell_path(manifest.shell_path, root)
    if not (shell_dir / "shell.yaml").is_file():
        raise click.ClickException(f"The configured shell is missing: {shell_dir}")
    env_path = root / ".env"
    if read_dotenv_value(env_path, "NGRAM_INFERENCE_PROVIDER") != manifest.provider:
        raise click.ClickException("The hosted provider no longer matches the lab profile.")
    if read_dotenv_value(env_path, "NGRAM_INFERENCE_MODEL") != manifest.model:
        raise click.ClickException("The hosted chat model no longer matches the lab profile.")
    if read_dotenv_value(env_path, "NGRAM_EMBEDDING_MODEL") != manifest.embedding_model:
        raise click.ClickException("The hosted embedding model no longer matches the lab profile.")
    configured_dimensions = read_dotenv_value(env_path, "NGRAM_EMBEDDING_DIMENSIONS")
    if configured_dimensions and configured_dimensions != "768":
        raise click.ClickException("The hosted lab must keep memory embeddings at 768 dimensions.")
    if read_dotenv_value(env_path, "NGRAM_INFERENCE_PASS_NUM_CTX").lower() != "false":
        raise click.ClickException("The hosted lab must disable Ollama-specific chat options.")
    validate_api_base_url(
        read_dotenv_value(env_path, "NGRAM_INFERENCE_BASE_URL"),
        required=manifest.provider == "custom",
    )
    key_env = read_dotenv_value(env_path, "NGRAM_INFERENCE_API_KEY_ENV")
    if not key_env or not (
        os.environ.get(key_env, "").strip() or read_dotenv_value(env_path, key_env)
    ):
        raise click.ClickException("The hosted inference credential is missing.")
    token = read_dotenv_value(env_path, "NGRAM_AR_ENTITY_BRIDGE_TOKEN")
    shell_token = read_dotenv_value(shell_dir / ".env", "NGRAM_AR_ENTITY_BRIDGE_TOKEN")
    if len(token) < 32 or token != shell_token:
        raise click.ClickException(
            "The Entity bridge token is missing or does not match the shell."
        )
    if read_dotenv_value(env_path, "NGRAM_AR_ENTITY_BRIDGE_HOST") not in {
        "127.0.0.1",
        "localhost",
        "::1",
    }:
        raise click.ClickException("The Entity bridge must remain bound to loopback.")
    if read_dotenv_value(env_path, "NGRAM_AR_ENTITY_BRIDGE_PORT") != str(manifest.bridge_port):
        raise click.ClickException("The Entity bridge port no longer matches the lab profile.")
    if len(read_dotenv_value(env_path, "NGRAM_AR_SURFACE_TOKEN")) < 32:
        raise click.ClickException("The WebXR surface access token is missing or too short.")
    shell_bridge = read_dotenv_value(shell_dir / ".env", "NGRAM_AR_ENTITY_BRIDGE_URL")
    if shell_bridge.rstrip("/") != f"ws://127.0.0.1:{manifest.bridge_port}":
        raise click.ClickException("The shell is not routed to the loopback Entity bridge.")
    if not (root / "ngramAR" / "packages" / "cli" / "dist" / "index.js").is_file():
        raise click.ClickException("The WebXR build is missing. Run lab setup.")
    npm_executable()
    try:
        version = (
            subprocess.run(
                [shutil.which("node") or "node", "--version"],
                capture_output=True,
                text=True,
                check=True,
            )
            .stdout.strip()
            .lstrip("v")
        )
        if int(version.split(".", 1)[0]) < 22:
            raise click.ClickException("ngram AR requires Node.js 22 or newer.")
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise click.ClickException("Could not verify Node.js 22 or newer.") from exc
    if require_free_ports:
        for host, port, label in (
            ("127.0.0.1", manifest.bridge_port, "Entity bridge"),
            ("127.0.0.1", manifest.surface_port, "WebXR surface"),
        ):
            if not _port_available(host, port):
                raise click.ClickException(f"{label} port {port} is already in use.")
    return manifest


def _wait_for_port(process: subprocess.Popen, port: int, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        code = process.poll()
        if code is not None:
            raise click.ClickException(f"The Entity exited during startup (code {code}).")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.25):
                return
        except OSError:
            time.sleep(0.2)
    raise click.ClickException(f"The Entity bridge did not open port {port} within {timeout:.0f}s.")


def _terminate_tree(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    try:
        parent = psutil.Process(process.pid)
        descendants = parent.children(recursive=True)
        for child in reversed(descendants):
            child.terminate()
        parent.terminate()
        _, alive = psutil.wait_procs([*descendants, parent], timeout=5.0)
        for item in alive:
            item.kill()
        psutil.wait_procs(alive, timeout=3.0)
    except (psutil.Error, OSError):
        try:
            process.terminate()
            process.wait(timeout=3.0)
        except (OSError, subprocess.TimeoutExpired):
            try:
                process.kill()
            except OSError:
                pass


def run_lab(entity_name: str, *, expose_lan: bool, assume_yes: bool) -> None:
    manifest = doctor_lab(entity_name)
    if expose_lan and not assume_yes:
        click.confirm(
            "Expose the WebXR surface to this LAN? Continue only on a trusted private network.",
            default=False,
            abort=True,
        )
    host = "0.0.0.0" if expose_lan else "127.0.0.1"
    if expose_lan and not _port_available(host, manifest.surface_port):
        raise click.ClickException(f"WebXR surface port {manifest.surface_port} is already in use.")
    root = repo_root()
    child_env = dict(os.environ)
    env_path = root / ".env"
    key_env = read_dotenv_value(env_path, "NGRAM_INFERENCE_API_KEY_ENV")
    for key in (
        "NGRAM_DEPLOYMENT_MODE",
        "NGRAM_INFERENCE_PROVIDER",
        "NGRAM_INFERENCE_BASE_URL",
        "NGRAM_INFERENCE_API_KEY_ENV",
        "NGRAM_INFERENCE_MODEL",
        "NGRAM_INFERENCE_PASS_NUM_CTX",
        "NGRAM_EMBEDDING_MODEL",
        "NGRAM_AR_ENTITY_BRIDGE_HOST",
        "NGRAM_AR_ENTITY_BRIDGE_PORT",
        "NGRAM_AR_ENTITY_BRIDGE_TOKEN",
        "NGRAM_AR_PERSON_ID",
        "NGRAM_AR_PERSON_NAME",
        "NGRAM_AR_SURFACE_TOKEN",
        key_env,
    ):
        if key:
            child_env[key] = read_dotenv_value(env_path, key)
    child_env["NGRAM_AR_HOST"] = host
    child_env["NGRAM_AR_PORT"] = str(manifest.surface_port)
    python_cmd = [sys.executable, "-m", "ngram.main", "run", manifest.entity]
    surface_cmd = [
        npm_executable(),
        "--prefix",
        str(root / "ngramAR"),
        "run",
        "ngram-ar",
        "--",
        "dev",
        str(_resolve_shell_path(manifest.shell_path, root)),
        "--host",
        host,
        "--port",
        str(manifest.surface_port),
    ]
    click.echo("Starting the persistent Entity brain…")
    entity_process = subprocess.Popen(python_cmd, cwd=root, env=child_env)
    surface_process: subprocess.Popen | None = None
    try:
        _wait_for_port(entity_process, manifest.bridge_port)
        click.echo("Starting the WebXR body…")
        surface_process = subprocess.Popen(surface_cmd, cwd=root, env=child_env)
        while True:
            entity_code = entity_process.poll()
            surface_code = surface_process.poll()
            if entity_code is not None or surface_code is not None:
                failed = entity_code if entity_code is not None else surface_code
                if failed:
                    raise click.ClickException(
                        f"A lab process exited unexpectedly (code {failed})."
                    )
                break
            time.sleep(0.25)
    except KeyboardInterrupt:
        click.echo("\nStopping the lab…")
    finally:
        if surface_process is not None:
            _terminate_tree(surface_process)
        _terminate_tree(entity_process)


def platform_summary() -> str:
    return f"{platform.system()} {platform.release()} / Python {platform.python_version()}"
