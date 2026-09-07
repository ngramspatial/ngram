from __future__ import annotations

import asyncio
from types import MethodType

import pytest

import ngram.entity as entity_module
from ngram.entity import Entity
from ngram.inference.control import InferenceControl
from ngram.models import Input


@pytest.mark.asyncio
async def test_entity_serializes_turns_from_multiple_surfaces(tmp_path) -> None:
    entity = object.__new__(Entity)
    entity._turn_lock = asyncio.Lock()
    entity.inference_control = InferenceControl(tmp_path / "paused")
    active = 0
    max_active = 0

    async def fake_perceive_once(self: Entity, inp: Input, **_kwargs) -> tuple[str, bool]:
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.01)
        active -= 1
        return inp.platform, False

    entity._perceive_once = MethodType(fake_perceive_once, entity)
    telegram = Input(
        text="one",
        person_id="person",
        person_name="You",
        channel="telegram-chat",
        platform="telegram",
    )
    ar = Input(
        text="two",
        person_id="person",
        person_name="You",
        channel="ar-session",
        platform="ngram_ar",
    )

    replies = await asyncio.gather(entity.perceive(telegram), entity.perceive(ar))

    assert max_active == 1
    assert {reply[0] for reply in replies} == {"telegram", "ngram_ar"}


@pytest.mark.asyncio
async def test_entity_emits_turn_activity_start_and_finish_even_on_failure(tmp_path) -> None:
    entity = object.__new__(Entity)
    entity._turn_lock = asyncio.Lock()
    entity.inference_control = InferenceControl(tmp_path / "paused")
    entity._turn_activity_sinks = []
    events: list[dict[str, object]] = []

    async def sink(event: dict[str, object]) -> None:
        events.append(event)

    async def failing_perceive_once(
        self: Entity, inp: Input, **_kwargs
    ) -> tuple[str, bool]:
        raise RuntimeError("turn failed")

    unregister = entity.register_turn_activity_sink(sink)
    entity._perceive_once = MethodType(failing_perceive_once, entity)
    telegram = Input(
        text="hello",
        person_id="person",
        person_name="You",
        channel="telegram-chat",
        platform="telegram",
    )

    with pytest.raises(RuntimeError, match="turn failed"):
        await entity.perceive(telegram)

    assert [event["phase"] for event in events] == ["started", "finished"]
    assert all(event["platform"] == "telegram" for event in events)
    assert all("text" not in event for event in events)

    unregister()
    assert entity._turn_activity_sinks == []


@pytest.mark.asyncio
async def test_entity_can_defer_activity_finish_through_reply_delivery(tmp_path) -> None:
    entity = object.__new__(Entity)
    entity._turn_lock = asyncio.Lock()
    entity.inference_control = InferenceControl(tmp_path / "paused")
    entity._turn_activity_sinks = []
    events: list[dict[str, object]] = []

    async def sink(event: dict[str, object]) -> None:
        events.append(event)

    async def fake_perceive_once(
        self: Entity, _inp: Input, **_kwargs
    ) -> tuple[str, bool]:
        return "reply", True

    entity.register_turn_activity_sink(sink)
    entity._perceive_once = MethodType(fake_perceive_once, entity)
    telegram = Input(
        text="hello",
        person_id="person",
        person_name="You",
        channel="telegram-chat",
        platform="telegram",
    )

    await entity.perceive(telegram, defer_turn_activity_finish=True)
    assert [event["phase"] for event in events] == ["started"]

    await entity.finish_turn_activity(telegram)
    assert [event["phase"] for event in events] == ["started", "finished"]
    assert events[0]["turn_id"] == events[1]["turn_id"]

    # Cleanup is idempotent when cancellation and normal delivery race.
    await entity.finish_turn_activity(telegram)
    assert len(events) == 2


@pytest.mark.asyncio
async def test_entity_labels_non_message_spatial_cognition(tmp_path) -> None:
    entity = object.__new__(Entity)
    entity._turn_lock = asyncio.Lock()
    entity.inference_control = InferenceControl(tmp_path / "paused")
    entity._turn_activity_sinks = []
    events: list[dict[str, object]] = []

    async def sink(event: dict[str, object]) -> None:
        events.append(event)

    async def fake_perceive_once(
        self: Entity, _inp: Input, **_kwargs
    ) -> tuple[str, bool]:
        return "", False

    entity.register_turn_activity_sink(sink)
    entity._perceive_once = MethodType(fake_perceive_once, entity)
    camera_turn = Input(
        text="look",
        person_id="person",
        person_name="You",
        channel="another-spatial-session",
        platform="ngram_ar",
        metadata={"spatial_camera_capture": True},
    )

    await entity.perceive(camera_turn)
    assert [event["turn_kind"] for event in events] == ["camera", "camera"]


