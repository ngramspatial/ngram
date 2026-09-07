"""One-off: reset soma in Postgres + /app/data/soma for hybrid Railway workers.

Run inside the worker container (DATABASE_URL + volume mounted):

    python3 scripts/railway_reset_soma_baseline.py

Or:  type scripts\\railway_reset_soma_baseline.py | railway ssh -s ngram-worker -- sh -c 'cat > /tmp/r.py && python3 /tmp/r.py'
"""
from __future__ import annotations

import asyncio
import json
import os
import pathlib
import time

try:
    import asyncpg
except ImportError as e:  # pragma: no cover
    raise SystemExit("asyncpg required in container") from e

# Must match harness default five bars + initials (configs/default.yaml)
ORDERED = ["social", "curiosity", "creative", "tension", "comfort"]
VALUES: dict[str, float] = {
    "social": 50.0,
    "curiosity": 50.0,
    "creative": 40.0,
    "tension": 15.0,
    "comfort": 65.0,
}


def snapshot() -> dict:
    return {
        "values": dict(VALUES),
        "history": [dict(VALUES)],
        "ordered_names": list(ORDERED),
        "saved_at": time.time(),
        "affects": [],
        "noise_fragments": [],
    }


BODY_MD = """## Bars
social     █████░░░░░  moderate  —
curiosity  █████░░░░░  moderate  —
creative   ████░░░░░░  moderate  —
tension    ██░░░░░░░░  quiet  ↓
comfort    ██████░░░░  strong  —

## Affects
(flat — body not naming a texture yet)

## Noise
(quiet)

## Conflicts
(no structural strain — no paired drives are colliding yet)

## Impulses
(no pull signal — thresholds quiet, nothing crowding the edge)
"""


async def main() -> None:
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL")
    if not dsn:
        raise SystemExit("DATABASE_URL not set")

    raw = json.dumps(snapshot(), separators=(",", ":"))

    soma_dir = pathlib.Path("/app/data/soma")
    soma_dir.mkdir(parents=True, exist_ok=True)
    (soma_dir / "soma-state.json").write_text(raw, encoding="utf-8")
    (soma_dir / "body.md").write_text(BODY_MD, encoding="utf-8")

    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO entity_state (key, value) VALUES ($1, $2) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
            "soma_state_v2",
            raw,
        )
        await conn.execute("DELETE FROM entity_state WHERE key = $1", "soma_bar_state_v1")
    finally:
        await conn.close()

    print("soma baseline written: Postgres entity_state.soma_state_v2 + /app/data/soma/{soma-state.json,body.md}")
    print("Restart ngram-worker to reload in-memory tonic.")


if __name__ == "__main__":
    asyncio.run(main())
