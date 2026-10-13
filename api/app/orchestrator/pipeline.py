"""One run of the SDLC state machine.

INIT (input rails, clone) → BA → [requirements gate] → Architect → [design gate] → Dev →
Review ⇄ Dev (≤3 fixes) → QA ⇄ Dev (≤2 fixes) → push → DevOps (publish rails, docs, draft PR)
→ CI → [release gate] → PR ready for review.

Every durable point writes a checkpoint before the run moves on. A resumed run starts at its
checkpoint: `*_drafted` re-opens that gate without re-running the agent; checkpoints before
`pushed` re-clone the base commit (unpushed agent work is lost); later ones clone the run
branch. Gate and CI waits don't count against the run's time limit.
"""

import asyncio
import logging
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from pydantic import SecretStr

from app.agents.artifacts import ARTIFACT_MODELS, ReleasePlan, render_markdown
from app.agents.roles import RoleConfig
from app.agents.runner import AgentRunner, AgentSpec
from app.events.bus import EventBus
from app.events.models import (
    ArtifactKind,
    Checkpoint,
    CiStatusPayload,
    ContractModel,
    GateAction,
    GateKind,
    GitPushedPayload,
    GuardrailBlockedPayload,
    HandoffPayload,
    LoopIterationPayload,
    Phase,
    PhaseCompletedPayload,
    PhaseStartedPayload,
    PrOpenedPayload,
    PrReadyPayload,
    Role,
    RunAbortedPayload,
    RunCompletedPayload,
    RunFailedPayload,
    RunInterruptedPayload,
    RunStatus,
    RunStatusChangedPayload,
)
from app.gitops.github import CiStatus, GitHubClient, GitHubError, PullRequest
from app.gitops.repo import GitIdentity, PushRefused, RunRepo
from app.guardrails.service import GuardrailsService, GuardrailsUnavailable
from app.llm.registry import ProxyRegistry
from app.orchestrator import prompts
from app.orchestrator.clock import ActiveClock
from app.orchestrator.gates import GateBroker, GateDecision
from app.orchestrator.input_guard import InputGuard
from app.settings import Settings
from app.store.artifacts import ArtifactRecord, ArtifactStore
from app.store.mongo import MongoStore, RunRecord

log = logging.getLogger(__name__)

REVIEW_FIX_LIMIT = 3
QA_FIX_LIMIT = 2
APPROVAL_ACTIONS: tuple[GateAction, ...] = ("approve", "revise", "edit", "abort")
RELEASE_ACTIONS: tuple[GateAction, ...] = ("approve", "revise", "abort")
ESCALATION_ACTIONS: tuple[GateAction, ...] = ("approve", "revise", "abort")
CHECKPOINT_ORDER: tuple[Checkpoint, ...] = tuple(Checkpoint)
PUBLISHED_DOCS = {
    ArtifactKind.REQUIREMENTS: "requirements.md",
    ArtifactKind.DESIGN: "design.md",
    ArtifactKind.REVIEW: "review.md",
    ArtifactKind.TEST_REPORT: "test-report.md",
}
PROJECT_URL = "https://github.com/gagandeepp/agentic-sdlc"


@dataclass(frozen=True)
class RunTarget:
    repo: str
    clone_url: str
    base_branch: str


@dataclass
class RunControl:
    """Why the manager cancelled the run's task, so the pipeline records the right outcome."""

    stop_reason: Literal["abort", "shutdown"] | None = None


@dataclass(frozen=True)
class PipelineDeps:
    bus: EventBus
    store: MongoStore
    artifacts: ArtifactStore
    gates: GateBroker
    runner: AgentRunner
    roles: Mapping[Role, RoleConfig]
    guard: InputGuard
    guardrails: GuardrailsService
    github: GitHubClient
    registry: ProxyRegistry
    settings: Settings
    github_token: SecretStr | None = None


