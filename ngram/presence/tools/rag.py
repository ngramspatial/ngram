"""Local document RAG (Retrieval-Augmented Generation) indexing and search."""

from __future__ import annotations

import json
import math
import sqlite3
from pathlib import Path
from typing import Any

from ngram.presence.tools.registry import tool
from ngram.presence.tools.runtime import require_tool_runtime
from ngram.utils.embeddings import embed_text

def _get_rag_db_path(entity: Any) -> Path:
    # Use the same directory as the journal/memory
    try:
        journal_path = Path(entity.config.journal_path()).expanduser()
        db_path = journal_path.parent / "rag.db"
    except Exception:
        # Fallback if config is weird
        db_path = Path("~/.ngram/default_rag.db").expanduser()

    db_path.parent.mkdir(parents=True, exist_ok=True)
    return db_path

def _init_rag_db(db_path: Path) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS rag_chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                collection_name TEXT NOT NULL,
                source_label TEXT NOT NULL,
                text_chunk TEXT NOT NULL,
                vector_json TEXT NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_rag_collection ON rag_chunks(collection_name)")
        conn.commit()

def _cosine_similarity(v1: list[float], v2: list[float]) -> float:
    if not v1 or not v2 or len(v1) != len(v2):
        return 0.0
    dot = sum(a * b for a, b in zip(v1, v2))
    mag1 = math.sqrt(sum(a * a for a in v1))
    mag2 = math.sqrt(sum(b * b for b in v2))
    if mag1 == 0 or mag2 == 0:
        return 0.0
    return dot / (mag1 * mag2)

def _chunk_text(text: str, chunk_size: int = 800, overlap: int = 100) -> list[str]:
    # Very simple character-based chunking, preferring to split on double newlines or spaces
    chunks = []
    text = text.strip()

    start = 0
    while start < len(text):
        end = start + chunk_size
        if end >= len(text):
            chunks.append(text[start:])
            break

        # Try to find a good break point (double newline, then single newline, then space)
        break_point = end
        for separator in ["\n\n", "\n", ". ", " "]:
            pos = text.rfind(separator, start + chunk_size // 2, end)
            if pos != -1:
                break_point = pos + len(separator)
                break

        chunks.append(text[start:break_point].strip())
        start = break_point - overlap

    return [c for c in chunks if c]

@tool(
    name="index_text",
    description="Index a large block of text into a RAG collection for later semantic searching. Useful for saving the contents of PDFs, long web pages, or codebases so you can search them later without bloating your context window.",
)
async def index_text(text: str, collection_name: str, source_label: str) -> str:
    ctx = require_tool_runtime()
    db_path = _get_rag_db_path(ctx.entity)
    _init_rag_db(db_path)

    t = (text or "").strip()
    col = (collection_name or "").strip().lower()
    lbl = (source_label or "").strip()

    if not t or not col or not lbl:
        return json.dumps({"error": "text, collection_name, and source_label are required"})

    embed_model = ctx.entity.config.harness.models.embedding
    if not embed_model:
        return json.dumps({"error": "No embedding model configured in configs/default.yaml"})

    chunks = _chunk_text(t)
    inserted = 0

    try:
        with sqlite3.connect(db_path) as conn:
            for chunk in chunks:
                vector = await embed_text(ctx.entity.client, embed_model, chunk)
                conn.execute(
                    "INSERT INTO rag_chunks (collection_name, source_label, text_chunk, vector_json) VALUES (?, ?, ?, ?)",
                    (col, lbl, chunk, json.dumps(vector))
                )
                inserted += 1
            conn.commit()

        return json.dumps({
            "success": True,
            "collection": col,
            "source": lbl,
            "chunks_indexed": inserted
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)})

@tool(
    name="search_collection",
    description="Semantically search a specific RAG collection for information. Use this to retrieve facts from documents you previously indexed.",
)
async def search_collection(collection_name: str, query: str, limit: int = 5) -> str:
    ctx = require_tool_runtime()
    db_path = _get_rag_db_path(ctx.entity)
    _init_rag_db(db_path)

    col = (collection_name or "").strip().lower()
    q = (query or "").strip()
    lim = max(1, min(20, int(limit or 5)))

    if not col or not q:
        return json.dumps({"error": "collection_name and query are required"})

    embed_model = ctx.entity.config.harness.models.embedding

    try:
        query_vector = await embed_text(ctx.entity.client, embed_model, q)

        results = []
        with sqlite3.connect(db_path) as conn:
            cur = conn.execute(
                "SELECT source_label, text_chunk, vector_json FROM rag_chunks WHERE collection_name = ?",
                (col,)
            )
            for row in cur.fetchall():
                lbl, chunk, vec_json = row
                try:
                    vec = json.loads(vec_json)
                    score = _cosine_similarity(query_vector, vec)
                    results.append((score, lbl, chunk))
                except Exception:
                    continue

        # Sort by similarity descending
        results.sort(key=lambda x: x[0], reverse=True)
        top_results = results[:lim]

        out = []
        for score, lbl, chunk in top_results:
            out.append({
                "source": lbl,
                "similarity": round(score, 3),
                "text": chunk
            })

        return json.dumps({
            "collection": col,
            "query": q,
            "results": out
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)})
