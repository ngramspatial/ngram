#!/usr/bin/env sh
# Persistent workspace bootstrap for Railway hybrid services.
# Set NGRAM_SKIP_VOLUME_VENV=1 to use the immutable image environment while
# still placing HOME, entity state, and caches on the mounted workspace.

set -eu

WORKSPACE="${NGRAM_EXECUTION_WORKSPACE_DIR:-/app/data}"
mkdir -p "$WORKSPACE"

# Canonical home + caches on the volume so pip, Playwright, and similar tools persist.
export HOME="${NGRAM_VOLUME_HOME:-$WORKSPACE/.home}"
mkdir -p "$HOME"

unset PIP_NO_CACHE_DIR 2>/dev/null || true
export PIP_CACHE_DIR="${NGRAM_PIP_CACHE_DIR:-$WORKSPACE/.cache/pip}"
mkdir -p "$PIP_CACHE_DIR"

export XDG_CACHE_HOME="${NGRAM_XDG_CACHE_HOME:-$WORKSPACE/.cache}"
mkdir -p "$XDG_CACHE_HOME"

export PLAYWRIGHT_BROWSERS_PATH="${NGRAM_PLAYWRIGHT_BROWSERS_PATH:-$WORKSPACE/.cache/ms-playwright}"
mkdir -p "$PLAYWRIGHT_BROWSERS_PATH"

if [ "${NGRAM_SKIP_VOLUME_VENV:-0}" = "1" ]; then
  exec python "$@"
fi

VENV="$WORKSPACE/.venv"
STAMP="$VENV/.pyproject_sha"

PY_SHA="$(python3 -c "import hashlib;print(hashlib.sha256(open('/app/pyproject.toml','rb').read()).hexdigest())")"

if [ ! -x "$VENV/bin/python" ]; then
  python3 -m venv "$VENV"
fi

if [ ! -f "$STAMP" ] || [ "$(cat "$STAMP")" != "$PY_SHA" ]; then
  "$VENV/bin/pip" install --upgrade pip
  "$VENV/bin/pip" install --upgrade "/app[railway,api,full]"
  echo "$PY_SHA" > "$STAMP"
  if "$VENV/bin/python" -c "import playwright" 2>/dev/null; then
    "$VENV/bin/playwright" install chromium 2>/dev/null || true
  fi
fi

exec "$VENV/bin/python" "$@"
