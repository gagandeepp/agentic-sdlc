import asyncio

import pytest
from pydantic import SecretStr

from app.events.models import Phase
from app.llm.pricing import load_pricing
from app.llm.registry import ProxyRegistry, RunBudget

KEY = SecretStr("sk-ant-test-registry")


def _registry() -> ProxyRegistry:
    registry = ProxyRegistry(load_pricing())
    registry.open_run("run_1", KEY, budget_usd=1.0)
    return registry


def _grant(registry: ProxyRegistry, agent_id: str = "ba") -> str:
    return registry.grant(
        run_id="run_1", agent_id=agent_id, phase=Phase.BA, allowed_models={"claude-sonnet-5-5"}
    ).token


def test_budget_reserve_settle_release_and_exhaustion() -> None:
    budget = RunBudget(budget_usd=1.0)
    assert budget.try_reserve(0.4)
    budget.settle(0.4, actual=0.1)
    assert (budget.spent_usd, budget.reserved_usd) == (pytest.approx(0.1), pytest.approx(0.0))
    assert budget.try_reserve(0.5)
    budget.release(0.5)
    assert budget.remaining_usd == pytest.approx(0.9)
    assert not budget.try_reserve(0.95)
    assert budget.exhausted


def test_settling_past_the_budget_marks_it_exhausted() -> None:
    budget = RunBudget(budget_usd=0.01)
    assert budget.try_reserve(0.005)
    budget.settle(0.005, actual=0.02)
    assert budget.exhausted


def test_grants_are_unique_and_scoped_to_open_runs() -> None:
    registry = _registry()
    first, second = _grant(registry), _grant(registry, "architect")
    assert first != second
    found = registry.lookup(first)
    assert found is not None and found.agent_id == "ba" and found.run_id == "run_1"
    assert registry.lookup("agt_unknown") is None
    assert registry.lookup(None) is None
    with pytest.raises(KeyError):
        registry.grant(run_id="run_closed", agent_id="ba", phase=None, allowed_models=set())
    assert registry.api_key("run_1").get_secret_value() == "sk-ant-test-registry"


async def test_drain_waits_for_in_flight_requests_then_revokes() -> None:
    registry = _registry()
    token = _grant(registry)
    registry.begin(token)
    drain = asyncio.create_task(registry.drain_and_revoke(token))
    await asyncio.sleep(0.05)
    assert not drain.done()
    registry.end(token)
    await asyncio.wait_for(drain, 1)
    assert registry.lookup(token) is None


async def test_spawned_accounting_counts_as_in_flight() -> None:
    registry = _registry()
    token = _grant(registry)
    finished = asyncio.Event()

    async def accounting() -> None:
        await asyncio.sleep(0.05)
        finished.set()

    registry.begin(token)
    registry.spawn(token, accounting())
    await asyncio.wait_for(registry.drain_and_revoke(token), 1)
    assert finished.is_set()


async def test_drain_gives_up_after_its_timeout() -> None:
    registry = _registry()
    token = _grant(registry)
    registry.begin(token)  # never ended
    await asyncio.wait_for(registry.drain_and_revoke(token, wait_s=0.1), 1)
    assert registry.lookup(token) is None


def test_revoke_run_drops_every_grant_and_the_key() -> None:
    registry = _registry()
    tokens = [_grant(registry), _grant(registry, "dev")]
    assert registry.is_open("run_1")
    registry.revoke_run("run_1")
    assert not registry.is_open("run_1")
    assert all(registry.lookup(t) is None for t in tokens)
    with pytest.raises(KeyError):
        registry.api_key("run_1")
