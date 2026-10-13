import json

from asgi_lifespan import LifespanManager
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.events.bus import EventBus
from app.events.models import RunCompletedPayload, RunCreatedPayload, RunStatusChangedPayload
from app.main import create_app
from app.settings import Settings
from app.store.mongo import MongoStore, NewRun
from tests.sse_utils import parse_sse

NEW = NewRun(requirement="Add CSV export", repo="demo/orders-app")


async def _finished_run(app: FastAPI) -> str:
    store: MongoStore = app.state.store
    bus: EventBus = app.state.bus
    run = await store.create_run(NEW)
    await bus.emit(
        run.id,
        "run.created",
        RunCreatedPayload(
            requirement=NEW.requirement,
            repo=NEW.repo,
            base_branch="main",
            budget_usd=5.0,
            max_parallel_devs=3,
        ),
    )
    await bus.emit(run.id, "run.status_changed", RunStatusChangedPayload(status="running"))
    await bus.emit(run.id, "run.completed", RunCompletedPayload(pr_url=None))
    return run.id


async def test_list_and_get_runs(app: FastAPI, client: AsyncClient) -> None:
    assert (await client.get("/api/runs")).json() == []
    run_id = await _finished_run(app)
    listed = (await client.get("/api/runs")).json()
    assert [r["id"] for r in listed] == [run_id]
    fetched = await client.get(f"/api/runs/{run_id}")
    assert fetched.status_code == 200
    assert fetched.json()["status"] == "completed"
    assert (await client.get("/api/runs/run_missing")).status_code == 404


async def test_event_stream_replays_a_finished_run_and_closes(
    app: FastAPI, client: AsyncClient
) -> None:
    run_id = await _finished_run(app)
    response = await client.get(f"/api/runs/{run_id}/events")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    messages = parse_sse(response.text)
    assert [seq for seq, _ in messages] == [1, 2, 3]
    assert messages[-1][1]["type"] == "run.completed"


async def test_last_event_id_resumes_after_that_seq(app: FastAPI, client: AsyncClient) -> None:
    run_id = await _finished_run(app)
    response = await client.get(f"/api/runs/{run_id}/events", headers={"Last-Event-ID": "2"})
    assert [seq for seq, _ in parse_sse(response.text)] == [3]


async def test_after_query_param_resumes(app: FastAPI, client: AsyncClient) -> None:
    run_id = await _finished_run(app)
    response = await client.get(f"/api/runs/{run_id}/events", params={"after": 1})
    assert [seq for seq, _ in parse_sse(response.text)] == [2, 3]


async def test_events_for_unknown_run_is_404_not_a_hanging_stream(client: AsyncClient) -> None:
    response = await client.get("/api/runs/run_missing/events")
    assert response.status_code == 404


async def test_events_jsonl_export(app: FastAPI, client: AsyncClient) -> None:
    run_id = await _finished_run(app)
    response = await client.get(f"/api/runs/{run_id}/events.jsonl")
    lines = [json.loads(line) for line in response.text.splitlines()]
    assert [e["seq"] for e in lines] == [1, 2, 3]
    assert (await client.get("/api/runs/run_missing/events.jsonl")).status_code == 404


async def test_database_outage_returns_503_json() -> None:
    app = create_app(Settings(mongo_uri="mongodb://127.0.0.1:1", mongo_timeout_ms=200))
    async with LifespanManager(app):  # must start even though Mongo is down
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            response = await c.get("/api/runs")
    assert response.status_code == 503
    assert response.json() == {"detail": "database unavailable"}
