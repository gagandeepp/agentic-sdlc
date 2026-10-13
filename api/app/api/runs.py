"""Run listing, details, live event stream (SSE) and full event-log export."""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from app.api.sse import sse_stream
from app.events.bus import EventBus
from app.events.models import EVENT_ADAPTER, GateAction
from app.orchestrator.gates import GateDecision, GateError
from app.orchestrator.manager import RunManager, RunRejected
from app.settings import Settings
from app.store.artifacts import ArtifactRecord, ArtifactStore
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


SettingsDep = Annotated[Settings, Depends(get_settings)]


def get_manager(request: Request) -> RunManager:
    manager: RunManager = request.app.state.manager
    return manager


ManagerDep = Annotated[RunManager, Depends(get_manager)]


class StartRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requirement: str = Field(min_length=1, max_length=4000)
    repo: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    base_branch: str = Field(
        default="main", max_length=200, pattern=r"^[A-Za-z0-9][A-Za-z0-9._/-]*$"
    )
    budget_usd: float = Field(default=5.0, gt=0, le=100)


class GateDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: GateAction
    feedback: str | None = Field(default=None, max_length=4000)
    markdown: str | None = Field(default=None, max_length=50_000)


def _rejected(exc: RunRejected | GateError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=exc.detail)


@router.post("", status_code=201)
async def start_run(body: StartRunRequest, manager: ManagerDep, settings: SettingsDep) -> RunRecord:
    try:
        return await manager.start(
            requirement=body.requirement.strip(),
            repo=body.repo or settings.default_repo,
            base_branch=body.base_branch,
            budget_usd=body.budget_usd,
        )
    except RunRejected as exc:
        raise _rejected(exc) from exc


@router.post("/{run_id}/gates/{gate_id}")
async def decide_gate(
    run_id: str, gate_id: str, body: GateDecisionRequest, store: StoreDep, manager: ManagerDep
) -> dict[str, str]:
    await _require_run(store, run_id)
    try:
        await manager.decide(
            run_id, gate_id, GateDecision(body.action, body.feedback, body.markdown)
        )
    except (RunRejected, GateError) as exc:
        raise _rejected(exc) from exc
    return {"gate_id": gate_id, "action": body.action}


@router.post("/{run_id}/abort")
async def abort_run(run_id: str, store: StoreDep, manager: ManagerDep) -> RunRecord:
    try:
        await manager.abort(run_id)
    except RunRejected as exc:
        raise _rejected(exc) from exc
    return await _require_run(store, run_id)


@router.post("/{run_id}/resume")
async def resume_run(run_id: str, manager: ManagerDep) -> RunRecord:
    try:
        return await manager.resume(run_id)
    except RunRejected as exc:
        raise _rejected(exc) from exc


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
    settings: SettingsDep,
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


def get_artifacts(request: Request) -> ArtifactStore:
    artifacts: ArtifactStore = request.app.state.artifacts
    return artifacts


ArtifactsDep = Annotated[ArtifactStore, Depends(get_artifacts)]


@router.get("/{run_id}/artifacts")
async def list_artifacts(
    run_id: str, store: StoreDep, artifacts: ArtifactsDep
) -> list[ArtifactRecord]:
    await _require_run(store, run_id)
    return await artifacts.list_for_run(run_id)


@router.get("/{run_id}/artifacts/{artifact_id}")
async def get_artifact(run_id: str, artifact_id: str, artifacts: ArtifactsDep) -> ArtifactRecord:
    artifact = await artifacts.get(artifact_id)
    if artifact is None or artifact.run_id != run_id:
        raise HTTPException(status_code=404, detail="artifact not found")
    return artifact
