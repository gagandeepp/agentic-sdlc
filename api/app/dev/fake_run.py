"""A scripted SDLC run for developing and testing the GUI without calling any model.

The same script drives the live dev endpoint (through the real EventBus) and the committed
replay fixture used by the web tests.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import NamedTuple

from app.events.bus import EventBus
from app.events.models import (
    EVENT_ADAPTER,
    AgentFinishedPayload,
    AgentStartedPayload,
    AgentTextPayload,
    AgentThinkingPayload,
    AgentToolCallPayload,
    AgentToolResultPayload,
    AgentUsagePayload,
    ArtifactKind,
    ArtifactPayload,
    BudgetUpdatedPayload,
    CiStatusPayload,
    ContractModel,
    GateAction,
    GateKind,
    GateOpenedPayload,
    GateResolvedPayload,
    GitPushedPayload,
    GuardrailBlockedPayload,
    HandoffPayload,
    LoopIterationPayload,
    MergeCompletedPayload,
    MergeStartedPayload,
    Phase,
    PhaseCompletedPayload,
    PhaseStartedPayload,
    PrOpenedPayload,
    PrReadyPayload,
    Role,
    RunCompletedPayload,
    RunCreatedPayload,
    RunEvent,
    RunStatus,
    RunStatusChangedPayload,
)

FAKE_REQUIREMENT = "Add CSV export to the orders page"
FAKE_REPO = "demo/orders-app"
FAKE_BUDGET_USD = 5.0
FAKE_PR_URL = "https://github.com/demo/orders-app/pull/42"
FIXTURE_RUN_ID = "run_demo0001"
FIXTURE_START = datetime(2026, 10, 13, 9, 0, tzinfo=UTC)

SONNET = "claude-sonnet-5-5"
OPUS = "claude-opus-5-5"
HAIKU = "claude-haiku-5-5"
CONTEXT_MAX = 200_000
RUN_BRANCH = "sdlc/run_demo"


class FakeStep(NamedTuple):
    delay: float
    event_type: str
    payload: ContractModel
    phase: Phase | None = None
    agent_id: str | None = None


def _usage(
    model: str, inp: int, out: int, cache_read: int, ctx: int, cost: float
) -> AgentUsagePayload:
    return AgentUsagePayload(
        model=model,
        input_tokens=inp,
        output_tokens=out,
        cache_creation_input_tokens=0,
        cache_read_input_tokens=cache_read,
        context_tokens=ctx,
        context_max=CONTEXT_MAX,
        cost_usd=cost,
    )


def _started(role: Role, model: str, task_id: str | None = None) -> AgentStartedPayload:
    return AgentStartedPayload(role=role, model=model, task_id=task_id)


def _finished(turns: int, ms: int) -> AgentFinishedPayload:
    return AgentFinishedPayload(ok=True, num_turns=turns, duration_ms=ms)


def _call(tool_use_id: str, name: str, **tool_input: str) -> AgentToolCallPayload:
    return AgentToolCallPayload(tool_use_id=tool_use_id, name=name, input=tool_input)


def _result(tool_use_id: str, content: str, *, is_error: bool = False) -> AgentToolResultPayload:
    return AgentToolResultPayload.from_content(tool_use_id, content, is_error=is_error)


def _artifact(
    artifact_id: str, kind: ArtifactKind, title: str, version: int = 1
) -> ArtifactPayload:
    return ArtifactPayload(artifact_id=artifact_id, kind=kind, version=version, title=title)


def _handoff(src: str, dst: str, summary: str, artifact_id: str | None = None) -> HandoffPayload:
    return HandoffPayload(from_agent=src, to_agent=dst, summary=summary, artifact_id=artifact_id)


def _gate(gate_id: str, kind: GateKind, artifact_id: str) -> GateOpenedPayload:
    actions: list[GateAction] = ["approve", "revise", "abort"]
    if kind is not GateKind.RELEASE:
        actions.insert(2, "edit")
    return GateOpenedPayload(
        gate_id=gate_id, gate=kind, actions=actions, artifact_id=artifact_id, reason=None
    )


def _approve(gate_id: str) -> GateResolvedPayload:
    return GateResolvedPayload(gate_id=gate_id, action="approve", feedback=None)


def _phase_ok(attempt: int = 1) -> PhaseCompletedPayload:
    return PhaseCompletedPayload(attempt=attempt, outcome="ok")


def _base_script() -> list[FakeStep]:
    S, P = FakeStep, Phase
    start = PhaseStartedPayload(attempt=1)
    return [
        S(0.0, "run.created", RunCreatedPayload(requirement=FAKE_REQUIREMENT, repo=FAKE_REPO, base_branch="main", budget_usd=FAKE_BUDGET_USD, max_parallel_devs=2)),
        S(0.2, "run.status_changed", RunStatusChangedPayload(status=RunStatus.RUNNING)),
        # --- BA ---
        S(0.2, "phase.started", start, P.BA),
        S(0.1, "agent.started", _started(Role.BA, SONNET), P.BA, "ba"),
        S(0.5, "agent.tool_call", _call("tu_ba_1", "Grep", pattern="orders", path="src"), P.BA, "ba"),
        S(0.3, "agent.tool_result", _result("tu_ba_1", "src/orders/page.tsx\nsrc/orders/api.py"), P.BA, "ba"),
        S(0.8, "agent.text", AgentTextPayload(text="Drafted 2 user stories: export filtered orders as CSV; include a totals row."), P.BA, "ba"),
        S(0.1, "agent.usage", _usage(SONNET, 8_200, 950, 0, 9_150, 0.0389), P.BA, "ba"),
        S(0.1, "agent.finished", _finished(3, 1_900), P.BA, "ba"),
        S(0.1, "artifact.created", _artifact("art_req", ArtifactKind.REQUIREMENTS, "Requirements"), P.BA, "ba"),
        S(0.1, "phase.completed", _phase_ok(), P.BA),
        S(0.2, "gate.opened", _gate("gate_req", GateKind.REQUIREMENTS, "art_req"), P.BA),
        S(1.5, "gate.resolved", _approve("gate_req"), P.BA),
        S(0.1, "handoff", _handoff("ba", "architect", "2 stories approved", "art_req"), P.BA, "ba"),
        # --- Architect ---
        S(0.2, "phase.started", start, P.ARCHITECT),
        S(0.1, "agent.started", _started(Role.ARCHITECT, OPUS), P.ARCHITECT, "architect"),
        S(0.6, "agent.thinking", AgentThinkingPayload(text="CSV generation belongs in the API; the page only needs a download button."), P.ARCHITECT, "architect"),
        S(0.4, "agent.tool_call", _call("tu_ar_1", "Read", file_path="src/orders/api.py"), P.ARCHITECT, "architect"),
        S(0.3, "agent.tool_result", _result("tu_ar_1", "def list_orders(filters): ..."), P.ARCHITECT, "architect"),
        S(0.9, "agent.text", AgentTextPayload(text="2 independent tasks: T1 CSV endpoint (api.py), T2 download button (page.tsx)."), P.ARCHITECT, "architect"),
        S(0.1, "agent.usage", _usage(OPUS, 14_300, 2_100, 6_000, 22_400, 0.2465), P.ARCHITECT, "architect"),
        S(0.1, "agent.finished", _finished(4, 2_600), P.ARCHITECT, "architect"),
        S(0.1, "artifact.created", _artifact("art_design", ArtifactKind.DESIGN, "Design"), P.ARCHITECT, "architect"),
        S(0.1, "phase.completed", _phase_ok(), P.ARCHITECT),
        S(0.2, "gate.opened", _gate("gate_design", GateKind.DESIGN, "art_design"), P.ARCHITECT),
        S(1.5, "gate.resolved", _approve("gate_design"), P.ARCHITECT),
        S(0.1, "handoff", _handoff("architect", "dev-1", "T1: CSV endpoint in src/orders/api.py", "art_design"), P.ARCHITECT, "architect"),
        S(0.1, "handoff", _handoff("architect", "dev-2", "T2: download button in src/orders/page.tsx", "art_design"), P.ARCHITECT, "architect"),
        # --- Dev (two in parallel) ---
        S(0.2, "phase.started", start, P.DEV),
        S(0.1, "agent.started", _started(Role.DEV, SONNET, "T1"), P.DEV, "dev-1"),
        S(0.1, "agent.started", _started(Role.DEV, SONNET, "T2"), P.DEV, "dev-2"),
        S(0.5, "agent.tool_call", _call("tu_d1_1", "Edit", file_path="src/orders/api.py"), P.DEV, "dev-1"),
        S(0.3, "agent.tool_call", _call("tu_d2_1", "Edit", file_path="src/orders/page.tsx"), P.DEV, "dev-2"),
        S(0.3, "agent.tool_result", _result("tu_d1_1", "Edited src/orders/api.py (+38 -2)"), P.DEV, "dev-1"),
        S(0.2, "agent.tool_result", _result("tu_d2_1", "Edited src/orders/page.tsx (+21 -0)"), P.DEV, "dev-2"),
        S(0.4, "agent.tool_call", _call("tu_d2_2", "Bash", command="git push origin HEAD"), P.DEV, "dev-2"),
        S(0.1, "guardrail.blocked", GuardrailBlockedPayload(rule="no-git-push", tool="Bash", detail="git push origin HEAD"), P.DEV, "dev-2"),
        S(0.1, "agent.tool_result", _result("tu_d2_2", "Blocked by policy: agents cannot push; the orchestrator pushes.", is_error=True), P.DEV, "dev-2"),
        S(0.5, "agent.tool_call", _call("tu_d1_2", "Bash", command="uv run pytest tests/orders -q"), P.DEV, "dev-1"),
        S(1.0, "agent.tool_result", _result("tu_d1_2", "tests/orders/test_csv_export.py ....\n4 passed in 0.82s"), P.DEV, "dev-1"),
        S(0.1, "agent.usage", _usage(SONNET, 21_500, 3_400, 12_000, 37_100, 0.1191), P.DEV, "dev-1"),
        S(0.1, "agent.finished", _finished(6, 4_800), P.DEV, "dev-1"),
        S(0.1, "agent.usage", _usage(SONNET, 15_200, 2_200, 8_000, 25_600, 0.0819), P.DEV, "dev-2"),
        S(0.1, "agent.finished", _finished(5, 4_100), P.DEV, "dev-2"),
        S(0.1, "phase.completed", _phase_ok(), P.DEV),
        # --- Integrate ---
        S(0.2, "phase.started", start, P.INTEGRATE),
        S(0.2, "merge.started", MergeStartedPayload(branch=f"{RUN_BRANCH}/task-T1", into=RUN_BRANCH), P.INTEGRATE),
        S(0.3, "merge.completed", MergeCompletedPayload(branch=f"{RUN_BRANCH}/task-T1", into=RUN_BRANCH, commit="a1b2c3d4e5f6"), P.INTEGRATE),
        S(0.2, "merge.started", MergeStartedPayload(branch=f"{RUN_BRANCH}/task-T2", into=RUN_BRANCH), P.INTEGRATE),
        S(0.3, "merge.completed", MergeCompletedPayload(branch=f"{RUN_BRANCH}/task-T2", into=RUN_BRANCH, commit="b2c3d4e5f6a7"), P.INTEGRATE),
        S(0.1, "phase.completed", _phase_ok(), P.INTEGRATE),
        S(0.1, "handoff", _handoff("integrator", "reviewer", "T1 and T2 merged cleanly"), P.INTEGRATE),
        # --- Review (one revise loop) ---
        S(0.2, "phase.started", start, P.REVIEW),
        S(0.1, "agent.started", _started(Role.REVIEWER, OPUS), P.REVIEW, "reviewer"),
        S(1.0, "agent.text", AgentTextPayload(text="1 change requested: an empty order list should yield a header-only CSV."), P.REVIEW, "reviewer"),
        S(0.1, "agent.usage", _usage(OPUS, 18_900, 1_400, 9_000, 29_300, 0.2155), P.REVIEW, "reviewer"),
        S(0.1, "agent.finished", _finished(3, 2_200), P.REVIEW, "reviewer"),
        S(0.1, "artifact.created", _artifact("art_review", ArtifactKind.REVIEW, "Review"), P.REVIEW, "reviewer"),
        S(0.1, "loop.iteration", LoopIterationPayload(loop="review", iteration=1, limit=3), P.REVIEW),
        S(0.1, "handoff", _handoff("reviewer", "dev-1", "Handle an empty order list", "art_review"), P.REVIEW, "reviewer"),
        S(0.1, "agent.started", _started(Role.DEV, SONNET, "T1"), P.REVIEW, "dev-1"),
        S(0.5, "agent.tool_call", _call("tu_d1_3", "Edit", file_path="src/orders/api.py"), P.REVIEW, "dev-1"),
        S(0.3, "agent.tool_result", _result("tu_d1_3", "Edited src/orders/api.py (+4 -1)"), P.REVIEW, "dev-1"),
        S(0.1, "agent.usage", _usage(SONNET, 9_800, 700, 30_000, 40_500, 0.0489), P.REVIEW, "dev-1"),
        S(0.1, "agent.finished", _finished(2, 1_100), P.REVIEW, "dev-1"),
        S(0.1, "handoff", _handoff("dev-1", "reviewer", "Empty list now yields a header-only CSV"), P.REVIEW, "dev-1"),
        S(0.1, "agent.started", _started(Role.REVIEWER, OPUS), P.REVIEW, "reviewer"),
        S(0.8, "agent.text", AgentTextPayload(text="Approved."), P.REVIEW, "reviewer"),
        S(0.1, "agent.usage", _usage(OPUS, 6_200, 300, 21_000, 27_500, 0.0490), P.REVIEW, "reviewer"),
        S(0.1, "agent.finished", _finished(1, 900), P.REVIEW, "reviewer"),
        S(0.1, "artifact.revised", _artifact("art_review", ArtifactKind.REVIEW, "Review", version=2), P.REVIEW, "reviewer"),
        S(0.1, "phase.completed", _phase_ok(attempt=2), P.REVIEW),
        S(0.1, "handoff", _handoff("reviewer", "qa", "Approved after 1 revision", "art_review"), P.REVIEW, "reviewer"),
        # --- QA ---
        S(0.2, "phase.started", start, P.QA),
        S(0.1, "agent.started", _started(Role.QA, SONNET), P.QA, "qa"),
        S(0.5, "agent.tool_call", _call("tu_qa_1", "Bash", command="uv run pytest -q"), P.QA, "qa"),
        S(1.2, "agent.tool_result", _result("tu_qa_1", "14 passed in 2.31s"), P.QA, "qa"),
        S(0.1, "agent.usage", _usage(SONNET, 12_400, 1_800, 5_000, 19_200, 0.0642), P.QA, "qa"),
        S(0.1, "agent.finished", _finished(4, 3_000), P.QA, "qa"),
        S(0.1, "artifact.created", _artifact("art_tests", ArtifactKind.TEST_REPORT, "Test report"), P.QA, "qa"),
        S(0.1, "phase.completed", _phase_ok(), P.QA),
        S(0.1, "git.pushed", GitPushedPayload(branch=RUN_BRANCH, commit="c3d4e5f6a7b8"), P.QA),
        S(0.1, "handoff", _handoff("qa", "devops", "14 tests passing", "art_tests"), P.QA, "qa"),
        # --- DevOps ---
        S(0.2, "phase.started", start, P.DEVOPS),
        S(0.1, "agent.started", _started(Role.DEVOPS, HAIKU), P.DEVOPS, "devops"),
        S(0.6, "agent.text", AgentTextPayload(text="PR description and changelog prepared."), P.DEVOPS, "devops"),
        S(0.1, "agent.usage", _usage(HAIKU, 7_600, 900, 0, 8_500, 0.0121), P.DEVOPS, "devops"),
        S(0.1, "agent.finished", _finished(2, 1_200), P.DEVOPS, "devops"),
        S(0.1, "artifact.created", _artifact("art_release", ArtifactKind.RELEASE_PLAN, "Release plan"), P.DEVOPS, "devops"),
        S(0.2, "pr.opened", PrOpenedPayload(url=FAKE_PR_URL, number=42, draft=True), P.DEVOPS),
        S(0.8, "ci.status", CiStatusPayload(state="success", summary="3/3 checks passed"), P.DEVOPS),
        S(0.1, "phase.completed", _phase_ok(), P.DEVOPS),
        S(0.2, "gate.opened", _gate("gate_release", GateKind.RELEASE, "art_release"), P.DEVOPS),
        S(1.5, "gate.resolved", _approve("gate_release"), P.DEVOPS),
        S(0.2, "pr.ready", PrReadyPayload(url=FAKE_PR_URL, number=42), P.DEVOPS),
        S(0.1, "run.completed", RunCompletedPayload(pr_url=FAKE_PR_URL), P.DONE),
    ]  # fmt: skip


def fake_script() -> list[FakeStep]:
    """The base script plus a cumulative budget.updated after every usage event."""
    steps: list[FakeStep] = []
    spent = 0.0
    for step in _base_script():
        steps.append(step)
        if isinstance(step.payload, AgentUsagePayload):
            spent = round(spent + step.payload.cost_usd, 6)
            budget = BudgetUpdatedPayload(spent_usd=spent, budget_usd=FAKE_BUDGET_USD)
            steps.append(FakeStep(0.0, "budget.updated", budget, step.phase))
    return steps


def build_fake_events(
    run_id: str = FIXTURE_RUN_ID, start: datetime = FIXTURE_START
) -> list[RunEvent]:
    events: list[RunEvent] = []
    elapsed = 0.0
    for seq, step in enumerate(fake_script(), start=1):
        elapsed = round(elapsed + step.delay, 3)
        events.append(
            EVENT_ADAPTER.validate_python(
                {
                    "run_id": run_id,
                    "seq": seq,
                    "ts": start + timedelta(seconds=elapsed),
                    "type": step.event_type,
                    "phase": step.phase,
                    "agent_id": step.agent_id,
                    "payload": step.payload.model_dump(),
                }
            )
        )
    return events


async def play_fake_run(bus: EventBus, run_id: str, *, speed: float = 1.0) -> None:
    for step in fake_script():
        await asyncio.sleep(step.delay / speed)
        await bus.emit(
            run_id, step.event_type, step.payload, phase=step.phase, agent_id=step.agent_id
        )
