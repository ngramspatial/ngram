"""Run: ``python -m ngram.inference_gateway`` (requires ``pip install 'ngram[gateway]'``).

In hybrid setups, point Cloudflare Tunnel (or equivalent) **only** at this listener — not at a
shared reverse proxy — tunnel must terminate only at this gateway.
"""

from __future__ import annotations

import os

import uvicorn

from ngram.inference_gateway.app import create_app

if __name__ == "__main__":
    host = (os.environ.get("INFERENCE_GATEWAY_HOST") or "127.0.0.1").strip()
    port = int((os.environ.get("INFERENCE_GATEWAY_PORT") or "8010").strip())
    uvicorn.run(create_app(), host=host, port=port, log_level="info")
