"""Who may use the credential proxy, with which key, and how much budget is left.

Everything here is in-memory and synchronous except draining, so budget decisions can't be
interleaved by other coroutines.
"""

import asyncio
import logging
import secrets
from collections.abc import Coroutine, Iterable
from dataclasses import dataclass
from typing import Any

from pydantic import SecretStr

from app.events.models import Phase
from app.llm.pricing import PricingTable

log = logging.getLogger(__name__)

BUDGET_EXHAUSTED_MESSAGE = "agentic-sdlc run budget exhausted"


@dataclass
class RunBudget:
    budget_usd: float
    spent_usd: float = 0.0
    reserved_usd: float = 0.0
    exhausted: bool = False

    @property
    def remaining_usd(self) -> float:
        return max(0.0, self.budget_usd - self.spent_usd - self.reserved_usd)

    def try_reserve(self, amount: float) -> bool:
        if self.exhausted or self.spent_usd + self.reserved_usd + amount > self.budget_usd:
            self.exhausted = True
            return False
        self.reserved_usd += amount
        return True

    def release(self, reserved: float) -> None:
        self.reserved_usd = max(0.0, self.reserved_usd - reserved)

    def settle(self, reserved: float, actual: float) -> None:
        self.release(reserved)
        self.spent_usd += actual
        if self.spent_usd >= self.budget_usd:
            self.exhausted = True


@dataclass(frozen=True)
class Grant:
    token: str
    run_id: str
    agent_id: str
    phase: Phase | None
    allowed_models: frozenset[str]


class ProxyRegistry:
    def __init__(self, pricing: PricingTable) -> None:
        self.pricing = pricing
        self._grants: dict[str, Grant] = {}
        self._keys: dict[str, SecretStr] = {}
        self._budgets: dict[str, RunBudget] = {}
        self._inflight: dict[str, int] = {}
        self._idle: dict[str, asyncio.Event] = {}
        self._tasks: set[asyncio.Task[None]] = set()

    def open_run(self, run_id: str, api_key: SecretStr, budget_usd: float) -> RunBudget:
        self._keys[run_id] = api_key
        return self._budgets.setdefault(run_id, RunBudget(budget_usd=budget_usd))

    def budget(self, run_id: str) -> RunBudget:
        return self._budgets[run_id]

    def is_open(self, run_id: str) -> bool:
        return run_id in self._budgets

    def api_key(self, run_id: str) -> SecretStr:
        return self._keys[run_id]

    def grant(
        self, *, run_id: str, agent_id: str, phase: Phase | None, allowed_models: Iterable[str]
    ) -> Grant:
        if run_id not in self._budgets:
            raise KeyError(f"run {run_id} is not open on the proxy")
        token = f"agt_{secrets.token_urlsafe(24)}"
        grant = Grant(token, run_id, agent_id, phase, frozenset(allowed_models))
        self._grants[token] = grant
        self._inflight[token] = 0
        idle = asyncio.Event()
        idle.set()
        self._idle[token] = idle
        return grant

    def lookup(self, token: str | None) -> Grant | None:
        return self._grants.get(token) if token else None

    def begin(self, token: str) -> None:
        self._inflight[token] = self._inflight.get(token, 0) + 1
        if token in self._idle:
            self._idle[token].clear()

    def end(self, token: str) -> None:
        remaining = max(0, self._inflight.get(token, 1) - 1)
        self._inflight[token] = remaining
        if remaining == 0 and token in self._idle:
            self._idle[token].set()

    def spawn(self, token: str, work: Coroutine[Any, Any, None]) -> None:
        """Run accounting outside the request (a cancelled response can't await); ends `token`."""
        task = asyncio.create_task(work)
        self._tasks.add(task)

        def done(finished: asyncio.Task[None]) -> None:
            self._tasks.discard(finished)
            self.end(token)
            if not finished.cancelled() and finished.exception() is not None:
                log.error("proxy accounting failed", exc_info=finished.exception())

        task.add_done_callback(done)

    async def drain_and_revoke(self, token: str, wait_s: float = 10.0) -> None:
        idle = self._idle.get(token)
        if idle is not None:
            try:
                await asyncio.wait_for(idle.wait(), wait_s)
            except TimeoutError:
                log.warning("proxy requests for an agent did not finish within %ss", wait_s)
        self._grants.pop(token, None)
        self._inflight.pop(token, None)
        self._idle.pop(token, None)

    def revoke_run(self, run_id: str) -> None:
        for token in [t for t, g in self._grants.items() if g.run_id == run_id]:
            self._grants.pop(token, None)
            self._inflight.pop(token, None)
            self._idle.pop(token, None)
        self._keys.pop(run_id, None)
        self._budgets.pop(run_id, None)

    async def aclose(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
