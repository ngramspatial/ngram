import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from ngram.config import EntityCognition
from ngram.presence.platforms.telegram_platform import TelegramPlatform


def platform_for(entity):
    platform = object.__new__(TelegramPlatform)
    platform._entity = entity
    platform._take_update_if_fresh = AsyncMock(return_value=True)
    platform._check_allowed = AsyncMock(return_value=True)
    platform._reply_html = AsyncMock()
    return platform


def test_compaction_does_not_block_telegram_control_updates():
    platform = object.__new__(TelegramPlatform)
    platform.app = SimpleNamespace(add_handler=Mock())
    platform._register_handlers()
    handlers = [call.args[0] for call in platform.app.add_handler.call_args_list]
    compact = next(handler for handler in handlers if 'compact' in getattr(handler, 'commands', ()))
    assert compact.block is False


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', [False, True])
async def test_compact_summarizes_under_turn_lock_and_never_resets(failure):
    entity = SimpleNamespace(
        _turn_lock=asyncio.Lock(), _history=[{'content': 'keep this context'}],
        _history_rolling_summary='prior summary', clear_conversation_history=Mock(),
    )

    async def compact(**kwargs):
        assert entity._turn_lock.locked()
        if failure:
            raise RuntimeError('provider failure')
        return {'compacted': True, 'messages_before': 30, 'messages_after': 14}

    entity.compact_context_now = AsyncMock(side_effect=compact)
    platform = platform_for(entity)
    await platform._on_compact_command(object(), SimpleNamespace(args=[]))
    entity.compact_context_now.assert_awaited_once()
    entity.clear_conversation_history.assert_not_called()
    assert entity._history[0]['content'] == 'keep this context'
    assert entity._history_rolling_summary == 'prior summary'
    assert not entity._turn_lock.locked()
    assert ('failed' if failure else 'Context compacted') in platform._reply_html.call_args.args[1]


@pytest.mark.asyncio
@pytest.mark.parametrize('output_cap', [0, None])
async def test_context_uses_effective_provider_budget(output_cap):
    cognition = EntityCognition(max_context_tokens=16384, deliberate_max_tokens=output_cap)
    entity = SimpleNamespace(config=SimpleNamespace(
        cognition=cognition, effective_context_tokens=lambda: 256000,
        harness=SimpleNamespace(cognition=SimpleNamespace(deliberate_max_tokens=16000, reflex_max_tokens=1000)),
    ), _history=[], _history_rolling_summary='')
    platform = platform_for(entity)
    await platform._on_context_command(object(), SimpleNamespace(args=[]))
    text = platform._reply_html.call_args.args[1]
    assert '<b>Budget:</b> 256,000 tokens' in text
    assert 'Approximate usage, not provider billing' in text
    assert ('provider limit (no harness cap)' if output_cap is None else '16,000') in text


@pytest.mark.asyncio
@pytest.mark.parametrize('operator', [True, False])
async def test_pause_requires_operator_and_does_not_call_model(operator):
    entity = SimpleNamespace(set_inference_paused=Mock())
    platform = platform_for(entity)
    platform._operators_configured = lambda: True
    platform._is_operator = lambda uid: operator
    await platform._on_pause_command(SimpleNamespace(effective_user=SimpleNamespace(id=42)), SimpleNamespace(args=[]))
    assert entity.set_inference_paused.call_count == int(operator)
