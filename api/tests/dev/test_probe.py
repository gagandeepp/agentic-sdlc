import os
import stat

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from pydantic import SecretStr

from app.agents.runner import AgentOutcome, AgentSpec
from app.dev.probe import execute_probe, prepare_probe
from app.events.models import Role

REQUIREMENTS = {
    "summary": "s",
    "stories": [
        {"id": "US-1", "as_a": "a", "i_want": "b", "so_that": "c", "acceptance_criteria": ["d"]}
    ],
    "assumptions": [],
    "open_questions": [],
}


class StubRunner:
    def __init__(self) -> None:
        self.specs: list[AgentSpec] = []

    async def run(self, spec: AgentSpec) -> AgentOutcome:
        self.specs.append(spec)
        return AgentOutcome("ok", output=REQUIREMENTS, num_turns=3)


def _with_key(app: FastAPI) -> None:
    key = SecretStr("sk-ant-test-probe")
    app.state.settings = app.state.settings.model_copy(update={"agent_anthropic_key": key})


async def test_workspace_copy_uses_the_process_umask_not_source_modes(app: FastAPI) -> None:
    # In the container the API runs with umask 002 so the agent uid (group sdlc) can write.
    _with_key(app)
    previous = os.umask(0o002)
    try:
        ctx = await prepare_probe(app.state, role=Role.BA, requirement="x")
    finally:
        os.umask(previous)
    paths = [ctx.spec.workspace, *ctx.spec.workspace.rglob("*")]
    assert all(path.stat().st_mode & stat.S_IWGRP for path in paths)
    assert not any("__pycache__" in path.parts for path in paths)


class ExplodingRunner:
    async def run(self, spec: AgentSpec) -> AgentOutcome:
        raise RuntimeError("boom")


async def test_a_crashing_runner_still_ends_the_run(app: FastAPI) -> None:
    _with_key(app)
    app.state.runner = ExplodingRunner()
    ctx = await prepare_probe(app.state, role=Role.BA, requirement="x")
    with pytest.raises(RuntimeError):
        await execute_probe(app.state, ctx)
    events = [e async for e in app.state.store.list_events_after(ctx.run.id)]
    assert events[-1].type == "run.failed"
    assert "boom" in (events[-1].payload.detail or "")  # type: ignore[union-attr]
    assert not app.state.registry.is_open(ctx.run.id)


async def test_probe_requires_a_key(client: AsyncClient) -> None:
    response = await client.post("/api/dev/agent-probe", json={"role": "ba", "requirement": "x"})
    assert response.status_code == 400
    assert "AGENT_ANTHROPIC_KEY" in response.json()["detail"]


async def test_probe_refuses_bash_roles_without_uid_isolation(
    app: FastAPI, client: AsyncClient
) -> None:
    _with_key(app)
    response = await client.post("/api/dev/agent-probe", json={"role": "dev", "requirement": "x"})
    assert response.status_code == 400
    assert "AGENT_USER" in response.json()["detail"]


async def test_probe_prepares_a_workspace_and_records_the_artifact(app: FastAPI) -> None:
    _with_key(app)
    runner = StubRunner()
    app.state.runner = runner
    ctx = await prepare_probe(app.state, role=Role.BA, requirement="Add a low-stock report")
    assert (ctx.spec.workspace / "inventory" / "stock.py").is_file()
    assert (ctx.spec.workspace.parent / "outside.txt").is_file()
    outcome = await execute_probe(app.state, ctx)
    assert outcome.ok
    events = [e async for e in app.state.store.list_events_after(ctx.run.id)]
    types = [e.type for e in events]
    assert types[:3] == ["run.created", "run.status_changed", "phase.started"]
    assert "artifact.created" in types and types[-1] == "run.completed"
    assert any(
        e.type == "agent.text" and e.payload.text.startswith("# Requirements")  # type: ignore[union-attr]
        for e in events
    )
    assert not app.state.registry.is_open(ctx.run.id)  # key and grants revoked
