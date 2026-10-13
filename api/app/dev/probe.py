"""Run a single real agent against a bundled sample repo: the M3 end-to-end demo."""

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.agents.artifacts import ARTIFACT_MODELS, render_markdown
from app.agents.runner import AgentOutcome, AgentSpec, isolation_problem
from app.events.models import (
    AgentTextPayload,
    ArtifactPayload,
    Phase,
    PhaseCompletedPayload,
    PhaseStartedPayload,
    Role,
    RunCompletedPayload,
    RunCreatedPayload,
    RunFailedPayload,
    RunStatus,
    RunStatusChangedPayload,
)
from app.store.mongo import NewRun, RunRecord

SAMPLE_REPO = Path(__file__).resolve().parent / "sample_repo"
PHASE_FOR_ROLE = {
    Role.BA: Phase.BA,
    Role.ARCHITECT: Phase.ARCHITECT,
    Role.DEV: Phase.DEV,
    Role.REVIEWER: Phase.REVIEW,
    Role.QA: Phase.QA,
    Role.DEVOPS: Phase.DEVOPS,
}


class ProbeUnavailable(Exception):
    pass


def _copy_repo(source: Path, target: Path) -> None:
    """Copy without source modes, so the API's umask 002 and the setgid `sdlc` group apply."""
    for path in sorted(source.rglob("*")):
        if "__pycache__" in path.parts:
            continue
        destination = target / path.relative_to(source)
        if path.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, destination)


@dataclass(frozen=True)
class ProbeContext:
    run: RunRecord
    spec: AgentSpec
    budget_usd: float


async def prepare_probe(
    state: Any, *, role: Role, requirement: str, budget_usd: float = 0.5
) -> ProbeContext:
    settings = state.settings
    if settings.agent_anthropic_key is None:
        raise ProbeUnavailable("AGENT_ANTHROPIC_KEY is not configured")
    role_config = state.roles[role]
    problem = isolation_problem(role_config, settings)
    if problem is not None:
        raise ProbeUnavailable(problem)
    run = await state.store.create_run(
        NewRun(
            requirement=requirement,
            repo="local/sample_repo",
            budget_usd=budget_usd,
            max_parallel_devs=1,
        )
    )
    run_dir = settings.workspace_root / "runs" / run.id
    workspace = run_dir / "repo"
    workspace.mkdir(parents=True)
    _copy_repo(SAMPLE_REPO, workspace)
    (run_dir / "outside.txt").write_text("This file is outside the agent workspace.\n")
    state.registry.open_run(run.id, settings.agent_anthropic_key, budget_usd)
    spec = AgentSpec(
        run_id=run.id,
        agent_id=role.value,
        role=role_config,
        prompt=(
            f"Requirement: {requirement}\n\nThe repository is your current directory. "
            "Read what you need, then return your artifact."
        ),
        workspace=workspace,
        config_dir=run_dir / "claude" / role.value,
        phase=PHASE_FOR_ROLE[role],
    )
    return ProbeContext(run=run, spec=spec, budget_usd=budget_usd)


async def execute_probe(state: Any, ctx: ProbeContext) -> AgentOutcome:
    bus, run_id, spec = state.bus, ctx.run.id, ctx.spec
    created = RunCreatedPayload(
        requirement=ctx.run.requirement,
        repo=ctx.run.repo,
        base_branch=ctx.run.base_branch,
        budget_usd=ctx.budget_usd,
        max_parallel_devs=1,
    )
    await bus.emit(run_id, "run.created", created)
    await bus.emit(run_id, "run.status_changed", RunStatusChangedPayload(status=RunStatus.RUNNING))
    await bus.emit(run_id, "phase.started", PhaseStartedPayload(attempt=1), phase=spec.phase)
    try:
        outcome: AgentOutcome = await state.runner.run(spec)
        if outcome.ok and outcome.output is not None:
            kind = spec.role.output
            artifact = ARTIFACT_MODELS[kind].model_validate(outcome.output)
            payload = ArtifactPayload(
                artifact_id=f"art_{run_id}_{spec.agent_id}",
                kind=kind,
                version=1,
                title=kind.value.replace("_", " ").title(),
            )
            markdown = AgentTextPayload(text=render_markdown(kind, artifact))
            await bus.emit(
                run_id, "artifact.created", payload, phase=spec.phase, agent_id=spec.agent_id
            )
            await bus.emit(run_id, "agent.text", markdown, phase=spec.phase, agent_id=spec.agent_id)
            completed = PhaseCompletedPayload(attempt=1, outcome="ok")
            await bus.emit(run_id, "phase.completed", completed, phase=spec.phase)
            await bus.emit(
                run_id, "run.completed", RunCompletedPayload(pr_url=None), phase=Phase.DONE
            )
        else:
            reason = "budget" if outcome.status == "budget_exhausted" else outcome.status
            failed = RunFailedPayload(reason=reason, detail=outcome.error)
            await bus.emit(run_id, "run.failed", failed, phase=spec.phase)
        return outcome
    except Exception as exc:
        detail = f"{type(exc).__name__}: {exc}"[:500]
        failed = RunFailedPayload(reason="error", detail=detail)
        await bus.emit(run_id, "run.failed", failed, phase=spec.phase)
        raise
    finally:
        state.registry.revoke_run(run_id)
