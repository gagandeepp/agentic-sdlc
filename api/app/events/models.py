"""Run event contract.

Single source of truth for the live view, replay, metrics and tests. The TypeScript types in
web/src/types/api.ts are generated from these models by `make types`.
"""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

TOOL_RESULT_LIMIT_BYTES = 16_384
TRUNCATION_MARK = "...[truncated]"


def _clip(text: str, limit: int = TOOL_RESULT_LIMIT_BYTES) -> tuple[str, int | None]:
    """Cap `text` to `limit` UTF-8 bytes; return it and the original size when it was cut.

    "replace" turns lone surrogates (seen in decoded CLI output) into "?" instead of raising.
    """
    raw = text.encode("utf-8", "replace")
    if len(raw) <= limit:
        return raw.decode(), None
    return raw[:limit].decode(errors="ignore"), len(raw)


def _cap_strings(value: Any) -> tuple[Any, bool]:
    """Cap every string in a JSON-like value; report whether anything was cut."""
    if isinstance(value, str):
        mark = len(TRUNCATION_MARK.encode())
        clipped, original = _clip(value, TOOL_RESULT_LIMIT_BYTES - mark)
        if original is None:
            return _clip(value)[0], False
        return clipped + TRUNCATION_MARK, True
    if isinstance(value, dict):
        pairs = {key: _cap_strings(item) for key, item in value.items()}
        return {key: item for key, (item, _) in pairs.items()}, any(c for _, c in pairs.values())
    if isinstance(value, list):
        items = [_cap_strings(item) for item in value]
        return [item for item, _ in items], any(c for _, c in items)
    return value, False


class Phase(StrEnum):
    INIT = "init"
    BA = "ba"
    ARCHITECT = "architect"
    DEV = "dev"
    INTEGRATE = "integrate"
    REVIEW = "review"
    QA = "qa"
    DEVOPS = "devops"
    DONE = "done"


class Role(StrEnum):
    BA = "ba"
    ARCHITECT = "architect"
    DEV = "dev"
    INTEGRATOR = "integrator"
    REVIEWER = "reviewer"
    QA = "qa"
    DEVOPS = "devops"
    GUARDRAILS = "guardrails"


class RunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING_GATE = "waiting_gate"
    COMPLETED = "completed"
    FAILED = "failed"
    ABORTED = "aborted"
    INTERRUPTED = "interrupted"


class GateKind(StrEnum):
    REQUIREMENTS = "requirements"
    DESIGN = "design"
    RELEASE = "release"
    ESCALATION = "escalation"


class Checkpoint(StrEnum):
    """Durable points a run resumes from, in run order (stored on the run, not derived)."""

    CLONED = "cloned"
    REQUIREMENTS_DRAFTED = "requirements_drafted"
    REQUIREMENTS_APPROVED = "requirements_approved"
    DESIGN_DRAFTED = "design_drafted"
    DESIGN_APPROVED = "design_approved"
    PUSHED = "pushed"
    PR_OPENED = "pr_opened"
    DONE = "done"


GateAction = Literal["approve", "revise", "edit", "abort"]


class ArtifactKind(StrEnum):
    REQUIREMENTS = "requirements"
    DESIGN = "design"
    DEV_RESULT = "dev_result"
    INTEGRATION = "integration"
    REVIEW = "review"
    TEST_REPORT = "test_report"
    RELEASE_PLAN = "release_plan"


class ContractModel(BaseModel):
    # Defaulted fields are always serialized, so mark them required in the JSON schema;
    # that keeps the generated TypeScript fields non-optional.
    model_config = ConfigDict(
        extra="forbid", frozen=True, json_schema_serialization_defaults_required=True
    )


# --- payloads ---------------------------------------------------------------------------


class RunCreatedPayload(ContractModel):
    requirement: str
    repo: str
    base_branch: str
    budget_usd: float
    max_parallel_devs: int


class RunStatusChangedPayload(ContractModel):
    status: RunStatus


class RunCompletedPayload(ContractModel):
    pr_url: str | None = None


class RunFailedPayload(ContractModel):
    reason: str
    detail: str | None = None


class RunAbortedPayload(ContractModel):
    by: Literal["user", "system"]
    reason: str | None = None


class RunInterruptedPayload(ContractModel):
    last_checkpoint: Checkpoint | None = None


class PhaseStartedPayload(ContractModel):
    attempt: int = 1


class PhaseCompletedPayload(ContractModel):
    attempt: int = 1
    outcome: Literal["ok", "escalated", "failed"]


