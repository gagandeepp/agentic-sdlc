"""Run listing, details, live event stream (SSE) and full event-log export."""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from app.api.sse import sse_stream
from app.events.bus import EventBus
from app.events.models import EVENT_ADAPTER
from app.settings import Settings
from app.store.mongo import MongoStore, RunRecord

router = APIRouter(prefix="/api/runs", tags=["runs"])


def get_store(request: Request) -> MongoStore:
    store: MongoStore = request.app.state.store
    return store


def get_bus(request: Request) -> EventBus:
    bus: EventBus = request.app.state.bus
    return bus


def get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


StoreDep = Annotated[MongoStore, Depends(get_store)]


async def _require_run(store: MongoStore, run_id: str) -> RunRecord:
    run = await store.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    return run


def resume_point(after: int | None, last_event_id: str | None) -> int:
    # Last-Event-ID wins: it is what the browser actually received before reconnecting.
    if last_event_id and last_event_id.isdigit():
        return int(last_event_id)
    return after or 0


@router.get("")
async def list_runs(
    store: StoreDep, limit: Annotated[int, Query(ge=1, le=200)] = 50
) -> list[RunRecord]:
    return await store.list_runs(limit)


@router.get("/{run_id}")
async def get_run(run_id: str, store: StoreDep) -> RunRecord:
    return await _require_run(store, run_id)


@router.get("/{run_id}/events")
async def stream_events(
    run_id: str,
    store: StoreDep,
    bus: Annotated[EventBus, Depends(get_bus)],
    settings: Annotated[Settings, Depends(get_settings)],
    after: Annotated[int | None, Query(ge=0)] = None,
    last_event_id: Annotated[str | None, Header()] = None,
) -> StreamingResponse:
    await _require_run(store, run_id)
    events = bus.subscribe(run_id, resume_point(after, last_event_id))
    return StreamingResponse(
        sse_stream(events, ping_seconds=settings.sse_ping_seconds),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/{run_id}/events.jsonl")
async def export_events(run_id: str, store: StoreDep) -> StreamingResponse:
    await _require_run(store, run_id)

    async def lines() -> AsyncIterator[bytes]:
        async for event in store.list_events_after(run_id):
            yield EVENT_ADAPTER.dump_json(event) + b"\n"

    return StreamingResponse(lines(), media_type="application/x-ndjson")
