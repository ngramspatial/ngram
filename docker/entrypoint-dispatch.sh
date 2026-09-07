#!/usr/bin/env sh
# Single image entrypoint for Railway services. The service role is selected
# entirely by environment, so new Railway services do not depend on legacy
# railway.json start-command support or dashboard-specific shell quoting.

set -eu

case "${NGRAM_RAILWAY_ROLE:-}" in
  api)
    exec /app/docker/entrypoint-railway.sh \
      -m ngram.main api --host 0.0.0.0 --port "${PORT:-8080}"
    ;;
  hands)
    exec /app/docker/entrypoint-railway.sh \
      -m ngram.main hands --host 0.0.0.0 --port "${PORT:-8080}"
    ;;
  worker|"")
    if [ -n "${NGRAM_ENTITY:-}" ]; then
      exec /app/docker/entrypoint-railway.sh -m ngram.main worker "$NGRAM_ENTITY"
    fi
    echo "Railway: set NGRAM_RAILWAY_ROLE=api or hands, or set NGRAM_ENTITY for the worker." >&2
    exit 1
    ;;
  *)
    echo "Railway: unsupported NGRAM_RAILWAY_ROLE='$NGRAM_RAILWAY_ROLE'." >&2
    exit 1
    ;;
esac