class AgentStartedPayload(ContractModel):
    role: Role
    model: str
    task_id: str | None = None


class AgentFinishedPayload(ContractModel):
    ok: bool
    num_turns: int
    duration_ms: int


class AgentTextPayload(ContractModel):
    text: str


class AgentThinkingPayload(ContractModel):
    text: str


class AgentToolCallPayload(ContractModel):
    tool_use_id: str
    name: str
    input: dict[str, Any]
    input_truncated: bool = False

    @model_validator(mode="before")
    @classmethod
    def _cap_input(cls, data: Any) -> Any:
        if not isinstance(data, dict) or not isinstance(data.get("input"), dict):
            return data
        capped, truncated = _cap_strings(data["input"])
        return {
            **data,
            "input": capped,
            "input_truncated": bool(data.get("input_truncated")) or truncated,
        }


class AgentToolResultPayload(ContractModel):
    tool_use_id: str
    is_error: bool
    content: str
    truncated: bool = False
    original_size: int | None = None

    @model_validator(mode="before")
    @classmethod
    def _cap_content(cls, data: Any) -> Any:
        if not isinstance(data, dict) or not isinstance(data.get("content"), str):
            return data
        content, original = _clip(data["content"])
        # Re-validating a stored (already capped) event keeps its flags and original size.
        return {
            **data,
            "content": content,
            "truncated": bool(data.get("truncated")) or original is not None,
            "original_size": data.get("original_size") or original,
        }

    @classmethod
    def from_content(
        cls, tool_use_id: str, content: str, *, is_error: bool = False
    ) -> "AgentToolResultPayload":
        return cls(tool_use_id=tool_use_id, is_error=is_error, content=content)


class AgentUsagePayload(ContractModel):
    model: str
    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    context_tokens: int | None = None
    context_max: int | None = None
    cost_usd: float


class HandoffPayload(ContractModel):
    from_agent: str
    to_agent: str
    summary: str
    artifact_id: str | None = None


class ArtifactPayload(ContractModel):
    artifact_id: str
    kind: ArtifactKind
    version: int
    title: str


class GateOpenedPayload(ContractModel):
    gate_id: str
    gate: GateKind
    actions: list[GateAction] = Field(min_length=1)
    artifact_id: str | None = None
    reason: str | None = None


class GateResolvedPayload(ContractModel):
    gate_id: str
    action: GateAction
    feedback: str | None = None


class LoopIterationPayload(ContractModel):
    loop: Literal["review", "qa", "revise"]
    iteration: int
    limit: int | None = None  # None: a person may ask for changes any number of times


class GuardrailBlockedPayload(ContractModel):
    rule: str
    tool: str
    detail: str


class BudgetUpdatedPayload(ContractModel):
    spent_usd: float
    budget_usd: float


class MergeStartedPayload(ContractModel):
    branch: str
    into: str


class MergeConflictPayload(ContractModel):
    branch: str
    files: list[str]


class MergeCompletedPayload(ContractModel):
    branch: str
    into: str
    commit: str


class GitPushedPayload(ContractModel):
    branch: str
    commit: str


class PrOpenedPayload(ContractModel):
    url: str
    number: int
    draft: bool


class PrReadyPayload(ContractModel):
    url: str
    number: int


class CiStatusPayload(ContractModel):
    state: Literal["pending", "success", "failure", "neutral"]
    summary: str


# --- events -------------------------------------------------------------------------------


class EventBase(ContractModel):
    run_id: str
    seq: int = Field(ge=1)
    ts: datetime
    phase: Phase | None = None
    agent_id: str | None = None


class RunCreatedEvent(EventBase):
    type: Literal["run.created"]
    payload: RunCreatedPayload


class RunStatusChangedEvent(EventBase):
    type: Literal["run.status_changed"]
    payload: RunStatusChangedPayload


class RunCompletedEvent(EventBase):
    type: Literal["run.completed"]
    payload: RunCompletedPayload


class RunFailedEvent(EventBase):
    type: Literal["run.failed"]
    payload: RunFailedPayload


class RunAbortedEvent(EventBase):
    type: Literal["run.aborted"]
    payload: RunAbortedPayload


class RunInterruptedEvent(EventBase):
    type: Literal["run.interrupted"]
    payload: RunInterruptedPayload


class PhaseStartedEvent(EventBase):
    type: Literal["phase.started"]
    payload: PhaseStartedPayload


class PhaseCompletedEvent(EventBase):
    type: Literal["phase.completed"]
    payload: PhaseCompletedPayload


