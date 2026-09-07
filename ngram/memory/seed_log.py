"""Audit log for exogenous noise seeds → GEN fragments (see NoiseSeeder)."""

from __future__ import annotations

import json
from typing import Any

async def insert_seed_entry(
    conn: Any,
    *,
    row_id: str,
    entity_name: str,
    tick_timestamp: float,
    source_type: str,
    source_detail: str,
    seed_text: str,
    trace_id: str,
) -> None:
    # Placeholders are `?`; Postgres shim translates to $1..$N (see PgShimConnection).
    sql = (
        "INSERT INTO seed_log (id, entity_name, tick_timestamp, source_type, source_detail, "
        "seed_text, consumed, fragment_produced, fragment_tags, trace_id) VALUES (?, ?, ?, ?, ?, ?, 0, NULL, NULL, ?)"
    )
    await conn.execute(
        sql,
        (
            row_id,
            entity_name,
            float(tick_timestamp),
            source_type,
            source_detail,
            seed_text,
            trace_id,
        ),
    )


async def mark_seed_consumed(
    conn: Any,
    *,
    row_id: str,
    fragment_produced: str,
    fragment_tags: str,
) -> None:
    sql = "UPDATE seed_log SET consumed = 1, fragment_produced = ?, fragment_tags = ? WHERE id = ?"
    await conn.execute(sql, (fragment_produced, fragment_tags, row_id))


async def list_recent_seeds(
    conn: Any,
    *,
    entity_name: str,
    limit: int = 50,
) -> list[dict[str, Any]]:
    sql = (
        "SELECT id, tick_timestamp, source_type, source_detail, seed_text, consumed, "
        "fragment_produced, fragment_tags, trace_id FROM seed_log WHERE entity_name = ? "
        "ORDER BY tick_timestamp DESC LIMIT ?"
    )
    cur = await conn.execute(sql, (entity_name, int(limit)))
    rows = await cur.fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        out.append(
            {
                "id": r[0],
                "tick_timestamp": float(r[1]),
                "source_type": r[2],
                "source_detail": r[3],
                "seed_text": r[4],
                "consumed": bool(r[5]),
                "fragment_produced": r[6],
                "fragment_tags": r[7],
                "trace_id": r[8],
            }
        )
    return out


async def list_trace_rows(
    conn: Any,
    *,
    entity_name: str,
    limit: int = 30,
) -> list[dict[str, Any]]:
    """Seeds that produced fragments (best-effort organism-test audit)."""
    sql = (
        "SELECT id, tick_timestamp, source_type, seed_text, fragment_produced, fragment_tags, trace_id "
        "FROM seed_log WHERE entity_name = ? AND fragment_produced IS NOT NULL AND fragment_produced != '' "
        "ORDER BY tick_timestamp DESC LIMIT ?"
    )
    cur = await conn.execute(sql, (entity_name, int(limit)))
    rows = await cur.fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        tags_raw = r[5]
        tags: list[str] = []
        if tags_raw:
            try:
                tags = list(json.loads(tags_raw))
            except Exception:
                tags = []
        out.append(
            {
                "id": r[0],
                "tick_timestamp": float(r[1]),
                "source_type": r[2],
                "seed_text": r[3],
                "fragment_produced": r[4],
                "fragment_tags": tags,
                "trace_id": r[6],
            }
        )
    return out
