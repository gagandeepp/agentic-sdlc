"""In-process event bus: ordered, persisted appends and gap-free subscriptions.

Appends for a run are serialized by a per-run lock, so `seq` order equals persistence order
equals publish order. A subscriber registers *before* reading the stored backlog. Anything
appended after registration is queued, and anything before it is in the store. Duplicates in
that overlap are dropped by `seq`. One API instance owns all live runs (Cloud Run
max-instances=1), so in-memory fan-out is sufficient.
"""

import asyncio
import logging
from collections import defaultdict
from collections.abc import AsyncIterator
from datetime import UTC, datetime

from pymongo.errors import PyMongoError

from app.events.models import (
    EVENT_ADAPTER,
    FINAL_EVENT_TYPES,
    PAYLOAD_BY_TYPE,
    TERMINAL_EVENT_TYPES,
    ContractModel,
    Phase,
    RunEvent,
)
from app.store.mongo import MongoStore

log = logging.getLogger(__name__)


class SubscriberOverflow(Exception):
    """The subscriber fell too far behind; reconnect and catch up from the store."""


class _Subscriber:
    def __init__(self, maxsize: int) -> None:
        self.queue: asyncio.Queue[RunEvent] = asyncio.Queue(maxsize=maxsize)
        self.overflowed = False


class EventBus:
    def __init__(self, store: MongoStore, *, queue_size: int = 1000) -> None:
        self._store = store
        self._queue_size = queue_size
        self._locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._subscribers: defaultdict[str, set[_Subscriber]] = defaultdict(set)
        self._closed: set[str] = set()

    async def emit(
        self,
        run_id: str,
        event_type: str,
        payload: ContractModel,
        *,
        phase: Phase | None = None,
        agent_id: str | None = None,
    ) -> RunEvent | None:
        expected = PAYLOAD_BY_TYPE.get(event_type)
        if expected is None:
            raise ValueError(f"unknown event type {event_type!r}")
        if not isinstance(payload, expected):
            raise ValueError(
                f"{event_type} expects {expected.__name__}, got {type(payload).__name__}"
            )
        async with self._locks[run_id]:
            if run_id in self._closed:
                # Late usage or text from a session that outlived its run.
                log.debug("dropping %s for finished run %s", event_type, run_id)
                return None
            seq = await self._store.next_seq(run_id)
            event = EVENT_ADAPTER.validate_python(
                {
                    "run_id": run_id,
                    "seq": seq,
                    "ts": datetime.now(UTC),
                    "type": event_type,
                    "phase": phase,
                    "agent_id": agent_id,
                    "payload": payload.model_dump(),
                }
            )
            try:
                await self._store.insert_event(event)
            except Exception:
                # The write can land even when the call fails (lost reply). Give the seq back
                # only if it did not, so the log gets neither a hole nor a duplicate.
                if not await self._store.has_event(run_id, seq):
                    await self._store.release_seq(run_id, seq)
                    raise
            self._publish(event)
            if event.type in FINAL_EVENT_TYPES:
                self._closed.add(run_id)
            try:
                await self._store.apply_projection(event)
            except PyMongoError:
                # The projection is derived from the log; a missed update must not hide a
                # durable event from live viewers.
                log.exception("run projection update failed for %s seq %d", run_id, seq)
        return event

    async def subscribe(self, run_id: str, after_seq: int = 0) -> AsyncIterator[RunEvent]:
        subscriber = _Subscriber(self._queue_size)
        async with self._locks[run_id]:
            # Read under the lock so the answer matches the log at registration time.
            finished = await self._store.last_event_is_terminal(run_id)
            self._subscribers[run_id].add(subscriber)
        last = after_seq
        try:
            async for event in self._store.list_events_after(run_id, after_seq):
                yield event
                last = event.seq
                if event.type in TERMINAL_EVENT_TYPES:
                    return
            if finished:
                # The terminal event is at or before after_seq (a client resuming after the
                # end); nothing else will arrive, so end instead of waiting forever.
                return
            while True:
                if subscriber.overflowed and subscriber.queue.empty():
                    raise SubscriberOverflow(run_id)
                event = await subscriber.queue.get()
                if event.seq <= last:
                    continue
                yield event
                last = event.seq
                if event.type in TERMINAL_EVENT_TYPES:
                    return
        finally:
            self._subscribers[run_id].discard(subscriber)

    def _publish(self, event: RunEvent) -> None:
        for subscriber in list(self._subscribers[event.run_id]):
            try:
                subscriber.queue.put_nowait(event)
            except asyncio.QueueFull:
                subscriber.overflowed = True
                self._subscribers[event.run_id].discard(subscriber)
