import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, ClassVar

import pytest
from claude_agent_sdk import (
    AssistantMessage,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)
from pydantic import SecretStr

from app.agents.artifacts import Requirements
from app.agents.roles import load_roles
from app.agents.runner import AgentSpec, SdkAgentRunner, classify, translate
from app.events.bus import EventBus
from app.events.models import Phase, Role
from app.guardrails.service import GuardrailsService
from app.llm.pricing import load_pricing
from app.llm.registry import ProxyRegistry
from app.settings import Settings
from app.store.mongo import MongoStore, NewRun

REQUIREMENTS = {
    "summary": "s",
    "stories": [
        {"id": "US-1", "as_a": "a", "i_want": "b", "so_that": "c", "acceptance_criteria": ["d"]}
    ],
}


def _result(**overrides: Any) -> ResultMessage:
    fields: dict[str, Any] = {
        "subtype": "success",
        "duration_ms": 10,
        "duration_api_ms": 5,
        "is_error": False,
        "num_turns": 2,
        "session_id": "s",
        "structured_output": REQUIREMENTS,
        "total_cost_usd": 0.01,
        "terminal_reason": "completed",
    }
    return ResultMessage(**{**fields, **overrides})


def test_translate_maps_blocks_to_events() -> None:
    assistant = AssistantMessage(
        content=[
            TextBlock(text="reading"),
            ThinkingBlock(thinking="", signature="x"),
            ThinkingBlock(thinking="plan", signature="y"),
            ToolUseBlock(id="t1", name="Read", input={"file_path": "a.py"}),
        ],
        model="claude-sonnet-5-5",
    )
    events = translate(assistant)
    assert [e for e, _ in events] == ["agent.text", "agent.thinking", "agent.tool_call"]
    user = UserMessage(
        content=[
            ToolResultBlock(
                tool_use_id="t1", content=[{"type": "text", "text": "line 1"}], is_error=False
            )
        ]
    )
    [(event_type, payload)] = translate(user)
    assert event_type == "agent.tool_result" and payload.content == "line 1"  # type: ignore[attr-defined]
    assert translate(SystemMessage(subtype="compact_boundary", data={}))[0][0] == "agent.text"
    assert translate(SystemMessage(subtype="init", data={})) == []
    assert translate(UserMessage(content="the prompt")) == []


@pytest.mark.parametrize(
    ("result", "budget_exhausted", "failure", "status"),
    [
        (_result(), False, None, "ok"),
        (_result(), True, None, "budget_exhausted"),
        (_result(subtype="error_max_budget_usd", is_error=True), False, None, "budget_exhausted"),
        (
            _result(
                is_error=True,
                terminal_reason="api_error",
                api_error_status=400,
                structured_output=None,
            ),
            False,
            None,
            "api_error",
        ),
        (_result(structured_output=None), False, None, "invalid_output"),
        (_result(structured_output={"summary": "no stories"}), False, None, "invalid_output"),
        (None, False, ("timeout", "too slow"), "timeout"),
        (None, False, None, "error"),
    ],
)
def test_classify(
    result: ResultMessage | None,
    budget_exhausted: bool,
    failure: tuple[str, str] | None,
    status: str,
) -> None:
    outcome = classify(
        result,
        budget_exhausted=budget_exhausted,
        failure=failure,  # type: ignore[arg-type]
        output_model=Requirements,
    )
    assert outcome.status == status
    if status == "ok":
        assert outcome.output == Requirements.model_validate(REQUIREMENTS).model_dump(mode="json")


class FakeClient:
    instances: ClassVar[list["FakeClient"]] = []

    def __init__(self, *, options: Any, messages: list[Any], hang: bool = False) -> None:
        self.options = options
        self.messages = messages
        self.hang = hang
        self.prompts: list[str] = []
        FakeClient.instances.append(self)

    async def __aenter__(self) -> "FakeClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def query(self, prompt: str) -> None:
        self.prompts.append(prompt)

    async def receive_response(self) -> AsyncIterator[Any]:
        for message in self.messages:
            yield message
        if self.hang:
            await asyncio.sleep(3600)