@dataclass
class RunState:
    """What a resumed run needs; saved with every checkpoint."""

    base_sha: str | None = None
    pushed_sha: str | None = None
    requirements_id: str | None = None
    design_id: str | None = None
    release_plan_id: str | None = None
    pr_number: int | None = None
    pr_url: str | None = None
    pr_node_id: str | None = None
    attempts: dict[str, int] = field(default_factory=dict)
    active_s: float = 0.0

    @classmethod
    def from_data(cls, data: Mapping[str, Any]) -> "RunState":
        return cls(**{name: data[name] for name in cls.__dataclass_fields__ if name in data})


@dataclass(frozen=True)
class Stage:
    """A phase whose artifact a person approves before the run moves on."""

    role: Role
    phase: Phase
    gate: GateKind
    drafted: Checkpoint
    approved: Checkpoint
    field: Literal["requirements_id", "design_id"]
    summary: str


REQUIREMENTS_STAGE = Stage(
    Role.BA, Phase.BA, GateKind.REQUIREMENTS,
    Checkpoint.REQUIREMENTS_DRAFTED, Checkpoint.REQUIREMENTS_APPROVED,
    "requirements_id", "write the requirements",
)  # fmt: skip
DESIGN_STAGE = Stage(
    Role.ARCHITECT, Phase.ARCHITECT, GateKind.DESIGN,
    Checkpoint.DESIGN_DRAFTED, Checkpoint.DESIGN_APPROVED,
    "design_id", "design the approved requirements",
)  # fmt: skip


class RunStop(Exception):
    """Ends the run with a terminal event."""

    def __init__(self, event_type: str, payload: ContractModel, phase: Phase | None = None) -> None:
        super().__init__(event_type)
        self.event_type = event_type
        self.payload = payload
        self.phase = phase


