"""PDF text extraction using PyMuPDF (fitz)."""

from __future__ import annotations

import json
from pathlib import Path

from ngram.presence.tools.execution_rpc import (
    local_tool_block_message,
    local_body_host_permitted,
)
from ngram.presence.tools.registry import tool
from ngram.presence.tools.runtime import require_tool_runtime


def _parse_pages(pages: str, page_count: int) -> list[int]:
    raw = (pages or "").strip()
    if not raw:
        return list(range(page_count))
    out: set[int] = set()
    for part in raw.split(","):
        p = part.strip()
        if not p:
            continue
        if "-" in p:
            a, b = p.split("-", 1)
            start = max(1, int(a))
            end = min(page_count, int(b))
            for i in range(start, end + 1):
                out.add(i - 1)
        else:
            i = int(p)
            if 1 <= i <= page_count:
                out.add(i - 1)
    return sorted(out)


@tool(
    name="read_pdf",
    description="Read and extract text from a PDF file",
)
async def read_pdf(path: str, pages: str = "") -> str:
    ctx = require_tool_runtime()
    if not local_body_host_permitted(ctx.entity):
        return json.dumps({"error": local_tool_block_message(ctx.entity)})
    p = Path(path).expanduser()
    try:
        p = p.resolve()
    except OSError as e:
        return json.dumps({"error": str(e)})
    if not p.exists():
        return json.dumps({"error": f"file not found: {p}"})
    if p.suffix.lower() != ".pdf":
        return json.dumps({"error": "path is not a PDF"})
    try:
        import fitz  # type: ignore[import-not-found]
    except ImportError:
        return json.dumps({"error": "PyMuPDF not installed. Install with: pip install ngram[pdf]"})

    try:
        doc = fitz.open(str(p))
        try:
            selected = _parse_pages(pages, doc.page_count)
            chunks: list[str] = []
            no_text_pages: list[int] = []
            for idx in selected:
                page = doc.load_page(idx)
                txt = (page.get_text("text") or "").strip()
                if not txt:
                    no_text_pages.append(idx + 1)
                    continue
                chunks.append(f"\n[Page {idx + 1}]\n{txt}")
            text = "\n".join(chunks).strip()
            if len(text) > 48000:
                text = text[:47900] + "\n… [truncated]"
            out = {"path": str(p), "pages": pages or "all", "text": text}
            if no_text_pages:
                out["note"] = f"Pages with little/no extractable text: {no_text_pages[:20]}"
            return json.dumps(out, ensure_ascii=False)
        finally:
            doc.close()
    except Exception as e:
        return json.dumps({"error": str(e), "path": str(p)})