@pytest.fixture
async def runner_env(store: MongoStore, tmp_path: Path) -> dict[str, Any]:
    guardrails = GuardrailsService()
    await guardrails.self_test()
    registry = ProxyRegistry(load_pricing())
    run = await store.create_run(NewRun(requirement="r", repo="o/r"))
    registry.open_run(run.id, SecretStr("sk-ant-test-runner"), budget_usd=1.0)
    return {
        "run": run,
        "registry": registry,
        "bus": EventBus(store),
        "guardrails": guardrails,
        "settings": Settings(_env_file=None, workspace_root=tmp_path),
        "store": store,
        "tmp": tmp_path,
    }


def _spec(env: dict[str, Any], **role_overrides: Any) -> AgentSpec:
    role = load_roles()[Role.BA].model_copy(update=role_overrides)
    workspace = env["tmp"] / "repo"
    workspace.mkdir(exist_ok=True)
    return AgentSpec(
        run_id=env["run"].id,
        agent_id="ba",
        role=role,
        prompt="Write requirements.",
        workspace=workspace,
        config_dir=env["tmp"] / "claude",
        phase=Phase.BA,
    )


def _runner(env: dict[str, Any], messages: list[Any], hang: bool = False) -> SdkAgentRunner:
    return SdkAgentRunner(
        bus=env["bus"],
        registry=env["registry"],
        guardrails=env["guardrails"],
        settings=env["settings"],
        proxy_base_url="http://127.0.0.1:9",
        client_factory=lambda options: FakeClient(options=options, messages=messages, hang=hang),
    )


async def test_run_streams_events_and_returns_the_validated_artifact(
    runner_env: dict[str, Any],
) -> None:
    messages = [
        AssistantMessage(content=[TextBlock(text="reading the repo")], model="claude-sonnet-5-5"),
        _result(),
    ]
    outcome = await _runner(runner_env, messages).run(_spec(runner_env))
    assert outcome.ok and outcome.output is not None
    events = [e async for e in runner_env["store"].list_events_after(runner_env["run"].id)]
    assert [e.type for e in events] == ["agent.started", "agent.text", "agent.finished"]
    assert events[-1].payload.ok is True
    client = FakeClient.instances[-1]
    token = client.options.env["ANTHROPIC_AUTH_TOKEN"]
    assert runner_env["registry"].lookup(token) is None  # revoked after the session


async def test_options_carry_the_isolation_contract(runner_env: dict[str, Any]) -> None:
    spec = _spec(runner_env)
    runner = _runner(runner_env, [])
    grant = runner_env["registry"].grant(
        run_id=spec.run_id, agent_id="ba", phase=Phase.BA, allowed_models={"claude-sonnet-5-5"}
    )
    options = runner.build_options(spec, grant, hooks={})
    assert options.env["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:9"
    assert options.env["ANTHROPIC_AUTH_TOKEN"] == grant.token
    assert options.env["ANTHROPIC_API_KEY"] == ""
    assert options.env["CLAUDE_CONFIG_DIR"] == str(spec.config_dir)
    assert "sk-ant-test" not in str(options.env)
    assert options.setting_sources == []
    assert options.permission_mode == "dontAsk"
    assert options.tools == ["Read", "Glob", "Grep"]
    assert set(options.disallowed_tools) == {"WebFetch", "WebSearch", "Task"}
    assert options.output_format is not None and options.output_format["type"] == "json_schema"
    assert options.cli_path is None  # no agent_user: run the bundled CLI directly
    isolated = SdkAgentRunner(
        bus=runner_env["bus"],
        registry=runner_env["registry"],
        guardrails=runner_env["guardrails"],
        settings=runner_env["settings"].model_copy(update={"agent_user": "agent"}),
        proxy_base_url="http://127.0.0.1:9",
    )
    isolated_options = isolated.build_options(spec, grant, hooks={})
    assert str(isolated_options.cli_path) == "/usr/local/bin/agent-cli"
    assert isolated_options.env["CLAUDE_AGENT_REAL_CLI"].endswith("_bundled/claude")


async def test_a_session_that_overruns_its_timeout_is_cut_off(runner_env: dict[str, Any]) -> None:
    outcome = await _runner(runner_env, [], hang=True).run(_spec(runner_env, timeout_s=1))
    assert outcome.status == "timeout"
    events = [e async for e in runner_env["store"].list_events_after(runner_env["run"].id)]
    assert events[-1].type == "agent.finished" and events[-1].payload.ok is False
