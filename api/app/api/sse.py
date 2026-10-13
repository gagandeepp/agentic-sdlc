"""Server-Sent Events framing with keep-alive pings.

Events are pumped through a queue so that idle waits can time out (to send a ping) without
cancelling the underlying subscription generator.
"""

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator

from app.events.bus import SubscriberOverflow
from app.events.models import EVENT_ADAPTER, RunEvent

log = logging.getLogger(__name__)

RETRY = "retry: 2000\n\n"
PING = ": ping\n\n"


def format_event(event: RunEvent) -> str:
    # dump_json escapes newlines inside strings, so the payload is always a single data line
    return f"id: {event.seq}\ndata: {EVENT_ADAPTER.dump_json(event).decode()}\n\n"


async def sse_stream(events: AsyncIterator[RunEvent], *, ping_seconds: float) -> AsyncIterator[str]:
    queue: asyncio.Queue[RunEvent | Exception | None] = asyncio.Queue()

    async def pump() -> None:
        try:
            async for event in events:
                await queue.put(event)
            await queue.put(None)
        except Exception as exc:  # handed to the stream loop below
            await queue.put(exc)

    task = asyncio.create_task(pump())
    try:
        yield RETRY
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=ping_seconds)
            except TimeoutError:
                yield PING
                continue
            if item is None:
                return
            if isinstance(item, SubscriberOverflow):
                # Closing without a terminal event makes EventSource reconnect with
                # Last-Event-ID, which catches up from the store.
                return
            if isinstance(item, Exception):
                log.error("event stream failed", exc_info=item)
                return
            yield format_event(item)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
