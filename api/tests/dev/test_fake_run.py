from collections import Counter
from itertools import pairwise
from pathlib import Path

import pytest
from asgi_lifespan import LifespanManager
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.dev.fake_run import (
    FAKE_BUDGET_USD,
    build_fake_events,
    fake_script,
    play_fake_run,
)
from app.events.bus import EventBus
from app.events.models import EVENT_ADAPTER, AgentUsageEvent, BudgetUpdatedEvent, RunStatus
from app.main import create_app
from app.settings import Settings
from app.store.mongo import MongoStore, NewRun
from tests.sse_utils import parse_sse

FIXTURE = Path(__file__).resolve().parents[3] / "fixtures" / "demo-runs" / "fake-basic.jsonl"


def test_script_is_a_complete_consistent_run() -> None:
    events = build_fake_events()
    types = [e.type for e in events]
    assert [e.seq for e in events] == list(range(1, len(events) + 1))
    assert types[0] == "run.created"
    assert types[-1] == "run.completed"
    assert all(a.ts <= b.ts for a, b in pairwise(events))
    assert {"guardrail.blocked", "loop.iteration", "handoff", "merge.completed"} <= set(types)

    started = Counter(e.agent_id for e in events if e.type == "agent.started")
    finished = Counter(e.agent_id for e in events if e.type == "agent.finished")
    assert started == finished
    assert set(started) == {"ba", "architect", "dev-1", "dev-2", "reviewer", "qa", "devops"}

    opened = [e.payload.gate_id for e in events if e.type == "gate.opened"]
    resolved = [e.payload.gate_id for e in events if e.type == "gate.resolved"]
    assert opened == resolved == ["gate_req", "gate_design", "gate_release"]

    total = sum(e.payload.cost_usd for e in events if isinstance(e, AgentUsageEvent))
    budgets = [e for e in events if isinstance(e, BudgetUpdatedEvent)]
    assert budgets[-1].payload.spent_usd == pytest.approx(total)
    assert total < FAKE_BUDGET_USD


def test_committed_fixture_matches_the_script() -> None:
    expected = [EVENT_ADAPTER.dump_json(e).decode() for e in build_fake_events()]
    assert FIXTURE.read_text().splitlines() == expected, "run `make fixtures`"


async def test_play_fake_run_persists_the_script(store: MongoStore) -> None:
    bus = EventBus(store)
    run = await store.create_run(NewRun(requirement="r", repo="o/r"))
    await play_fake_run(bus, run.id, speed=100_000)
    stored = [e.type async for e in store.list_events_after(run.id)]
    assert stored == [step.event_type for step in fake_script()]
    record = await store.get_run(run.id)
    assert record is not None
    assert record.status == RunStatus.COMPLETED


async def test_dev_endpoint_starts_a_fake_run_that_streams_to_completion(
    client: AsyncClient,
) -> None:
    created = await client.post("/api/dev/fake-runs", params={"speed": 100_000})
    assert created.status_code == 201
    run_id = created.json()["id"]
    response = await client.get(f"/api/runs/{run_id}/events")
    messages = parse_sse(response.text)
    assert len(messages) == len(fake_script())
    assert messages[-1][1]["type"] == "run.completed"


async def test_dev_endpoint_is_absent_unless_enabled(settings: Settings) -> None:
    app: FastAPI = create_app(settings.model_copy(update={"enable_dev_routes": False}))
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            assert (await c.post("/api/dev/fake-runs")).status_code == 404