class Pipeline:
    def __init__(
        self, deps: PipelineDeps, run: RunRecord, target: RunTarget, control: RunControl
    ) -> None:
        self._d = deps
        self._run = run
        self._target = target
        self._control = control
        self._state = RunState.from_data(run.checkpoint_data)
        self._checkpoint = run.checkpoint
        self._clock = ActiveClock(deps.settings.run_timeout_s, used_s=self._state.active_s)
        self._branch = f"sdlc/{run.id}"
        self._run_dir = deps.settings.workspace_root / "runs" / run.id
        self._repo_ref: RunRepo | None = None
        self._summary = ""
        self._phase: Phase | None = None
        self._last_agent = "orchestrator"

    @property
    def run_id(self) -> str:
        return self._run.id

    @property
    def _repo(self) -> RunRepo:
        assert self._repo_ref is not None, "the repository is cloned before any agent runs"
        return self._repo_ref

    @property
    def _base_sha(self) -> str:
        assert self._state.base_sha is not None, "the base commit is recorded at `cloned`"
        return self._state.base_sha

    # --- lifecycle --------------------------------------------------------------------------

    async def execute(self) -> None:
        """Run to a terminal event. Cancellation records `run.aborted` or `run.interrupted`."""
        try:
            running = RunStatusChangedPayload(status=RunStatus.RUNNING)
            await self._emit("run.status_changed", running)
            async with self._clock.running():
                pr_url = await self._steps()
            await self._emit("run.completed", RunCompletedPayload(pr_url=pr_url), phase=Phase.DONE)
        except RunStop as stop:
            await self._emit(stop.event_type, stop.payload, phase=stop.phase or self._phase)
        except TimeoutError:
            minutes = self._d.settings.run_timeout_s / 60
            await self._fail(
                "timeout", f"the run used its {minutes:g}-minute limit (gate and CI waits excluded)"
            )
        except asyncio.CancelledError:
            if self._control.stop_reason == "abort":
                aborted = RunAbortedPayload(by="user", reason="aborted while running")
                await self._emit("run.aborted", aborted, phase=self._phase)
            else:
                interrupted = RunInterruptedPayload(last_checkpoint=self._checkpoint)
                await self._emit("run.interrupted", interrupted, phase=self._phase)
            raise
        except Exception as exc:
            log.exception("run %s failed", self.run_id)
            await self._fail("error", f"{type(exc).__name__}: {exc}")
        finally:
            self._d.registry.revoke_run(self.run_id)

    async def _steps(self) -> str:
        if self._checkpoint is None:
            await self._init()
        else:
            await self._restore()
        requirements = await self._approved(
            REQUIREMENTS_STAGE,
            lambda rev: prompts.ba_prompt(self._run.requirement, self._summary, revision=rev),
            source=None,
        )
        design = await self._approved(
            DESIGN_STAGE,
            lambda rev: prompts.architect_prompt(
                requirements.markdown, self._summary, revision=rev
            ),
            source=requirements,
        )
        if self._before(Checkpoint.PUSHED):
            await self._build(requirements, design, fix=None)
        return await self._release(requirements, design)

    def _before(self, checkpoint: Checkpoint) -> bool:
        if self._checkpoint is None:
            return True
        return CHECKPOINT_ORDER.index(self._checkpoint) < CHECKPOINT_ORDER.index(checkpoint)

    # --- init and repository ------------------------------------------------------------------

    async def _init(self) -> None:
        self._phase = Phase.INIT
        attempt = self._next_attempt(Phase.INIT)
        await self._emit("phase.started", PhaseStartedPayload(attempt=attempt), phase=Phase.INIT)
        check = await self._d.guard.check(
            self.run_id, self._run.requirement, source="requirement", phase=Phase.INIT
        )
        if not check.allowed:
            self._stop_if_budget_spent()
            if check.unavailable:
                detail = "the input rails could not check the requirement"
                raise RunStop(
                    "run.failed", RunFailedPayload(reason="guardrails-unavailable", detail=detail)
                )
            detail = f"the requirement was blocked by the input rails ({check.rail})"
            raise RunStop("run.failed", RunFailedPayload(reason="guardrails", detail=detail))
        await self._clone_base(None)
        await self._save(Checkpoint.CLONED)
        completed = PhaseCompletedPayload(attempt=attempt, outcome="ok")
        await self._emit("phase.completed", completed, phase=Phase.INIT)

    async def _clone(self, branch: str) -> RunRepo:
        settings = self._d.settings
        self._repo_ref = await RunRepo.clone(
            self._target.clone_url,
            branch,
            worktree=self._run_dir / "repo",
            git_dir=settings.workspace_root / "git" / self.run_id,
            identity=GitIdentity(settings.git_author_name, settings.git_author_email),
            token=self._d.github_token,
            agent_user=settings.agent_user,
        )
        self._summary = self._repo_ref.summary()
        return self._repo_ref

    async def _clone_base(self, base_sha: str | None) -> None:
        repo = await self._clone(self._target.base_branch)
        base = base_sha or await repo.head()
        await repo.fetch_commit(base)
        await repo.start_branch(self._branch, base)
        self._state.base_sha = base

    async def _restore(self) -> None:
        """Rebuild the working tree of a resumed run (agents' unpushed work is gone)."""
        if self._before(Checkpoint.PUSHED):
            await self._clone_base(self._state.base_sha)
            return
        repo = await self._clone(self._branch)
        self._state.pushed_sha = await repo.head()
        await repo.fetch_commit(self._base_sha)

    # --- gated stages ---------------------------------------------------------------------------

    async def _approved(
        self,
        stage: Stage,
        prompt: Callable[[prompts.Revision | None], str],
        *,
        source: ArtifactRecord | None,
    ) -> ArtifactRecord:
        """Run the stage's agent until a person approves (or edits) its artifact."""
        if not self._before(stage.approved):
            return await self._artifact(getattr(self._state, stage.field))
        draft: ArtifactRecord | None = None
        if self._checkpoint is stage.drafted:
            draft = await self._artifact(getattr(self._state, stage.field))
        revision: prompts.Revision | None = None
        revisions = 0
        while True:
            if draft is None:
                draft = await self._produce(
                    stage.role, stage.phase, prompt(revision),
                    summary=stage.summary, artifact_id=source.id if source else None,
                )  # fmt: skip
                setattr(self._state, stage.field, draft.id)
                await self._save(stage.drafted)
            decision = await self._gate(
                stage.gate, stage.phase, APPROVAL_ACTIONS, artifact_id=draft.id,
                reason=f"Approve the {stage.gate.value} to continue, request changes, or edit them.",
            )  # fmt: skip
            if decision.action == "approve":
                break
            if decision.action == "edit":
                draft = await self._d.artifacts.save(
                    self.run_id, draft.kind, draft.data, decision.markdown or "",
                    phase=stage.phase, agent_id=None, edited_by_user=True,
                )  # fmt: skip
                setattr(self._state, stage.field, draft.id)
                break
            revisions += 1
            loop = LoopIterationPayload(loop="revise", iteration=revisions, limit=None)
            await self._emit("loop.iteration", loop, phase=stage.phase)
            revision = prompts.Revision(draft.markdown, decision.feedback or "")
            draft = None
        await self._save(stage.approved)
        return draft

    async def _gate(
        self,
        kind: GateKind,
        phase: Phase,
        actions: tuple[GateAction, ...],
        *,
        artifact_id: str | None = None,
        reason: str | None = None,
    ) -> GateDecision:
        self._phase = phase
        async with self._clock.paused():
            decision = await self._d.gates.wait(
                self.run_id, kind, actions=actions, phase=phase,
                artifact_id=artifact_id, reason=reason,
            )  # fmt: skip
        if decision.action == "abort":
            aborted = RunAbortedPayload(by="user", reason=f"aborted at the {kind.value} gate")
            raise RunStop("run.aborted", aborted, phase)
        return decision

    # --- agents ---------------------------------------------------------------------------------

    async def _produce(
        self, role: Role, phase: Phase, prompt: str, *, summary: str, artifact_id: str | None
    ) -> ArtifactRecord:
        output = await self._agent(role, phase, prompt, summary=summary, artifact_id=artifact_id)
        kind = self._d.roles[role].output
        artifact = ARTIFACT_MODELS[kind].model_validate(output)
        return await self._d.artifacts.save(
            self.run_id, kind, artifact.model_dump(mode="json"), render_markdown(kind, artifact),
            phase=phase, agent_id=role.value,
        )  # fmt: skip

    async def _agent(
        self, role: Role, phase: Phase, prompt: str, *, summary: str, artifact_id: str | None
    ) -> dict[str, Any]:
        """One agent session. A person decides what happens after a failure."""
        self._phase = phase
        guidance: str | None = None
        while True:
            attempt = self._next_attempt(phase)
            await self._emit("phase.started", PhaseStartedPayload(attempt=attempt), phase=phase)
            handoff = HandoffPayload(
                from_agent=self._last_agent, to_agent=role.value, summary=summary,
                artifact_id=artifact_id,
            )  # fmt: skip
            await self._emit("handoff", handoff, phase=phase)
            self._last_agent = role.value
            text = (
                prompt
                if guidance is None
                else (f"{prompt}\n## Guidance after a failed attempt\n\n{guidance}\n")
            )
            spec = AgentSpec(
                run_id=self.run_id, agent_id=role.value, role=self._d.roles[role], prompt=text,
                workspace=self._repo.worktree, config_dir=self._run_dir / "claude" / role.value,
                phase=phase,
            )  # fmt: skip
            outcome = await self._d.runner.run(spec)
            if outcome.ok and outcome.output is not None:
                completed = PhaseCompletedPayload(attempt=attempt, outcome="ok")
                await self._emit("phase.completed", completed, phase=phase)
                return outcome.output
            if outcome.status == "budget_exhausted":
                raise self._budget_stop(outcome.error)
            self._stop_if_budget_spent()
            escalated = PhaseCompletedPayload(attempt=attempt, outcome="escalated")
            await self._emit("phase.completed", escalated, phase=phase)
            decision = await self._gate(
                GateKind.ESCALATION, phase, ESCALATION_ACTIONS,
                reason=(
                    f"The {role.value} agent failed ({outcome.status}): "
                    f"{outcome.error or 'no detail'}. Approve retries it; "
                    "Request changes retries it with your guidance."
                ),
            )  # fmt: skip
            guidance = decision.feedback if decision.action == "revise" else None

    # --- build: dev, review, QA, push ----------------------------------------------------------

    async def _build(
        self, requirements: ArtifactRecord, design: ArtifactRecord, *, fix: prompts.Fix | None
    ) -> None:
        await self._develop(requirements, design, fix)
        await self._review_loop(requirements, design)
        await self._qa_loop(requirements, design)
        await self._push()

    async def _develop(
        self, requirements: ArtifactRecord, design: ArtifactRecord, fix: prompts.Fix | None
    ) -> None:
        while True:
            prompt = prompts.dev_prompt(requirements.markdown, design.markdown, fix=fix)
            summary = (
                "implement the approved design" if fix is None else f"address {fix.source} feedback"
            )
            await self._produce(Role.DEV, Phase.DEV, prompt, summary=summary, artifact_id=design.id)
            subject = (
                f"feat: {self._title()}" if fix is None else f"fix: address {fix.source} feedback"
            )
            await self._repo.commit_all(self._commit_message(subject))
            if await self._repo.changed_files(self._base_sha):
                return
            decision = await self._gate(
                GateKind.ESCALATION, Phase.DEV, ESCALATION_ACTIONS,
                reason=(
                    "The developer finished without changing any files. Approve runs it "
                    "again; Request changes runs it with your guidance."
                ),
            )  # fmt: skip
            if decision.action == "revise":
                fix = prompts.Fix("human", decision.feedback or "")

    async def _review_loop(self, requirements: ArtifactRecord, design: ArtifactRecord) -> None:
        fixes = 0
        while True:
            prompt = prompts.reviewer_prompt(
                requirements.markdown, design.markdown, await self._diff(), self._base_sha
            )
            review = await self._produce(
                Role.REVIEWER,
                Phase.REVIEW,
                prompt,
                summary="review the change",
                artifact_id=design.id,
            )
            if review.data["verdict"] == "approve":
                return
            if fixes < REVIEW_FIX_LIMIT:
                fixes += 1
                loop = LoopIterationPayload(loop="review", iteration=fixes, limit=REVIEW_FIX_LIMIT)
                await self._emit("loop.iteration", loop, phase=Phase.REVIEW)
                fix = prompts.Fix("review", review.markdown)
            else:
                decision = await self._gate(
                    GateKind.ESCALATION, Phase.REVIEW, ESCALATION_ACTIONS, artifact_id=review.id,
                    reason=(
                        f"The reviewer still requests changes after {REVIEW_FIX_LIMIT} fix "
                        "rounds. Approve continues to QA anyway; Request changes sends your "
                        "guidance to the developer."
                    ),
                )  # fmt: skip
                if decision.action == "approve":
                    return
                fix = prompts.Fix(
                    "human",
                    f"{decision.feedback}\n\nThe reviewer's latest review:\n\n{review.markdown}",
                )
            await self._develop(requirements, design, fix)

    async def _qa_loop(self, requirements: ArtifactRecord, design: ArtifactRecord) -> None:
        fixes = 0
        while True:
            prompt = prompts.qa_prompt(requirements.markdown, design.markdown, await self._diff())
            report = await self._produce(
                Role.QA,
                Phase.QA,
                prompt,
                summary="verify the acceptance criteria",
                artifact_id=design.id,
            )
            await self._repo.commit_all(self._commit_message("test: add QA checks"))
            if report.data["passed"]:
                return
            if fixes < QA_FIX_LIMIT:
                fixes += 1
                loop = LoopIterationPayload(loop="qa", iteration=fixes, limit=QA_FIX_LIMIT)
                await self._emit("loop.iteration", loop, phase=Phase.QA)
                fix = prompts.Fix("qa", report.markdown)
            else:
                decision = await self._gate(
                    GateKind.ESCALATION, Phase.QA, ESCALATION_ACTIONS, artifact_id=report.id,
                    reason=(
                        f"QA still fails after {QA_FIX_LIMIT} fix rounds. Approve pushes the "
                        "change anyway; Request changes sends your guidance to the developer."
                    ),
                )  # fmt: skip
                if decision.action == "approve":
                    return
                fix = prompts.Fix(
                    "human", f"{decision.feedback}\n\nThe latest test report:\n\n{report.markdown}"
                )
            await self._develop(requirements, design, fix)

    async def _push(self) -> None:
        try:
            sha = await self._repo.push(
                self._branch, base=self._base_sha, expected=self._state.pushed_sha
            )
        except PushRefused as exc:
            blocked = GuardrailBlockedPayload(
                rule="push-refused", tool="git push", detail=str(exc)[:300]
            )
            await self._emit("guardrail.blocked", blocked, phase=self._phase)
            raise RunStop(
                "run.failed", RunFailedPayload(reason="guardrails", detail=str(exc))
            ) from exc
        self._state.pushed_sha = sha
        await self._emit(
            "git.pushed", GitPushedPayload(branch=self._branch, commit=sha), phase=self._phase
        )
        await self._save(Checkpoint.PUSHED)

    # --- release: DevOps, PR, CI, release gate --------------------------------------------------

    async def _release(self, requirements: ArtifactRecord, design: ArtifactRecord) -> str:
        while True:
            if self._before(Checkpoint.PR_OPENED):
                await self._open_pull_request(requirements, design)
            ci = await self._wait_for_ci()
            decision = await self._gate(
                GateKind.RELEASE, Phase.DEVOPS, RELEASE_ACTIONS,
                artifact_id=self._state.release_plan_id,
                reason=f"CI {ci.state}: {ci.summary}. Approve marks the pull request ready for review.",
            )  # fmt: skip
            if decision.action == "approve":
                break
            await self._build(
                requirements, design, fix=prompts.Fix("human", decision.feedback or "")
            )
        pr = self._pull_request()
        await self._d.github.mark_ready(pr)
        await self._emit(
            "pr.ready", PrReadyPayload(url=pr.url, number=pr.number), phase=Phase.DEVOPS
        )
        await self._save(Checkpoint.DONE)
        return pr.url

    async def _open_pull_request(
        self, requirements: ArtifactRecord, design: ArtifactRecord
    ) -> None:
        review = await self._d.artifacts.latest(self.run_id, ArtifactKind.REVIEW)
        report = await self._d.artifacts.latest(self.run_id, ArtifactKind.TEST_REPORT)
        prompt = prompts.devops_prompt(
            requirements.markdown, design.markdown, await self._diff(),
            review_md=review.markdown if review else None,
            test_report_md=report.markdown if report else None,
        )  # fmt: skip
        record = await self._produce(
            Role.DEVOPS,
            Phase.DEVOPS,
            prompt,
            summary="prepare the pull request",
            artifact_id=design.id,
        )
        self._state.release_plan_id = record.id
        plan = ReleasePlan.model_validate(record.data)
        footer = (
            f"\n\n---\nOpened by [agentic-sdlc]({PROJECT_URL}) run `{self.run_id}`. "
            f"Requirements, design, review and test report: `docs/sdlc/{self.run_id}/`.\n"
        )
        title = (await self._publishable(plan.pr_title, "pull request title")).strip()
        body = await self._publishable(
            f"{plan.pr_body_md}\n\n## Changelog\n\n{plan.changelog_md}{footer}", "pull request body"
        )
        for kind, name in PUBLISHED_DOCS.items():
            latest = await self._d.artifacts.latest(self.run_id, kind)
            if latest is not None:
                text = await self._publishable(latest.markdown, f"docs/sdlc/{name}")
                self._repo.write_file(f"docs/sdlc/{self.run_id}/{name}", text)
        await self._repo.commit_all(self._commit_message("docs: add SDLC artifacts"))
        await self._push()
        title = title or f"agentic-sdlc run {self.run_id}"
        if self._state.pr_number is None:
            pr = await self._d.github.open_pull_request(
                self._target.repo, head=self._branch, base=self._target.base_branch,
                title=title, body=body,
            )  # fmt: skip
            self._state.pr_number, self._state.pr_url, self._state.pr_node_id = (
                pr.number, pr.url, pr.node_id,
            )  # fmt: skip
            opened = PrOpenedPayload(url=pr.url, number=pr.number, draft=pr.draft)
            await self._emit("pr.opened", opened, phase=Phase.DEVOPS)
        else:
            await self._d.github.update_pull_request(
                self._target.repo, self._state.pr_number, title=title, body=body
            )
        await self._save(Checkpoint.PR_OPENED)

    async def _publishable(self, text: str, where: str) -> str:
        try:
            published = await self._d.guardrails.mask_for_publish(text)
        except GuardrailsUnavailable as exc:
            failed = RunFailedPayload(reason="guardrails-unavailable", detail=str(exc)[:500])
            raise RunStop("run.failed", failed) from exc
        if published.rules:
            blocked = GuardrailBlockedPayload(
                rule="redacted-before-publish", tool=where, detail=", ".join(published.rules)
            )
            await self._emit("guardrail.blocked", blocked, phase=Phase.DEVOPS)
        return published.text

    async def _wait_for_ci(self) -> CiStatus:
        settings = self._d.settings
        loop = asyncio.get_running_loop()
        last: CiStatus | None = None
        async with self._clock.paused():
            deadline = loop.time() + settings.ci_poll_timeout_s
            while True:
                try:
                    status = await self._d.github.ci_status(
                        self._target.repo, self._state.pushed_sha or ""
                    )
                except GitHubError as exc:
                    status = CiStatus("pending", f"CI status unavailable: {exc}"[:200])
                if status != last:
                    payload = CiStatusPayload(state=status.state, summary=status.summary)
                    await self._emit("ci.status", payload, phase=Phase.DEVOPS)
                    last = status
                if status.state != "pending":
                    return status
                left = deadline - loop.time()
                if left <= 0:
                    minutes = settings.ci_poll_timeout_s / 60
                    waited = CiStatus("pending", f"CI had not finished after {minutes:g} minutes")
                    payload = CiStatusPayload(state=waited.state, summary=waited.summary)
                    await self._emit("ci.status", payload, phase=Phase.DEVOPS)
                    return waited
                await asyncio.sleep(min(settings.ci_poll_interval_s, left))

    def _pull_request(self) -> PullRequest:
        state = self._state
        assert state.pr_number is not None and state.pr_url and state.pr_node_id
        return PullRequest(state.pr_number, state.pr_url, state.pr_node_id, draft=True)

    # --- helpers ----------------------------------------------------------------------------------

    async def _artifact(self, artifact_id: str | None) -> ArtifactRecord:
        record = await self._d.artifacts.get(artifact_id) if artifact_id else None
        if record is None:
            raise RuntimeError(f"artifact {artifact_id!r} from the checkpoint is missing")
        return record

    async def _diff(self) -> str:
        return await self._repo.diff_against(self._base_sha, self._d.settings.max_diff_bytes)

    def _title(self) -> str:
        first = self._run.requirement.strip().splitlines()[0]
        return first if len(first) <= 60 else first[:57] + "..."

    def _commit_message(self, subject: str) -> str:
        return f"{subject}\n\nagentic-sdlc run {self.run_id}"

    def _next_attempt(self, phase: Phase) -> int:
        attempt = self._state.attempts.get(phase.value, 0) + 1
        self._state.attempts[phase.value] = attempt
        return attempt

    def _budget_stop(self, detail: str | None) -> RunStop:
        detail = detail or f"the ${self._run.budget_usd:.2f} budget is spent"
        return RunStop("run.failed", RunFailedPayload(reason="budget", detail=detail))

    def _stop_if_budget_spent(self) -> None:
        registry = self._d.registry
        if registry.is_open(self.run_id) and registry.budget(self.run_id).exhausted:
            raise self._budget_stop(None)

    async def _save(self, checkpoint: Checkpoint) -> None:
        self._state.active_s = self._clock.used_s()
        await self._d.store.set_checkpoint(self.run_id, checkpoint, asdict(self._state))
        self._checkpoint = checkpoint

    async def _fail(self, reason: str, detail: str) -> None:
        failed = RunFailedPayload(reason=reason, detail=detail[:500])
        await self._emit("run.failed", failed, phase=self._phase)

    async def _emit(
        self, event_type: str, payload: ContractModel, *, phase: Phase | None = None
    ) -> None:
        await self._d.bus.emit(self.run_id, event_type, payload, phase=phase)
