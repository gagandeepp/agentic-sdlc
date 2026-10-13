"""A run's wall-clock limit that stops counting while it waits on people or CI."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager


class ActiveClock:
    def __init__(self, limit_s: float, *, used_s: float = 0.0) -> None:
        self._limit = limit_s
        self._used = used_s
        self._timeout: asyncio.Timeout | None = None
        self._paused_left: float | None = None

    def used_s(self) -> float:
        if self._timeout is None:
            return self._used
        if self._paused_left is not None:
            return self._limit - self._paused_left
        when = self._timeout.when()
        left = 0.0 if when is None else max(0.0, when - asyncio.get_running_loop().time())
        return self._limit - left

    @asynccontextmanager
    async def running(self) -> AsyncIterator[None]:
        """Raise TimeoutError in the body once the active time reaches the limit."""
        async with asyncio.timeout(max(0.0, self._limit - self._used)) as timeout:
            self._timeout = timeout
            try:
                yield
            finally:
                self._used = min(self._limit, self.used_s())
                self._timeout = None

    @asynccontextmanager
    async def paused(self) -> AsyncIterator[None]:
        timeout = self._timeout
        if timeout is None or self._paused_left is not None:
            yield
            return
        loop = asyncio.get_running_loop()
        when = timeout.when()
        left = 0.0 if when is None else max(0.0, when - loop.time())
        self._paused_left = left
        timeout.reschedule(None)
        try:
            yield
        finally:
            self._paused_left = None
            timeout.reschedule(loop.time() + left)
