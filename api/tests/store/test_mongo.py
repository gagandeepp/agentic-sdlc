import asyncio
from typing import Any

import pytest
from pydantic import ValidationError
from pymongo import AsyncMongoClient
from pymongo.errors import DuplicateKeyError

from app.events.models import Checkpoint, RunStatus
from app.store.mongo import MongoStore, NewRun, RunNotFound
from tests.factories import make_event

NEW = NewRun(requirement="Add CSV export", repo="demo/orders-app")


async def _status(store: MongoStore, run_id: str) -> RunStatus:
    run = await store.get_run(run_id)
    assert run is not None
    return run.status


async def test_create_get_and_list_newest_first(store: MongoStore) -> None:
    first = await store.create_run(NEW)
    await asyncio.sleep(0.01)  # Mongo dates have millisecond precision
    second = await store.create_run(NEW)
    fetched = await store.get_run(first.id)
    assert fetched is not None
    assert fetched.status == RunStatus.PENDING
    assert fetched.created_at.tzinfo is not None
    assert [r.id for r in await store.list_runs()] == [second.id, first.id]
    assert await store.get_run("run_missing") is None


async def test_next_seq_increments_per_run(store: MongoStore) -> None:
    a = await store.create_run(NEW)
    b = await store.create_run(NEW)
    assert [await store.next_seq(a.id) for _ in range(3)] == [1, 2, 3]
    assert await store.next_seq(b.id) == 1
    with pytest.raises(RunNotFound):
        await store.next_seq("run_missing")


async def test_concurrent_next_seq_is_unique_and_contiguous(store: MongoStore) -> None:
    run = await store.create_run(NEW)
    seqs = await asyncio.gather(*(store.next_seq(run.id) for _ in range(50)))
    assert sorted(seqs) == list(range(1, 51))


async def test_events_are_listed_in_seq_order_after_a_cursor(store: MongoStore) -> None:
    run = await store.create_run(NEW)
    for seq in (2, 1, 3):
        await store.insert_event(
            make_event("agent.text", {"text": str(seq)}, run_id=run.id, seq=seq)
        )
    assert [e.seq async for e in store.list_events_after(run.id)] == [1, 2, 3]
    assert [e.seq async for e in store.list_events_after(run.id, 1)] == [2, 3]


async def test_duplicate_seq_is_rejected(store: MongoStore) -> None:
    run = await store.create_run(NEW)
    await store.insert_event(make_event("agent.text", {"text": "a"}, run_id=run.id, seq=1))
    with pytest.raises(DuplicateKeyError):
        await store.insert_event(make_event("agent.text", {"text": "b"}, run_id=run.id, seq=1))


async def test_indexes_are_created_lazily_on_first_run(mongo_uri: str) -> None:
    client: AsyncMongoClient[dict[str, Any]] = AsyncMongoClient(mongo_uri, tz_aware=True)
    lazy = MongoStore(client["test_lazy_indexes"])  # ensure_indexes() deliberately not called
    try:
        run = await lazy.create_run(NEW)
        await lazy.insert_event(make_event("agent.text", {"text": "a"}, run_id=run.id))
        with pytest.raises(DuplicateKeyError):
            await lazy.insert_event(make_event("agent.text", {"text": "b"}, run_id=run.id))
    finally:
        await client.drop_database("test_lazy_indexes")
        await client.close()


async def test_projection_tracks_status_phase_spend_and_tokens(store: MongoStore) -> None:
    run = await store.create_run(NEW)
    usage = {
        "model": "m", "input_tokens": 100, "output_tokens": 10, "cache_creation_input_tokens": 5,
        "cache_read_input_tokens": 40, "context_tokens": 155, "context_max": 200_000, "cost_usd": 0.25,
    }  # fmt: skip
    steps = [
        make_event("run.status_changed", {"status": "running"}, run_id=run.id, seq=1),
        make_event("phase.started", {"attempt": 1}, run_id=run.id, seq=2, phase="ba"),
        make_event("agent.usage", usage, run_id=run.id, seq=3, agent_id="ba"),
        make_event("agent.usage", usage, run_id=run.id, seq=4, agent_id="ba"),
    ]
    for event in steps:
        await store.apply_projection(event)
    projected = await store.get_run(run.id)
    assert projected is not None
    assert projected.status == RunStatus.RUNNING
    assert projected.phase == "ba"
    assert projected.spent_usd == pytest.approx(0.5)
    assert projected.tokens.input_tokens == 200
    assert projected.tokens.cache_read_input_tokens == 80

    gate = {
        "gate_id": "g1",
        "gate": "requirements",
        "actions": ["approve"],
        "artifact_id": None,
        "reason": None,
    }
    await store.apply_projection(make_event("gate.opened", gate, run_id=run.id, seq=5))
    assert await _status(store, run.id) == RunStatus.WAITING_GATE
    resolved = {"gate_id": "g1", "action": "approve", "feedback": None}
    await store.apply_projection(make_event("gate.resolved", resolved, run_id=run.id, seq=6))
    assert await _status(store, run.id) == RunStatus.RUNNING
    budget = {"spent_usd": 0.75, "budget_usd": 5.0}
    await store.apply_projection(make_event("budget.updated", budget, run_id=run.id, seq=7))
    projected = await store.get_run(run.id)
    assert projected is not None
    assert projected.spent_usd == pytest.approx(0.75)
    await store.apply_projection(
        make_event("run.completed", {"pr_url": None}, run_id=run.id, seq=8)
    )
    assert await _status(store, run.id) == RunStatus.COMPLETED


async def test_set_checkpoint_is_durable_and_raises_for_unknown_runs(store: MongoStore) -> None:
    run = await store.create_run(NEW)
    await store.set_checkpoint(run.id, Checkpoint.CLONED, {"base_sha": "abc"})
    fetched = await store.get_run(run.id)
    assert fetched is not None
    assert fetched.checkpoint is Checkpoint.CLONED
    assert fetched.checkpoint_data == {"base_sha": "abc"}
    with pytest.raises(RunNotFound):
        await store.set_checkpoint("run_missing", Checkpoint.CLONED, {})


async def test_projection_records_the_pr_url_and_interrupted_is_not_terminal(
    store: MongoStore,
) -> None:
    run = await store.create_run(NEW)
    opened = make_event(
        "pr.opened", {"url": "https://x/pull/1", "number": 1, "draft": True}, run_id=run.id
    )
    await store.apply_projection(opened)
    await store.insert_event(make_event("run.interrupted", {}, run_id=run.id))
    fetched = await store.get_run(run.id)
    assert fetched is not None and fetched.pr_url == "https://x/pull/1"
    assert not await store.last_event_is_terminal(run.id)


async def test_list_runs_in_filters_by_status_and_mode_defaults_to_live(store: MongoStore) -> None:
    running = await store.create_run(NEW)
    done = await store.create_run(NEW.model_copy(update={"mode": "scripted"}))
    await store.apply_projection(
        make_event("run.status_changed", {"status": "running"}, run_id=running.id)
    )
    await store.apply_projection(make_event("run.completed", {}, run_id=done.id))
    found = await store.list_runs_in([RunStatus.RUNNING, RunStatus.PENDING])
    assert [r.id for r in found] == [running.id]
    assert running.mode == "live" and done.mode == "scripted"
    with pytest.raises(ValidationError):
        NewRun(requirement="x" * 4001, repo="o/r")
