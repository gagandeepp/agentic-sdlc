"""Run one agent session through the Claude Agent SDK and stream it into the event bus.

Usage events come from the credential proxy, not from here, so nothing is double-counted.
`agent.finished` is emitted only after the proxy has drained and revoked the session's token,
so it always follows that agent's last usage event.
"""

import asyncio
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ClaudeSDKError,
    HookMatcher,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)
from pydantic import BaseModel, ValidationError

from app.agents.artifacts import output_schema
from app.agents.isolation import bundled_cli_path, kill_agent_processes
from app.agents.policy import make_policy_hooks
from app.agents.roles import RoleConfig
from app.events.bus import EventBus
from app.events.models import (
    AgentFinishedPayload,
    AgentStartedPayload,
    AgentTextPayload,
    AgentThinkingPayload,
    AgentToolCallPayload,
    AgentToolResultPayload,
    ContractModel,
    GuardrailBlockedPayload,
    Phase,
)
from app.guardrails.service import GuardrailsService
from app.llm.registry import Grant, ProxyRegistry
from app.settings import Settings

log = logging.getLogger(__name__)

HAIKU_MODEL = "claude-haiku-5-5"
DISALLOWED_TOOLS = ["WebFetch", "WebSearch", "Task"]
OutcomeStatus = Literal["ok", "invalid_output", "api_error", "timeout", "budget_exhausted", "error"]
COMPACTION_NOTE = "[Claude Code compacted the conversation context]"


@dataclass(frozen=True)
class AgentSpec:
    run_id: str
    agent_id: str
    role: RoleConfig
    prompt: str
    workspace: Path
    config_dir: Path
    phase: Phase
    task_id: str | None = None


@dataclass(frozen=True)
class AgentOutcome:
    status: OutcomeStatus
    output: dict[str, Any] | None = None
    error: str | None = None
    num_turns: int = 0
    duration_ms: int = 0
    total_cost_usd: float | None = None

    @property
    def ok(self) -> bool:
        return self.status == "ok"


class AgentRunner(Protocol):
    async def run(self, spec: AgentSpec) -> AgentOutcome: ...


def _result_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    parts = []
    for item in content:
        if isinstance(item, dict) and item.get("type") == "text":
            parts.append(str(item.get("text", "")))
        else:
            parts.append(json.dumps(item, default=str))
    return "\n".join(parts)


def translate(message: Any) -> list[tuple[str, ContractModel]]:
    events: list[tuple[str, ContractModel]] = []
    if isinstance(message, AssistantMessage):
        for block in message.content:
            if isinstance(block, TextBlock) and block.text.strip():
                events.append(("agent.text", AgentTextPayload(text=block.text)))
            elif isinstance(block, ThinkingBlock) and block.thinking.strip():
                events.append(("agent.thinking", AgentThinkingPayload(text=block.thinking)))
            elif isinstance(block, ToolUseBlock):
                call = AgentToolCallPayload(
                    tool_use_id=block.id, name=block.name, input=block.input
                )
                events.append(("agent.tool_call", call))
    elif isinstance(message, UserMessage) and isinstance(message.content, list):
        for block in message.content:
            if isinstance(block, ToolResultBlock):
                result = AgentToolResultPayload.from_content(
                    block.tool_use_id, _result_text(block.content), is_error=bool(block.is_error)
                )
                events.append(("agent.tool_result", result))
    elif isinstance(message, SystemMessage) and message.subtype == "compact_boundary":
        events.append(("agent.text", AgentTextPayload(text=COMPACTION_NOTE)))
    return events


def classify(
    result: ResultMessage | None,
    *,
    budget_exhausted: bool,
    failure: tuple[OutcomeStatus, str] | None,
    output_model: type[BaseModel],
) -> AgentOutcome:
    turns = result.num_turns if result else 0
    cost = result.total_cost_usd if result else None
    common: dict[str, Any] = {"num_turns": turns, "total_cost_usd": cost}
    if budget_exhausted or (result is not None and result.subtype == "error_max_budget_usd"):
        return AgentOutcome("budget_exhausted", error="run budget exhausted", **common)
    if failure is not None:
        return AgentOutcome(failure[0], error=failure[1], **common)
    if result is None:
        return AgentOutcome("error", error="the session ended without a result", **common)
    if result.is_error or result.terminal_reason == "api_error":
        detail = f"API error {result.api_error_status or ''} {result.errors or ''}".strip()
        return AgentOutcome("api_error", error=detail, **common)
    if result.structured_output is None:
        error = f"no structured output ({result.subtype})"
        return AgentOutcome("invalid_output", error=error, **common)
    try:
        output = output_model.model_validate(result.structured_output)
    except ValidationError as exc:
        return AgentOutcome("invalid_output", error=str(exc)[:500], **common)
    return AgentOutcome("ok", output=output.model_dump(mode="json"), **common)


