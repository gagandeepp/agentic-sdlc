"""Keyless stand-ins for agents, GitHub and the input rails.

Tests drive the real pipeline with them, and so does the scripted demo run
(POST /api/dev/scripted-runs), which exercises the GUI, gates and abort/resume without keys.
"""

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from app.agents.runner import AgentOutcome, AgentSpec, OutcomeStatus
from app.events.bus import EventBus
from app.events.models import (
    AgentFinishedPayload,
    AgentStartedPayload,
    AgentTextPayload,
    ContractModel,
    GuardrailBlockedPayload,
    Phase,
    Role,
)
from app.gitops.github import CiStatus, PullRequest
from app.orchestrator.input_guard import GUARDRAILS_AGENT, InputCheck, InputSource

DEFAULT_OUTPUTS: dict[Role, dict[str, Any]] = {
    Role.BA: {
        "summary": "Add a health check endpoint.",
        "stories": [
            {
                "id": "US-1",
                "as_a": "operator",
                "i_want": "a /health endpoint",
                "so_that": "I can monitor the service",
                "acceptance_criteria": ['GET /health returns 200 with {"status": "ok"}'],
            }
        ],
        "assumptions": ["No authentication is needed for /health."],
        "open_questions": [],
    },
    Role.ARCHITECT: {
        "overview_md": "Add a `/health` route next to the existing routes, with a test.",
        "components": ["HTTP routes"],
        "tasks": [
            {
                "id": "T1",
                "title": "Add the /health route",
                "description": "Register a route that returns status ok, and test it.",
                "files": ["scripted_change.txt"],
                "depends_on": [],
                "acceptance_refs": ["US-1"],
            }
        ],
    },
    Role.DEV: {
        "summary": "Implemented the scripted change.",
        "task_ids": ["T1"],
        "files_changed": ["scripted_change.txt"],
        "notes": "",
    },
    Role.REVIEWER: {
        "verdict": "approve",
        "summary": "The change matches the design.",
        "comments": [],
    },
    Role.QA: {
        "passed": True,
        "summary": "Every acceptance criterion has a passing test.",
        "commands_run": ["uv run pytest -q"],
        "tests_added": [],
        "failures": [],
        "coverage_notes": "",
    },
    Role.DEVOPS: {
        "pr_title": "Add a health check endpoint",
        "pr_body_md": "Adds `GET /health`.\n\nVerify: `curl localhost:8000/health`.",
        "changelog_md": "- Added `GET /health`.",
        "ci_notes": "Tests pass locally.",
    },
}
# Split so this file itself doesn't trip the prompt-injection rules when scanned.
DEMO_BLOCKED_PHRASES = ("ignore your " + "instructions", "environment variables")


@dataclass(frozen=True)
class Scripted:
    """One scripted agent session: its outcome, its output and the files it writes."""

    status: OutcomeStatus = "ok"
    output: Mapping[str, Any] | None = None
    writes: Mapping[str, str] = field(default_factory=dict)
    error: str | None = None


