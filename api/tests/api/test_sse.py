import asyncio
import json
from collections.abc import AsyncIterator

from app.api.sse import PING, RETRY, format_event, sse_stream
from app.events.bus import EventBus, SubscriberOverflow
from app.events.models import EVENT_ADAPTER, AgentTextPayload, RunCompletedPayload, RunEvent
from app.store.mongo import MongoStore, NewRun
from tests.factories import make_event


async def _from(events: list[RunEvent], *, delay: float = 0.0) -> AsyncIterator[RunEvent]:
    for event in events:
        await asyncio.sleep(delay)
        yield event


async def _drain(stream: AsyncIterator[str]) -> list[str]:
    return [chunk async for chunk in stream]


def test_format_event_uses_seq_as_id_and_round_trips() -> None:
    event = make_event("agent.text", {"text": "hello\nworld"}, seq=5)
    frame = format_event(event)
    assert frame.startswith("id: 5\ndata: ")
    assert frame.endswith("\n\n")
    data = frame.split("data: ", 1)[1].strip()
    assert EVENT_ADAPTER.validate_python(json.loads(data)) == event


async def test_stream_sets_retry_then_emits_events_and_ends() -> None:
    events = [make_event("agent.text", {"text": str(i)}, seq=i) for i in (1, 2)]
    chunks = await _drain(sse_stream(_from(events), ping_seconds=5))
    assert chunks[0] == "retry: 2000\n\n"
    assert chunks[1:] == [format_event(e) for e in events]


async def test_stream_pings_while_idle() -> None:
    events = [make_event("agent.text", {"text": "late"}, seq=1)]
    chunks = await _drain(sse_stream(_from(events, delay=0.35), ping_seconds=0.1))
    assert PING in chunks
    assert chunks[-1] == format_event(events[0])


async def test_stalled_client_overflows_the_subscription_instead_of_buffering(
    store: MongoStore,
) -> None:
    bus = EventBus(store, queue_size=2)
    run = await store.create_run(NewRun(requirement="r", repo="o/r"))
    stream = sse_stream(bus.subscribe(run.id), ping_seconds=5)
    assert await anext(stream) == RETRY  # starts the pump; the client then stops reading
    await asyncio.sleep(0.05)
    for i in range(20):
        await bus.emit(run.id, "agent.text", AgentTextPayload(text=str(i)))
    await bus.emit(run.id, "run.completed", RunCompletedPayload(pr_url=None))
    rest = await asyncio.wait_for(_drain(stream), 5)
    frames = [chunk for chunk in rest if chunk.startswith("id: ")]
    assert 0 < len(frames) < 21  # cut off by overflow, so the browser reconnects and catches up
    assert not any('"run.completed"' in chunk for chunk in rest)


async def test_overflow_ends_the_stream_so_the_browser_reconnects() -> None:
    async def overflowing() -> AsyncIterator[RunEvent]:
        yield make_event("agent.text", {"text": "1"}, seq=1)
        raise SubscriberOverflow("run_test")

    chunks = await _drain(sse_stream(overflowing(), ping_seconds=5))
    assert chunks[-1].startswith("id: 1\n")
