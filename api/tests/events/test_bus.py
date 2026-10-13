import asyncio

import pytest

from app.events.bus import EventBus, SubscriberOverflow
from app.events.models import AgentTextPayload, HandoffPayload, RunCompletedPayload
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