class ScriptedAgentRunner:
    """Plays a per-role script instead of calling Claude, and records every spec it gets."""

    def __init__(
        self,
        bus: EventBus,
        script: Mapping[Role, Sequence[Scripted]] | None = None,
        *,
        delay_s: float = 0.0,
    ) -> None:
        self._bus = bus
        self._script = {role: list(steps) for role, steps in (script or {}).items()}
        self._delay_s = delay_s
        self.calls: list[AgentSpec] = []

    def prompts(self, role: Role) -> list[str]:
        return [spec.prompt for spec in self.calls if spec.role.role is role]

    async def run(self, spec: AgentSpec) -> AgentOutcome:
        role = spec.role.role
        self.calls.append(spec)
        steps = self._script.get(role)
        step = steps.pop(0) if steps else Scripted()

        async def emit(event_type: str, payload: ContractModel) -> None:
            await self._bus.emit(
                spec.run_id, event_type, payload, phase=spec.phase, agent_id=spec.agent_id
            )

        await emit(
            "agent.started",
            AgentStartedPayload(role=role, model=spec.role.model, task_id=spec.task_id),
        )
        if self._delay_s:
            await asyncio.sleep(self._delay_s)
        ok = step.status == "ok"
        writes = dict(step.writes)
        if role is Role.DEV and ok and not writes:
            count = len(self.prompts(Role.DEV))
            writes = {"scripted_change.txt": f"scripted change {count}\n"}
        for relative, content in writes.items():
            path = spec.workspace / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        outcome_text = "done" if ok else f"{step.status}: {step.error or 'scripted failure'}"
        await emit("agent.text", AgentTextPayload(text=f"[scripted {role.value}] {outcome_text}"))
        duration_ms = int(self._delay_s * 1000)
        await emit(
            "agent.finished", AgentFinishedPayload(ok=ok, num_turns=1, duration_ms=duration_ms)
        )
        output = dict(step.output if step.output is not None else DEFAULT_OUTPUTS[role])
        return AgentOutcome(
            step.status,
            output=output if ok else None,
            error=None if ok else step.error,
            num_turns=1,
            duration_ms=duration_ms,
            total_cost_usd=0.0,
        )


class FakeGitHub:
    """GitHub in memory: pull requests, plus a CI state sequence whose last state repeats."""

    def __init__(self, ci: Sequence[CiStatus] = (CiStatus("success", "1 check passed"),)) -> None:
        self.pulls: dict[int, dict[str, str]] = {}
        self.ready: set[int] = set()
        self._ci = list(ci)

    def _pull(self, number: int) -> PullRequest:
        repo = self.pulls[number]["repo"]
        return PullRequest(
            number=number,
            url=f"https://github.com/{repo}/pull/{number}",
            node_id=f"PR_{number}",
            draft=number not in self.ready,
        )

    async def open_pull_request(
        self, repo: str, *, head: str, base: str, title: str, body: str
    ) -> PullRequest:
        for number, pull in self.pulls.items():
            if pull["head"] == head:
                return await self.update_pull_request(repo, number, title=title, body=body)
        number = len(self.pulls) + 1
        self.pulls[number] = {
            "repo": repo,
            "head": head,
            "base": base,
            "title": title,
            "body": body,
        }
        return self._pull(number)

    async def update_pull_request(
        self, repo: str, number: int, *, title: str, body: str
    ) -> PullRequest:
        pull = self.pulls.setdefault(number, {"repo": repo, "head": "", "base": ""})
        pull.update(title=title, body=body)
        return self._pull(number)

    async def mark_ready(self, pr: PullRequest) -> None:
        self.ready.add(pr.number)

    async def ci_status(self, repo: str, sha: str) -> CiStatus:
        return self._ci.pop(0) if len(self._ci) > 1 else self._ci[0]


class KeywordInputGuard:
    """A keyless stand-in for the input rails: blocks text containing a listed phrase."""

    RAIL = "keyword check (scripted)"

    def __init__(self, bus: EventBus, blocked: Sequence[str] = DEMO_BLOCKED_PHRASES) -> None:
        self._bus = bus
        self._blocked = tuple(phrase.lower() for phrase in blocked)
        self.checked: list[tuple[str, str]] = []

    async def check(
        self, run_id: str, text: str, *, source: InputSource, phase: Phase | None
    ) -> InputCheck:
        self.checked.append((source, text))
        if not any(phrase in text.lower() for phrase in self._blocked):
            return InputCheck(allowed=True)
        blocked = GuardrailBlockedPayload(rule=self.RAIL, tool=source, detail=text[:300])
        await self._bus.emit(
            run_id, "guardrail.blocked", blocked, phase=phase, agent_id=GUARDRAILS_AGENT
        )
        return InputCheck(allowed=False, rail=self.RAIL)
