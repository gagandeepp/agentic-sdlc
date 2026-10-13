"""Owns live runs: starts, aborts and resumes pipelines, and recovers runs after a restart.

One asyncio task per live run. On shutdown every task is cancelled with reason "shutdown",
so each records `run.interrupted` while Mongo is still up. On the next start, runs a crashed
process left mid-flight are marked interrupted too. Either way they can be resumed.
"""

import asyncio
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import httpx
from pydantic import SecretStr

from app.agents.roles import RoleConfig
from app.agents.runner import AgentRunner, isolation_problem
from app.dev.probe import SAMPLE_REPO
from app.dev.scripted import FakeGitHub, KeywordInputGuard, ScriptedAgentRunner
from app.events.bus import EventBus
from app.events.models import (
    Role,
    RunAbortedPayload,
    RunCreatedPayload,
    RunInterruptedPayload,
    RunStatus,
)
from app.gitops.github import GitHubClient, HttpGitHubClient
from app.gitops.local import create_local_remote
from app.guardrails.service import GuardrailsService
from app.llm.registry import ProxyRegistry
from app.orchestrator.gates import GateBroker, GateDecision
from app.orchestrator.input_guard import InputGuard, InputSource
from app.orchestrator.pipeline import Pipeline, PipelineDeps, RunControl, RunTarget
from app.settings import Settings
from app.store.artifacts import ArtifactStore
from app.store.mongo import MongoStore, NewRun, RunRecord

log = logging.getLogger(__name__)

ACTIVE_STATUSES = (RunStatus.PENDING, RunStatus.RUNNING, RunStatus.WAITING_GATE)
SCRIPTED_REPO = "local/sample-repo"
SCRIPTED_KEY = SecretStr("scripted-runs-never-call-a-model")


class RunRejected(Exception):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


@dataclass
class _Live:
    task: asyncio.Task[None]
    control: RunControl
    deps: PipelineDeps


