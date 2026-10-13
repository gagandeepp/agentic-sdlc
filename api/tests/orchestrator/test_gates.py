import asyncio

import pytest

from app.events.bus import EventBus
from app.events.models import GateKind, GateOpenedPayload, Phase
from app.orchestrator.gates import GateBroker, GateDecision, GateError
from app.store.mongo import MongoStore, NewRun

FULL = ("approve", "revise", "edit", "abort")


async def _opened(store: MongoStore, run_id: str) -> GateOpenedPayload:
    for _ in range(200):
        events = [e async for e in store.list_events_after(run_id) if e.type == "gate.opened"]
        if events:
            return events[-1].payload  # type: ignore[return-value]
        await asyncio.sleep(0.01)
    raise AssertionError("no gate opened")


async def test_a_gate_waits_for_a_valid_decision(store: MongoStore) -> None:
    broker = GateBroker(EventBus(store))
    run = await store.create_run(NewRun(requirement="r", repo="o/r"))
    waiter = asyncio.create_task(
        broker.wait(
            run.id, GateKind.DESIGN, actions=FULL, phase=Phase.ARCHITECT, artifact_id="art_1"
        )
    )
    gate = await _opened(store, run.id)
    assert gate.actions == list(FULL) and gate.artifact_id == "art_1"
    for decision, status in [
        (GateDecision("edit"), 422),
        (GateDecision("revise", feedback="  "), 422),
    ]:
        with pytest.raises(GateError) as error:
            broker.validate(run.id, gate.gate_id, decision)
        assert error.value.status == status
    with pytest.raises(GateError) as stale:
        broker.validate(run.id, "gate_other", GateDecision("approve"))
    assert stale.value.status == 409

    await broker.resolve(run.id, gate.gate_id, GateDecision("revise", feedback="split story 2"))
    assert await waiter == GateDecision("revise", feedback="split story 2")
    events = [e async for e in store.list_events_after(run.id)]
    assert [e.type for e in events] == ["gate.opened", "gate.resolved"]
    assert events[1].payload.feedback == "split story 2"  # type: ignore[union-attr]
    assert events[1].phase is Phase.ARCHITECT
    with pytest.raises(GateError) as late:
        await broker.resolve(run.id, gate.gate_id, GateDecision("approve"))
    assert late.value.status == 409


async def test_actions_outside_the_gate_are_rejected(store: MongoStore) -> None:
    broker = GateBroker(EventBus(store))
    run = await store.create_run(NewRun(requirement="r", repo="o/r"))
    waiter = asyncio.create_task(
        broker.wait(run.id, GateKind.ESCALATION, actions=("approve", "revise", "abort"), phase=None)
    )
    gate = await _opened(store, run.id)
    with pytest.raises(GateError) as error:
        broker.validate(run.id, gate.gate_id, GateDecision("edit", markdown="# x"))
    assert error.value.status == 422 and "escalation" in error.value.detail
    await broker.resolve(run.id, gate.gate_id, GateDecision("abort"))
    assert (await waiter).action == "abort"


async def test_concurrent_decisions_settle_exactly_once(store: MongoStore) -> None:
    broker = GateBroker(EventBus(store))
    run = await store.create_run(NewRun(requirement="r", repo="o/r"))
    waiter = asyncio.create_task(
        broker.wait(run.id, GateKind.RELEASE, actions=("approve", "abort"), phase=None)
    )
    gate = await _opened(store, run.id)
    results = await asyncio.gather(
        broker.resolve(run.id, gate.gate_id, GateDecision("approve")),
        broker.resolve(run.id, gate.gate_id, GateDecision("abort")),
        return_exceptions=True,
    )
    assert results[0] is None and isinstance(results[1], GateError) and results[1].status == 409
    assert (await waiter).action == "approve"
    resolved = [e async for e in store.list_events_after(run.id) if e.type == "gate.resolved"]
    assert len(resolved) == 1


async def test_a_cancelled_wait_closes_the_gate(store: MongoStore) -> None:
    broker = GateBroker(EventBus(store))
    run = await store.create_run(NewRun(requirement="r", repo="o/r"))
    with pytest.raises(GateError) as none:
        broker.validate(run.id, "gate_x", GateDecision("approve"))
    assert none.value.status == 409
    waiter = asyncio.create_task(broker.wait(run.id, GateKind.DESIGN, actions=FULL, phase=None))
    gate = await _opened(store, run.id)
    assert broker.is_open(run.id)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert not broker.is_open(run.id)
    with pytest.raises(GateError):
        broker.validate(run.id, gate.gate_id, GateDecision("approve"))
