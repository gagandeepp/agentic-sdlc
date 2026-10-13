"""Run an extra ASGI app on a loopback port inside the API process."""

import asyncio
import contextlib
from collections.abc import Iterator

import uvicorn
from starlette.types import ASGIApp


class _NoSignalServer(uvicorn.Server):
    @contextlib.contextmanager
    def capture_signals(self) -> Iterator[None]:
        # The main uvicorn server owns SIGTERM/SIGINT; this one is stopped by the app's lifespan.
        yield


class EmbeddedServer:
    def __init__(self, app: ASGIApp, port: int = 0) -> None:
        config = uvicorn.Config(
            app, host="127.0.0.1", port=port, lifespan="off", log_config=None, access_log=False
        )
        self._server = _NoSignalServer(config)
        self._task: asyncio.Task[None] | None = None

    async def start(self, startup_s: float = 5.0) -> None:
        self._task = asyncio.create_task(self._server.serve())
        async with asyncio.timeout(startup_s):
            while not self._server.started:
                if self._task.done():
                    await self._task  # surfaces bind errors
                await asyncio.sleep(0.02)

    @property
    def port(self) -> int:
        return int(self._server.servers[0].sockets[0].getsockname()[1])

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    async def stop(self) -> None:
        if self._task is None:
            return
        self._server.should_exit = True
        with contextlib.suppress(TimeoutError, asyncio.CancelledError):
            await asyncio.wait_for(self._task, 5)
        self._task = None
