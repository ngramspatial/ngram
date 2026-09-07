FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY . /app

# canary.yaml is gitignored locally; ship Canary from the canonical example for NGRAM_ENTITY=canary.
RUN cp configs/entities/canary.example.yaml configs/entities/canary.yaml \
    && chmod +x /app/docker/entrypoint-railway.sh /app/docker/entrypoint-dispatch.sh

# Bootstrap image Python (used when NGRAM_SKIP_VOLUME_VENV=1). Runtime uses volume venv via entrypoint.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --upgrade pip \
    && pip install ".[railway,api,full]" \
    && playwright install-deps chromium \
    && rm -rf /root/.cache/pip

CMD ["/app/docker/entrypoint-dispatch.sh"]
