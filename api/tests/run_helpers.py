"""Drive runs through the HTTP API in tests: start an app, wait for gates, approve them."""

import asyncio
import contextlib
from collections.abc import AsyncIterator, Callable

from asgi_lifespan import LifespanManager
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.events.models import GateOpenedPayload, RunEvent
from app.main import create_app
from app.settings import Settings


@contextlib.asynccontextmanager
async def running(settings: Settings) -> AsyncIterator[tuple[FastAPI, AsyncClient]]:
    app = create_app(settings)
    async with LifespanManager(app, shutdown_timeout=30):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield app, client


async def events_of(app: FastAPI, run_id: str) -> list[RunEvent]:
    return [e async for e in app.state.store.list_events_after(run_id)]


async def wait_for(
    app: FastAPI, run_id: str, done: Callable[[list[RunEvent]], bool], *, limit_s: float = 20.0
) -> list[RunEvent]:
    async with asyncio.timeout(limit_s):
        while True:
            events = await events_of(app, run_id)
            if done(events):
                return events
            await asyncio.sleep(0.02 if limit_s <= 60 else 2.0)


def open_gate(events: list[RunEvent]) -> GateOpenedPayload | None:
    """The newest undecided gate; one left open by an interrupted run is dead."""
    resolved = {e.payload.gate_id for e in events if e.type == "gate.resolved"}
    restart = max((e.seq for e in events if e.type == "run.interrupted"), default=0)
    pending = [
        e.payload
        for e in events
        if e.type == "gate.opened" and e.payload.gate_id not in resolved and e.seq > restart
    ]
    return pending[-1] if pending else None  # type: ignore[return-value]


def ended(events: list[RunEvent]) -> bool:
    return bool(events) and events[-1].type in ("run.completed", "run.failed", "run.aborted")


async def gate_id(app: FastAPI, run_id: str, kind: str, *, limit_s: float = 20.0) -> str:
    """Wait for an open gate of `kind`; fail fast if the run ends or another gate opens."""

    def ready(events: list[RunEvent]) -> bool:
        gate = open_gate(events)
        return ended(events) or (gate is not None and app.state.gates.is_open(run_id))

    events = await wait_for(app, run_id, ready, limit_s=limit_s)
    gate = open_gate(events)
    assert gate is not None and not ended(events), f"the run ended: {events[-1].payload}"
    assert gate.gate == kind, f"expected a {kind} gate, got {gate.gate}: {gate.reason}"
    return gate.gate_id


async def approve(
    client: AsyncClient, app: FastAPI, run_id: str, kind: str, *, limit_s: float = 20.0
) -> str:
    gate = await gate_id(app, run_id, kind, limit_s=limit_s)
    response = await client.post(f"/api/runs/{run_id}/gates/{gate}", json={"action": "approve"})
    assert response.status_code == 200, response.text
    return gate