class AgentStartedEvent(EventBase):
    type: Literal["agent.started"]
    payload: AgentStartedPayload


class AgentFinishedEvent(EventBase):
    type: Literal["agent.finished"]
    payload: AgentFinishedPayload


class AgentTextEvent(EventBase):
    type: Literal["agent.text"]
    payload: AgentTextPayload


class AgentThinkingEvent(EventBase):
    type: Literal["agent.thinking"]
    payload: AgentThinkingPayload


class AgentToolCallEvent(EventBase):
    type: Literal["agent.tool_call"]
    payload: AgentToolCallPayload


class AgentToolResultEvent(EventBase):
    type: Literal["agent.tool_result"]
    payload: AgentToolResultPayload


class AgentUsageEvent(EventBase):
    type: Literal["agent.usage"]
    payload: AgentUsagePayload


class HandoffEvent(EventBase):
    type: Literal["handoff"]
    payload: HandoffPayload


class ArtifactCreatedEvent(EventBase):
    type: Literal["artifact.created"]
    payload: ArtifactPayload


class ArtifactRevisedEvent(EventBase):
    type: Literal["artifact.revised"]
    payload: ArtifactPayload


class GateOpenedEvent(EventBase):
    type: Literal["gate.opened"]
    payload: GateOpenedPayload


class GateResolvedEvent(EventBase):
    type: Literal["gate.resolved"]
    payload: GateResolvedPayload


class LoopIterationEvent(EventBase):
    type: Literal["loop.iteration"]
    payload: LoopIterationPayload


class GuardrailBlockedEvent(EventBase):
    type: Literal["guardrail.blocked"]
    payload: GuardrailBlockedPayload


class BudgetUpdatedEvent(EventBase):
    type: Literal["budget.updated"]
    payload: BudgetUpdatedPayload


class MergeStartedEvent(EventBase):
    type: Literal["merge.started"]
    payload: MergeStartedPayload


class MergeConflictEvent(EventBase):
    type: Literal["merge.conflict"]
    payload: MergeConflictPayload


class MergeCompletedEvent(EventBase):
    type: Literal["merge.completed"]
    payload: MergeCompletedPayload


class GitPushedEvent(EventBase):
    type: Literal["git.pushed"]
    payload: GitPushedPayload


class PrOpenedEvent(EventBase):
    type: Literal["pr.opened"]
    payload: PrOpenedPayload


class PrReadyEvent(EventBase):
    type: Literal["pr.ready"]
    payload: PrReadyPayload


class CiStatusEvent(EventBase):
    type: Literal["ci.status"]
    payload: CiStatusPayload


RunEvent = Annotated[
    RunCreatedEvent
    | RunStatusChangedEvent
    | RunCompletedEvent
    | RunFailedEvent
    | RunAbortedEvent
    | RunInterruptedEvent
    | PhaseStartedEvent
    | PhaseCompletedEvent
    | AgentStartedEvent
    | AgentFinishedEvent
    | AgentTextEvent
    | AgentThinkingEvent
    | AgentToolCallEvent
    | AgentToolResultEvent
    | AgentUsageEvent
    | HandoffEvent
    | ArtifactCreatedEvent
    | ArtifactRevisedEvent
    | GateOpenedEvent
    | GateResolvedEvent
    | LoopIterationEvent
    | GuardrailBlockedEvent
    | BudgetUpdatedEvent
    | MergeStartedEvent
    | MergeConflictEvent
    | MergeCompletedEvent
    | GitPushedEvent
    | PrOpenedEvent
    | PrReadyEvent
    | CiStatusEvent,
    Field(discriminator="type"),
]

EVENT_ADAPTER: TypeAdapter[RunEvent] = TypeAdapter(RunEvent)
EVENT_CLASSES: tuple[type[EventBase], ...] = get_args(get_args(RunEvent)[0])
# Only these end a run; run.interrupted is resumable, so streams stay open across it.
TERMINAL_EVENT_TYPES = frozenset({"run.completed", "run.failed", "run.aborted"})


def _payload_index() -> dict[str, type[ContractModel]]:
    index: dict[str, type[ContractModel]] = {}
    for cls in EVENT_CLASSES:
        (event_type,) = get_args(cls.model_fields["type"].annotation)
        payload = cls.model_fields["payload"].annotation
        assert isinstance(payload, type) and issubclass(payload, ContractModel)
        index[event_type] = payload
    return index


PAYLOAD_BY_TYPE = _payload_index()
