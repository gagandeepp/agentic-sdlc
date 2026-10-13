"""Development-only endpoints (mounted when ENABLE_DEV_ROUTES=true)."""

import asyncio
import logging
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.dev.fake_run import FAKE_BUDGET_USD, FAKE_REPO, FAKE_REQUIREMENT, play_fake_run
from app.dev.probe import ProbeUnavailable, execute_probe, prepare_probe
from app.events.bus import EventBus
from app.events.models import Role
from app.store.mongo import MongoStore, NewRun, RunRecord

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/dev", tags=["dev"])


def _log_failure(task: asyncio.Task[None]) -> None:
    if not task.cancelled() and task.exception() is not None:
        log.error("dev background task failed", exc_info=task.exception())


def _track(request: Request, task: asyncio.Task[None]) -> None:
    background: set[asyncio.Task[None]] = request.app.state.background_tasks
    background.add(task)
    task.add_done_callback(background.discard)
    task.add_done_callback(_log_failure)


class ProbeRequest(BaseModel):
    role: Role = Role.BA
    requirement: str = Field(min_length=1)


@router.post("/agent-probe", status_code=201)
async def start_agent_probe(request: Request, body: ProbeRequest) -> RunRecord:
    try:
        ctx = await prepare_probe(request.app.state, role=body.role, requirement=body.requirement)
    except ProbeUnavailable as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    async def run_probe() -> None:
        await execute_probe(request.app.state, ctx)

    _track(request, asyncio.create_task(run_probe()))
    return ctx.run


@router.post("/fake-runs", status_code=201)
async def start_fake_run(
    request: Request, speed: Annotated[float, Query(gt=0, le=100_000)] = 1.0
) -> RunRecord:
    store: MongoStore = request.app.state.store
    bus: EventBus = request.app.state.bus
    run = await store.create_run(
        NewRun(
            requirement=FAKE_REQUIREMENT,
            repo=FAKE_REPO,
            budget_usd=FAKE_BUDGET_USD,
            max_parallel_devs=2,
        )
    )
    _track(request, asyncio.create_task(play_fake_run(bus, run.id, speed=speed)))
    return run
