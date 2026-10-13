"""NeMo input rails on text people give the agents, run as the metered `guardrails` agent."""

import time
from dataclasses import dataclass
from typing import Literal, Protocol

import httpx

from app.agents.runner import HAIKU_MODEL
from app.events.bus import EventBus
from app.events.models import (
    AgentFinishedPayload,
    AgentStartedPayload,
    ContractModel,
    GuardrailBlockedPayload,
    Phase,
    Role,
)
from app.guardrails.llm import ProxyLLM
from app.guardrails.service import UNAVAILABLE, GuardrailsService
from app.llm.registry import ProxyRegistry

GUARDRAILS_AGENT = "guardrails"
InputSource = Literal["requirement", "gate-feedback", "artifact-edit"]


@dataclass(frozen=True)
class InputCheck:
    allowed: bool
    rail: str | None = None
    unavailable: bool = False


class InputGuard(Protocol):
    async def check(
        self, run_id: str, text: str, *, source: InputSource, phase: Phase | None
    ) -> InputCheck: ...


class ProxyInputGuard:
    def __init__(
        self,
        *,
        guardrails: GuardrailsService,
        registry: ProxyRegistry,
        bus: EventBus,
        http: httpx.AsyncClient,
        proxy_base_url: str,
    ) -> None:
        self._guardrails = guardrails
        self._registry = registry
        self._bus = bus
        self._http = http
        self._proxy_base_url = proxy_base_url

    async def check(
        self, run_id: str, text: str, *, source: InputSource, phase: Phase | None
    ) -> InputCheck:
        async def emit(event_type: str, payload: ContractModel) -> None:
            await self._bus.emit(
                run_id, event_type, payload, phase=phase, agent_id=GUARDRAILS_AGENT
            )

        grant = self._registry.grant(
            run_id=run_id, agent_id=GUARDRAILS_AGENT, phase=phase, allowed_models={HAIKU_MODEL}
        )
        await emit("agent.started", AgentStartedPayload(role=Role.GUARDRAILS, model=HAIKU_MODEL))
        started = time.monotonic()
        try:
            llm = ProxyLLM(self._http, self._proxy_base_url, grant.token)
            verdict = await self._guardrails.check_input(f"[{source}]\n{text}", llm=llm)
        finally:
            # Usage for this check is emitted before agent.finished.
            await self._registry.drain_and_revoke(grant.token)
        unavailable = verdict.reason == UNAVAILABLE
        duration_ms = int((time.monotonic() - started) * 1000)
        await emit(
            "agent.finished",
            AgentFinishedPayload(ok=not unavailable, num_turns=1, duration_ms=duration_ms),
        )
        if verdict.allowed:
            return InputCheck(allowed=True)
        if unavailable:
            return InputCheck(allowed=False, unavailable=True)
        rail = verdict.rules[0] if verdict.rules else "input rails"
        await emit(
            "guardrail.blocked", GuardrailBlockedPayload(rule=rail, tool=source, detail=text[:300])
        )
        return InputCheck(allowed=False, rail=rail)
