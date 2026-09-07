"""Wall-clock tool for when the model wants to double-check the time."""

from __future__ import annotations

from ngram.presence.tools.registry import tool
from ngram.utils.clock import format_wall_clock_tool_line


@tool(
    name="get_current_time",
    description=(
        "Get the current date and time in the display timezone (NGRAM_TIMEZONE / TZ, "
        "or US Eastern on Railway / hybrid_railway). Use when you need the exact wall time."
    ),
)
async def get_current_time() -> str:
    return format_wall_clock_tool_line()