@pytest.mark.asyncio
async def test_entity_fans_out_turn_activity_to_sinks_concurrently(tmp_path) -> None:
    entity = object.__new__(Entity)
    entity._turn_lock = asyncio.Lock()
    entity.inference_control = InferenceControl(tmp_path / "paused")
    entity._turn_activity_sinks = []
    first_started = asyncio.Event()
    second_started = asyncio.Event()
    observed: list[tuple[str, str]] = []

    async def first_sink(event: dict[str, object]) -> None:
        phase = str(event["phase"])
        observed.append(("first", phase))
        if phase == "started":
            first_started.set()
            await second_started.wait()

    async def second_sink(event: dict[str, object]) -> None:
        phase = str(event["phase"])
        observed.append(("second", phase))
        if phase == "started":
            second_started.set()
            await first_started.wait()

    async def fake_perceive_once(
        self: Entity, _inp: Input, **_kwargs
    ) -> tuple[str, bool]:
        return "reply", True

    entity.register_turn_activity_sink(first_sink)
    entity.register_turn_activity_sink(second_sink)
    entity._perceive_once = MethodType(fake_perceive_once, entity)
    telegram = Input(
        text="hello",
        person_id="person",
        person_name="You",
        channel="telegram-chat",
        platform="telegram",
    )

    reply = await asyncio.wait_for(entity.perceive(telegram), timeout=0.5)

    assert reply == ("reply", True)
    assert observed.count(("first", "started")) == 1
    assert observed.count(("second", "started")) == 1
    assert observed.count(("first", "finished")) == 1
    assert observed.count(("second", "finished")) == 1


@pytest.mark.asyncio
async def test_stale_activity_sink_is_bounded_evicted_and_cannot_block_turn(
    monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr(entity_module, "_TURN_ACTIVITY_SINK_TIMEOUT_SECONDS", 0.02)
    entity = object.__new__(Entity)
    entity._turn_lock = asyncio.Lock()
    entity.inference_control = InferenceControl(tmp_path / "paused")
    entity._turn_activity_sinks = []
    stale_cancelled = asyncio.Event()
    release_stale_cleanup = asyncio.Event()
    healthy_phases: list[str] = []
    broken_phases: list[str] = []

    async def stale_sink(_event: dict[str, object]) -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            # Simulate a sink whose cancellation cleanup is itself stuck. The
            # Entity must not await this cleanup before running the model turn.
            stale_cancelled.set()
            await release_stale_cleanup.wait()

    async def broken_sink(event: dict[str, object]) -> None:
        broken_phases.append(str(event["phase"]))
        raise RuntimeError("closed surface")

    async def healthy_sink(event: dict[str, object]) -> None:
        healthy_phases.append(str(event["phase"]))

    async def fake_perceive_once(
        self: Entity, _inp: Input, **_kwargs
    ) -> tuple[str, bool]:
        return "reply", True

    entity.register_turn_activity_sink(stale_sink)
    entity.register_turn_activity_sink(broken_sink)
    entity.register_turn_activity_sink(healthy_sink)
    entity._perceive_once = MethodType(fake_perceive_once, entity)
    telegram = Input(
        text="hello",
        person_id="person",
        person_name="You",
        channel="telegram-chat",
        platform="telegram",
    )

    started_at = asyncio.get_running_loop().time()
    reply = await asyncio.wait_for(entity.perceive(telegram), timeout=0.5)
    elapsed = asyncio.get_running_loop().time() - started_at

    await asyncio.wait_for(stale_cancelled.wait(), timeout=0.1)
    assert reply == ("reply", True)
    assert elapsed < 0.25
    assert healthy_phases == ["started", "finished"]
    assert broken_phases == ["started", "finished"]
    assert stale_sink not in entity._turn_activity_sinks

    # Let the deliberately cancellation-resistant observer cleanly exit so the
    # test leaves no pending task behind.
    release_stale_cleanup.set()
    await asyncio.sleep(0)
