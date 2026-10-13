import asyncio

import pytest
from pymongo.errors import AutoReconnect

from app.events.bus import EventBus, SubscriberOverflow
from app.events.models import AgentTextPayload, HandoffPayload, RunCompletedPayload, RunEvent
from app.store.mongo import MongoStore, NewRun, RunRecord

NEW = NewRun(requirement="r", repo="o/r")


async def _text(bus: EventBus, run: RunRecord, text: str = "x") -> None:
    await bus.emit(run.id, "agent.text", AgentTextPayload(text=text), agent_id="ba")


async def _complete(bus: EventBus, run: RunRecord) -> None:
    await bus.emit(run.id, "run.completed", RunCompletedPayload(pr_url=None))


async def _collect(bus: EventBus, run: RunRecord, after: int = 0) -> list[int]:
    return [event.seq async for event in bus.subscribe(run.id, after)]


async def test_emit_assigns_contiguous_seq_and_persists(store: MongoStore) -> None:
    bus = EventBus(store)
    run = await store.create_run(NEW)
    for i in range(3):
        await _text(bus, run, str(i))
    assert [e.seq async for e in store.list_events_after(run.id)] == [1, 2, 3]


async def test_emit_rejects_mismatched_payload_without_consuming_a_seq(store: MongoStore) -> None:
    bus = EventBus(store)
    run = await store.create_run(NEW)
    wrong = HandoffPayload(from_agent="ba", to_agent="architect", summary="x")
    with pytest.raises(ValueError, match=r"agent\.text expects AgentTextPayload"):
        await bus.emit(run.id, "agent.text", wrong)
    with pytest.raises(ValueError, match="unknown event type"):
        await bus.emit(run.id, "agent.teleported", AgentTextPayload(text="x"))
    event = await bus.emit(run.id, "agent.text", AgentTextPayload(text="ok"))
    assert event.seq == 1


async def test_subscribe_catches_up_then_streams_live_until_terminal(store: MongoStore) -> None:
    bus = EventBus(store)
    run = await store.create_run(NEW)
    await _text(bus, run)
    await _text(bus, run)
    consumer = asyncio.create_task(_collect(bus, run))
    await asyncio.sleep(0.05)
    await _text(bus, run)
    await _complete(bus, run)
    assert await asyncio.wait_for(consumer, 5) == [1, 2, 3, 4]


async def test_subscribe_resumes_after_a_given_seq(store: MongoStore) -> None:
    bus = EventBus(store)
    run = await store.create_run(NEW)
    for _ in range(4):
        await _text(bus, run)
    await _complete(bus, run)
    assert await _collect(bus, run, after=2) == [3, 4, 5]


async def test_resuming_after_the_terminal_event_ends_instead_of_hanging(store: MongoStore) -> None:
    bus = EventBus(store)
    run = await store.create_run(NEW)
    await _text(bus, run)
    await _complete(bus, run)
    # A client that already saw the final event reconnects with Last-Event-ID = 2.
    assert await asyncio.wait_for(_collect(bus, run, after=2), 2) == []


async def test_failed_insert_does_not_leave_a_seq_hole(
    store: MongoStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    bus = EventBus(store)
    run = await store.create_run(NEW)
    real_insert = store.insert_event
    calls = 0

    async def flaky_insert(event: RunEvent) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise AutoReconnect("write failed")
        await real_insert(event)

    monkeypatch.setattr(store, "insert_event", flaky_insert)
    await _text(bus, run, "1")
    with pytest.raises(AutoReconnect):
        await _text(bus, run, "2")
    await _text(bus, run, "3")
    assert [e.seq async for e in store.list_events_after(run.id)] == [1, 2]


async def test_insert_that_landed_despite_an_error_is_kept_and_published(
    store: MongoStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    bus = EventBus(store)
    run = await store.create_run(NEW)
    real_insert = store.insert_event

    async def write_then_lose_reply(event: RunEvent) -> None:
        await real_insert(event)
        if event.seq == 2:
            raise AutoReconnect("reply lost")

    monkeypatch.setattr(store, "insert_event", write_then_lose_reply)
    consumer = asyncio.create_task(_collect(bus, run))
    await asyncio.sleep(0.05)
    for i in range(3):
        await _text(bus, run, str(i))
    await _complete(bus, run)
    assert await asyncio.wait_for(consumer, 5) == [1, 2, 3, 4]


async def test_projection_failure_still_persists_and_publishes(
    store: MongoStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    bus = EventBus(store)
    run = await store.create_run(NEW)
    real_projection = store.apply_projection

    async def flaky_projection(event: RunEvent) -> None:
        if event.seq == 2:
            raise AutoReconnect("projection failed")
        await real_projection(event)

    monkeypatch.setattr(store, "apply_projection", flaky_projection)
    consumer = asyncio.create_task(_collect(bus, run))
    await asyncio.sleep(0.05)
    for i in range(3):
        await _text(bus, run, str(i))
    await _complete(bus, run)
    assert await asyncio.wait_for(consumer, 5) == [1, 2, 3, 4]
    assert [e.seq async for e in store.list_events_after(run.id)] == [1, 2, 3, 4]


async def test_concurrent_emits_reach_a_subscriber_in_order(store: MongoStore) -> None:
    bus = EventBus(store)
    run = await store.create_run(NEW)
    consumer = asyncio.create_task(_collect(bus, run))
    await asyncio.sleep(0.05)
    await asyncio.gather(*(_text(bus, run, str(i)) for i in range(50)))
    await _complete(bus, run)
    assert await asyncio.wait_for(consumer, 10) == list(range(1, 52))


async def test_subscriber_joining_mid_burst_sees_each_event_once(store: MongoStore) -> None:
    bus = EventBus(store)
    run = await store.create_run(NEW)
    burst = asyncio.gather(*(_text(bus, run, str(i)) for i in range(50)))
    consumer = asyncio.create_task(_collect(bus, run))
    await burst
    await _complete(bus, run)
    assert await asyncio.wait_for(consumer, 10) == list(range(1, 52))


async def test_slow_subscriber_overflows_and_must_reconnect(store: MongoStore) -> None:
    bus = EventBus(store, queue_size=2)
    run = await store.create_run(NEW)
    await _text(bus, run)
    stream = bus.subscribe(run.id)
    first = await anext(stream)  # registers the subscriber and starts catch-up
    for _ in range(10):
        await _text(bus, run)
    rest: list[int] = []
    with pytest.raises(SubscriberOverflow):
        async for event in stream:
            rest.append(event.seq)
    assert [first.seq, *rest] == [1, 2, 3]


async def test_events_after_a_final_event_are_dropped(store: MongoStore) -> None:
    bus = EventBus(store)
    run = await store.create_run(NEW)
    await _complete(bus, run)
    assert await bus.emit(run.id, "agent.text", AgentTextPayload(text="late usage")) is None
    assert [e.type async for e in store.list_events_after(run.id)] == ["run.completed"]