class RunManager:
    def __init__(
        self,
        *,
        bus: EventBus,
        store: MongoStore,
        artifacts: ArtifactStore,
        gates: GateBroker,
        runner: AgentRunner,
        roles: Mapping[Role, RoleConfig],
        guard: InputGuard,
        guardrails: GuardrailsService,
        registry: ProxyRegistry,
        settings: Settings,
        http: httpx.AsyncClient,
    ) -> None:
        self._bus = bus
        self._store = store
        self._artifacts = artifacts
        self._gates = gates
        self._runner = runner
        self._roles = roles
        self._guard = guard
        self._guardrails = guardrails
        self._registry = registry
        self._settings = settings
        self._http = http
        self._live: dict[str, _Live] = {}
        self._lock = asyncio.Lock()  # start/resume/abort-while-interrupted are check-then-act

    def is_live(self, run_id: str) -> bool:
        return run_id in self._live

    async def start(
        self, *, requirement: str, repo: str, base_branch: str, budget_usd: float
    ) -> RunRecord:
        async with self._lock:
            key = self._live_run_key()
            self._require_capacity()
            run = await self._store.create_run(
                NewRun(
                    requirement=requirement, repo=repo, base_branch=base_branch,
                    budget_usd=budget_usd, max_parallel_devs=1, mode="live",
                )
            )  # fmt: skip
            await self._announce(run)
            self._launch(run, key)
            return run

    async def start_scripted(self, requirement: str) -> RunRecord:
        async with self._lock:
            self._require_capacity()
            run = await self._store.create_run(
                NewRun(
                    requirement=requirement, repo=SCRIPTED_REPO, budget_usd=1.0,
                    max_parallel_devs=1, mode="scripted",
                )
            )  # fmt: skip
            await asyncio.to_thread(create_local_remote, SAMPLE_REPO, self._remote_path(run.id))
            await self._announce(run)
            self._launch(run, SCRIPTED_KEY)
            return run

    async def decide(self, run_id: str, gate_id: str, decision: GateDecision) -> None:
        live = self._live.get(run_id)
        if live is None:
            raise RunRejected(409, "the run is not running; resume it before deciding its gate")
        # Only the text the chosen action uses reaches agents (or the log), so only it is checked.
        decision = GateDecision(
            decision.action,
            decision.feedback if decision.action == "revise" else None,
            decision.markdown if decision.action == "edit" else None,
        )
        gate = self._gates.validate(run_id, gate_id, decision)
        if (decision.feedback or decision.markdown) and self._budget_spent(run_id):
            # Neither the input check nor the agent that would act on the text can run.
            raise RunRejected(
                409, "the run's budget is spent: approve (the run then stops) or abort this gate"
            )
        texts: list[tuple[InputSource, str | None]] = [
            ("gate-feedback", decision.feedback),
            ("artifact-edit", decision.markdown),
        ]
        for source, text in texts:
            if not text:
                continue
            check = await live.deps.guard.check(run_id, text, source=source, phase=gate.phase)
            if check.unavailable:
                raise RunRejected(503, "the guardrails could not check this text; try again")
            if not check.allowed:
                raise RunRejected(422, f"blocked by guardrails ({check.rail})")
        await self._gates.resolve(run_id, gate_id, decision)

    async def abort(self, run_id: str) -> None:
        live = self._live.get(run_id)
        if live is not None:
            # Cancel once: a second cancel would interrupt the run while it records its end.
            if live.control.stop_reason is None:
                live.control.stop_reason = "abort"
                live.task.cancel()
            # wait(), unlike gather(), doesn't cancel the run if this request goes away.
            await asyncio.wait([live.task])
            return
        async with self._lock:
            run = await self._require_run(run_id)
            if run_id in self._live or run.status is not RunStatus.INTERRUPTED:
                raise RunRejected(
                    409, f"only running or interrupted runs can be aborted ({run.status})"
                )
            aborted = RunAbortedPayload(by="user", reason="aborted while interrupted")
            await self._bus.emit(run_id, "run.aborted", aborted)

    async def resume(self, run_id: str) -> RunRecord:
        async with self._lock:
            run = await self._require_run(run_id)
            if run_id in self._live or run.status is not RunStatus.INTERRUPTED:
                raise RunRejected(409, f"only interrupted runs can be resumed ({run.status})")
            if run.mode == "demo":
                raise RunRejected(409, "demo runs can't be resumed")
            key = SCRIPTED_KEY if run.mode == "scripted" else self._live_run_key()
            self._require_capacity()
            self._launch(run, key)
            return run

    async def recover(self) -> None:
        """Runs a previous process left mid-flight become interrupted (resumable)."""
        for run in await self._store.list_runs_in(ACTIVE_STATUSES):
            if run.id in self._live:
                continue
            if await self._store.last_event_is_terminal(run.id):
                continue  # it ended; only its projection update was lost
            interrupted = RunInterruptedPayload(last_checkpoint=run.checkpoint)
            await self._bus.emit(run.id, "run.interrupted", interrupted)

    async def shutdown(self) -> None:
        live = list(self._live.values())
        for entry in live:
            if entry.control.stop_reason is None:  # an abort in progress finishes as an abort
                entry.control.stop_reason = "shutdown"
                entry.task.cancel()
        if live:
            await asyncio.wait([entry.task for entry in live])

    # --- internals ------------------------------------------------------------------------------

    def _live_run_key(self) -> SecretStr:
        settings = self._settings
        if settings.agent_anthropic_key is None or settings.agent_github_token is None:
            raise RunRejected(
                400, "live runs need AGENT_ANTHROPIC_KEY and AGENT_GITHUB_TOKEN in the API's .env"
            )
        for role in self._roles.values():
            problem = isolation_problem(role, settings)
            if problem is not None:
                raise RunRejected(400, problem)
        return settings.agent_anthropic_key

    def _budget_spent(self, run_id: str) -> bool:
        return self._registry.is_open(run_id) and self._registry.budget(run_id).exhausted

    def _require_capacity(self) -> None:
        if len(self._live) >= self._settings.max_concurrent_runs:
            raise RunRejected(429, "another run is in progress; try again when it finishes")

    async def _require_run(self, run_id: str) -> RunRecord:
        run = await self._store.get_run(run_id)
        if run is None:
            raise RunRejected(404, "run not found")
        return run

    async def _announce(self, run: RunRecord) -> None:
        created = RunCreatedPayload(
            requirement=run.requirement, repo=run.repo, base_branch=run.base_branch,
            budget_usd=run.budget_usd, max_parallel_devs=run.max_parallel_devs,
        )  # fmt: skip
        await self._bus.emit(run.id, "run.created", created)

    def _remote_path(self, run_id: str) -> Path:
        return self._settings.workspace_root / "remotes" / f"{run_id}.git"

    def _deps(
        self,
        *,
        runner: AgentRunner,
        guard: InputGuard,
        github: GitHubClient,
        token: SecretStr | None,
    ) -> PipelineDeps:
        return PipelineDeps(
            bus=self._bus, store=self._store, artifacts=self._artifacts, gates=self._gates,
            runner=runner, roles=self._roles, guard=guard, guardrails=self._guardrails,
            github=github, registry=self._registry, settings=self._settings, github_token=token,
        )  # fmt: skip

    def _wiring(self, run: RunRecord) -> tuple[PipelineDeps, RunTarget]:
        settings = self._settings
        if run.mode == "scripted":
            deps = self._deps(
                runner=ScriptedAgentRunner(self._bus, delay_s=settings.scripted_agent_delay_s),
                guard=KeywordInputGuard(self._bus),
                github=FakeGitHub(),
                token=None,
            )
            return deps, RunTarget(run.repo, self._remote_path(run.id).as_uri(), run.base_branch)
        token = settings.agent_github_token
        assert token is not None  # checked by _live_run_key
        github = HttpGitHubClient(self._http, token, settings.github_api_url)
        deps = self._deps(runner=self._runner, guard=self._guard, github=github, token=token)
        clone_url = f"{settings.github_clone_base}/{run.repo}.git"
        return deps, RunTarget(run.repo, clone_url, run.base_branch)

    def _launch(self, run: RunRecord, key: SecretStr) -> None:
        deps, target = self._wiring(run)
        budget = self._registry.open_run(run.id, key, run.budget_usd)
        budget.spent_usd = run.spent_usd  # a resumed run continues from what it already spent
        control = RunControl()
        task = asyncio.create_task(
            Pipeline(deps, run, target, control).execute(), name=f"run-{run.id}"
        )
        entry = _Live(task, control, deps)
        self._live[run.id] = entry
        task.add_done_callback(lambda done: self._finished(run.id, entry, done))

    def _finished(self, run_id: str, entry: _Live, task: asyncio.Task[None]) -> None:
        if self._live.get(run_id) is entry:
            del self._live[run_id]
        if not task.cancelled() and task.exception() is not None:
            log.error("run %s crashed", run_id, exc_info=task.exception())