class SdkAgentRunner:
    def __init__(
        self,
        *,
        bus: EventBus,
        registry: ProxyRegistry,
        guardrails: GuardrailsService,
        settings: Settings,
        proxy_base_url: str,
        client_factory: Callable[..., Any] = ClaudeSDKClient,
    ) -> None:
        self._bus = bus
        self._registry = registry
        self._guardrails = guardrails
        self._settings = settings
        self._proxy_base_url = proxy_base_url
        self._client_factory = client_factory

    def build_options(
        self, spec: AgentSpec, grant: Grant, hooks: dict[str, list[HookMatcher]]
    ) -> ClaudeAgentOptions:
        env = {
            "ANTHROPIC_BASE_URL": self._proxy_base_url,
            "ANTHROPIC_AUTH_TOKEN": grant.token,
            "ANTHROPIC_API_KEY": "",
            "ANTHROPIC_DEFAULT_HAIKU_MODEL": HAIKU_MODEL,
            "CLAUDE_CONFIG_DIR": str(spec.config_dir),
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
        }
        cli_path: Path | None = None
        if self._settings.agent_user:
            cli_path = self._settings.agent_cli_path
            env["CLAUDE_AGENT_REAL_CLI"] = str(bundled_cli_path())
            env["AGENT_USER"] = self._settings.agent_user
        budget = self._registry.budget(spec.run_id)
        return ClaudeAgentOptions(
            cwd=str(spec.workspace),
            model=spec.role.model,
            effort=spec.role.effort,
            tools=list(spec.role.tools),
            allowed_tools=spec.role.allowed_tools,
            disallowed_tools=list(DISALLOWED_TOOLS),
            permission_mode="dontAsk",
            system_prompt=spec.role.system_prompt,
            max_turns=spec.role.max_turns,
            max_budget_usd=max(budget.remaining_usd, 0.01),
            output_format={"type": "json_schema", "schema": output_schema(spec.role.output_model)},
            setting_sources=[],
            hooks=hooks,  # type: ignore[arg-type]
            cli_path=cli_path,
            env=env,
            stderr=lambda line: log.debug("[%s/%s] %s", spec.run_id, spec.agent_id, line),
        )

    async def run(self, spec: AgentSpec) -> AgentOutcome:
        async def emit(event_type: str, payload: ContractModel) -> None:
            await self._bus.emit(
                spec.run_id, event_type, payload, phase=spec.phase, agent_id=spec.agent_id
            )

        async def on_block(payload: GuardrailBlockedPayload) -> None:
            await emit("guardrail.blocked", payload)

        started = time.monotonic()
        spec.config_dir.mkdir(parents=True, exist_ok=True)
        grant = self._registry.grant(
            run_id=spec.run_id, agent_id=spec.agent_id, phase=spec.phase,
            allowed_models={spec.role.model, HAIKU_MODEL},
        )  # fmt: skip
        await emit(
            "agent.started",
            AgentStartedPayload(role=spec.role.role, model=spec.role.model, task_id=spec.task_id),
        )
        hooks = make_policy_hooks(spec.workspace, self._guardrails, on_block)
        result: ResultMessage | None = None
        failure: tuple[OutcomeStatus, str] | None = None
        try:
            async with asyncio.timeout(spec.role.timeout_s):
                options = self.build_options(spec, grant, hooks)
                async with self._client_factory(options=options) as client:
                    await client.query(spec.prompt)
                    async for message in client.receive_response():
                        for event_type, payload in translate(message):
                            await emit(event_type, payload)
                        if isinstance(message, ResultMessage):
                            result = message
        except TimeoutError:
            failure = ("timeout", f"the agent exceeded its {spec.role.timeout_s}s limit")
        except ClaudeSDKError as exc:
            failure = ("error", f"{type(exc).__name__}: {exc}"[:500])
        finally:
            if self._settings.agent_user:
                await kill_agent_processes(self._settings.agent_user)
            await self._registry.drain_and_revoke(grant.token)
        outcome = classify(
            result,
            budget_exhausted=self._registry.budget(spec.run_id).exhausted,
            failure=failure,
            output_model=spec.role.output_model,
        )
        duration_ms = int((time.monotonic() - started) * 1000)
        finished = AgentFinishedPayload(
            ok=outcome.ok, num_turns=outcome.num_turns, duration_ms=duration_ms
        )
        await emit("agent.finished", finished)
        return AgentOutcome(
            outcome.status,
            outcome.output,
            outcome.error,
            outcome.num_turns,
            duration_ms,
            outcome.total_cost_usd,
        )
