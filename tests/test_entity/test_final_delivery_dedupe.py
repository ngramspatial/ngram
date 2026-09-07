from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from ngram.entity import (
    Entity,
    TurnContext,
    _final_reply_repeats_sent_messages,
)
from ngram.models import Input
from ngram.utils.ollama_client import ChatCompletionResult


def test_close_multi_message_paraphrase_is_redundant() -> None:
    sent = [
        "I searched our recent history and found mission briefings, spatial lab setups, and Railway proofs.",
        "But there are no power grids or budget reallocations. I was hallucinating context that is not in our logs.",
    ]
    final = (
        "I've searched our recent history, and I'm seeing mission briefings, spatial lab setups, "
        "and Railway proofs. But no power grids. No budget reallocations. I was hallucinating "
        "context that isn't actually in the logs."
    )

    assert _final_reply_repeats_sent_messages(final, sent) is True


def test_recap_with_genuinely_new_finding_is_not_redundant() -> None:
    sent = [
        "I searched the migration logs and found the two incident reports.",
        "Both reports describe the recovery script and rollback process.",
    ]
    final = (
        "I searched the migration logs and found the two incident reports. Both reports describe "
        "the recovery script and rollback process. The production rollback failed because worker "
        "four lost its database lease."
    )

    assert _final_reply_repeats_sent_messages(final, sent) is False


def _turn(platform: str, final: str) -> TurnContext:
    sent = [
        "I searched recent history and found mission briefings and Railway proofs.",
        "There are no power-grid reports. I invented that context.",
    ]
    return TurnContext(
        inp=Input(
            text="when did we discuss that?",
            person_id="u",
            person_name="U",
            platform=platform,
        ),
        route="deliberate",
        reply_text=final,
        final_res=ChatCompletionResult(content=final, finish_reason="stop"),
        intermediate_sent=True,
        last_intermediate=sent[-1],
        tool_state={
            "_messages_sent": len(sent),
            "_sent_messages": sent,
            "tool_output_previews": [
                {"tool": "search_memory", "ok": True, "preview": "no matching history"},
            ],
        },
    )


def _bare_entity() -> Entity:
    entity = object.__new__(Entity)
    entity.voice_ctl = SimpleNamespace(sanitize_reply=lambda text: text)
    entity._maybe_extend_truncated_reply = AsyncMock(
        side_effect=lambda _route, _res, text, _sys, _msg: text,
    )
    return entity


@pytest.mark.parametrize("platform", ["telegram", "discord"])
@pytest.mark.asyncio
async def test_post_tool_paraphrase_is_not_delivered_twice(platform: str) -> None:
    final = (
        "I searched the recent history and found mission briefings and Railway proofs. "
        "There are no power-grid reports; I invented that context."
    )
    tc = _turn(platform, final)

    await _bare_entity()._finalize_reply(tc)

    assert tc.skip_final_delivery is True


@pytest.mark.asyncio
async def test_post_tool_new_finding_is_still_delivered() -> None:
    final = (
        "I searched the recent history and found mission briefings and Railway proofs. "
        "There are no power-grid reports. The archived audit instead identifies a failed "
        "authentication rollout in worker four."
    )
    tc = _turn("telegram", final)

    await _bare_entity()._finalize_reply(tc)

    assert tc.skip_final_delivery is False


@pytest.mark.asyncio
async def test_other_platforms_keep_their_final_delivery_behavior() -> None:
    final = (
        "I searched the recent history and found mission briefings and Railway proofs. "
        "There are no power-grid reports; I invented that context."
    )
    tc = _turn("ngram_ar", final)

    await _bare_entity()._finalize_reply(tc)

    assert tc.skip_final_delivery is False
