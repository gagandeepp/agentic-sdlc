"""Development-only endpoints (mounted when ENABLE_DEV_ROUTES=true)."""

import asyncio
import logging
from typing import Annotated

from fastapi import APIRouter, Query, Request

from app.dev.fake_run import FAKE_BUDGET_USD, FAKE_REPO, FAKE_REQUIREMENT, play_fake_run
from app.events.bus import EventBus
from app.store.mongo import MongoStore, NewRun, RunRecord

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/dev", tags=["dev"])


def _log_failure(task: asyncio.Task[None]) -> None:
    if not task.cancelled() and task.exception() is not None:
        log.error("fake run failed", exc_info=task.exception())


@router.post("/fake-runs", status_code=201)
async def start_fake_run(
    request: Request, speed: Annotated[float, Query(gt=0, le=100_000)] = 1.0
) -> RunRecord:
    store: MongoStore = request.app.state.store
    bus: EventBus = request.app.state.bus
    background: set[asyncio.Task[None]] = request.app.state.background_tasks
    run = await store.create_run(
        NewRun(
            requirement=FAKE_REQUIREMENT,
            repo=FAKE_REPO,
            budget_usd=FAKE_BUDGET_USD,
            max_parallel_devs=2,
        )
    )
    task = asyncio.create_task(play_fake_run(bus, run.id, speed=speed))
    background.add(task)
    task.add_done_callback(background.discard)
    task.add_done_callback(_log_failure)
    return run
