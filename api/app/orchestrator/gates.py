"""Human approval gates: the pipeline awaits a decision and the API delivers it.

One gate at most is open per run. A decision is claimed synchronously before anything is
awaited, so concurrent or repeated decisions for the same gate get 409 and `gate.resolved` is
recorded once.
"""

import asyncio
import secrets
from collections.abc import Sequence
from dataclasses import dataclass, field

from app.events.bus import EventBus
from app.events.models import GateAction, GateKind, GateOpenedPayload, GateResolvedPayload, Phase


@dataclass(frozen=True)
class GateDecision:
    action: GateAction
    feedback: str | None = None
    markdown: str | None = None


class GateError(Exception):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


@dataclass
class OpenGate:
    gate_id: str
    kind: GateKind
    actions: tuple[GateAction, ...]
    phase: Phase | None
    future: asyncio.Future[GateDecision] = field(repr=False)
    claimed: bool = False


class GateBroker:
    def __init__(self, bus: EventBus) -> None:
        self._bus = bus
        self._open: dict[str, OpenGate] = {}

    def is_open(self, run_id: str) -> bool:
        return run_id in self._open

    async def wait(
        self,
        run_id: str,
        kind: GateKind,
        *,
        actions: Sequence[GateAction],
        phase: Phase | None,
        artifact_id: str | None = None,
        reason: str | None = None,
    ) -> GateDecision:
        gate = OpenGate(
            gate_id=f"gate_{secrets.token_hex(4)}",
            kind=kind,
            actions=tuple(actions),
            phase=phase,
            future=asyncio.get_running_loop().create_future(),
        )
        self._open[run_id] = gate
        try:
            opened = GateOpenedPayload(
                gate_id=gate.gate_id,
                gate=kind,
                actions=list(actions),
                artifact_id=artifact_id,
                reason=reason,
            )
            await self._bus.emit(run_id, "gate.opened", opened, phase=phase)
            return await gate.future
        finally:
            if self._open.get(run_id) is gate:
                del self._open[run_id]

    def validate(self, run_id: str, gate_id: str, decision: GateDecision) -> OpenGate:
        gate = self._open.get(run_id)
        if gate is None or gate.gate_id != gate_id or gate.claimed or gate.future.done():
            raise GateError(
                409, "this gate is not open; it was already decided or the run moved on"
            )
        if decision.action not in gate.actions:
            raise GateError(422, f"'{decision.action}' is not available at the {gate.kind} gate")
        if decision.action == "revise" and not (decision.feedback or "").strip():
            raise GateError(422, "requesting changes needs feedback")
        if decision.action == "edit" and not (decision.markdown or "").strip():
            raise GateError(422, "editing needs the edited markdown")
        return gate

    async def resolve(self, run_id: str, gate_id: str, decision: GateDecision) -> None:
        gate = self.validate(run_id, gate_id, decision)
        gate.claimed = True  # before awaiting, so a concurrent decision gets 409
        try:
            resolved = GateResolvedPayload(
                gate_id=gate_id, action=decision.action, feedback=decision.feedback
            )
            await self._bus.emit(run_id, "gate.resolved", resolved, phase=gate.phase)
        except BaseException:
            gate.claimed = False
            raise
        if not gate.future.done():
            gate.future.set_result(decision)
