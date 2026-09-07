"""SQLite → PostgreSQL statement adaptation for the asyncpg shim."""

from __future__ import annotations


def qmarks_to_numbered(sql: str) -> str:
    parts = sql.split("?")
    if len(parts) == 1:
        return sql
    out: list[str] = []
    for i, p in enumerate(parts[:-1]):
        out.append(p)
        out.append(f"${i + 1}")
    out.append(parts[-1])
    return "".join(out)


def translate_sql(sql: str) -> str:
    s = sql.strip()
    if s.startswith("INSERT OR REPLACE INTO episodes"):
        return (
            "INSERT INTO episodes (id, timestamp, summary, participants, emotional_imprint, "
            "emotional_intensity, significance, tags, raw_context, self_reflection, embedding) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11) "
            "ON CONFLICT (id) DO UPDATE SET timestamp = EXCLUDED.timestamp, "
            "summary = EXCLUDED.summary, participants = EXCLUDED.participants, "
            "emotional_imprint = EXCLUDED.emotional_imprint, emotional_intensity = EXCLUDED.emotional_intensity, "
            "significance = EXCLUDED.significance, tags = EXCLUDED.tags, raw_context = EXCLUDED.raw_context, "
            "self_reflection = EXCLUDED.self_reflection, embedding = EXCLUDED.embedding"
        )
    if s.startswith("INSERT OR REPLACE INTO relationships"):
        return (
            "INSERT INTO relationships (person_id, name, first_met, last_interaction, interaction_count, "
            "familiarity, warmth, trust, dynamic, notes, topics_shared, unresolved) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12) "
            "ON CONFLICT (person_id) DO UPDATE SET name = EXCLUDED.name, first_met = EXCLUDED.first_met, "
            "last_interaction = EXCLUDED.last_interaction, interaction_count = EXCLUDED.interaction_count, "
            "familiarity = EXCLUDED.familiarity, warmth = EXCLUDED.warmth, trust = EXCLUDED.trust, "
            "dynamic = EXCLUDED.dynamic, notes = EXCLUDED.notes, topics_shared = EXCLUDED.topics_shared, "
            "unresolved = EXCLUDED.unresolved"
        )
    if s.startswith("INSERT OR REPLACE INTO relational_documents"):
        return (
            "INSERT INTO relational_documents (person_id, person_name, document, derived_scores, "
            "last_interaction, last_reflection, interaction_count, significant_moments, meta, created_at, updated_at) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11) "
            "ON CONFLICT (person_id) DO UPDATE SET person_name = EXCLUDED.person_name, "
            "document = EXCLUDED.document, derived_scores = EXCLUDED.derived_scores, "
            "last_interaction = EXCLUDED.last_interaction, last_reflection = EXCLUDED.last_reflection, "
            "interaction_count = EXCLUDED.interaction_count, significant_moments = EXCLUDED.significant_moments, "
            "meta = EXCLUDED.meta, updated_at = EXCLUDED.updated_at"
        )
    if s.startswith("INSERT OR REPLACE INTO entity_state"):
        return (
            "INSERT INTO entity_state (key, value) VALUES ($1, $2) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
        )
    if "PRAGMA" in s.upper():
        raise RuntimeError("PRAGMA is SQLite-only")
    return qmarks_to_numbered(sql)
