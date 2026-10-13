import pytest
from pydantic import ValidationError

from app.events.models import (
    EVENT_ADAPTER,
    PAYLOAD_BY_TYPE,
    TERMINAL_EVENT_TYPES,
    TOOL_RESULT_LIMIT_BYTES,
    TRUNCATION_MARK,
    AgentToolResultPayload,
    AgentUsageEvent,
    Checkpoint,
)
from tests.factories import make_event

SPEC_EVENT_TYPES = {
    "run.created", "run.status_changed", "run.completed", "run.failed", "run.aborted",
    "run.interrupted", "phase.started", "phase.completed", "agent.started", "agent.finished",
    "agent.text", "agent.thinking", "agent.tool_call", "agent.tool_result", "agent.usage",
    "handoff", "artifact.created", "artifact.revised", "gate.opened", "gate.resolved",
    "loop.iteration", "guardrail.blocked", "budget.updated", "merge.started", "merge.conflict",
    "merge.completed", "git.pushed", "pr.opened", "pr.ready", "ci.status",
}  # fmt: skip

USAGE = {
    "model": "claude-sonnet-5-5",
    "input_tokens": 100,
    "output_tokens": 20,
    "cache_creation_input_tokens": 0,
    "cache_read_input_tokens": 50,
    "context_tokens": 150,
    "context_max": 200_000,
    "cost_usd": 0.001,
}


def test_contract_covers_every_spec_event_type() -> None:
    assert set(PAYLOAD_BY_TYPE) == SPEC_EVENT_TYPES
    assert TERMINAL_EVENT_TYPES <= SPEC_EVENT_TYPES


def test_discriminator_selects_the_event_class() -> None:
    event = make_event("agent.usage", USAGE, agent_id="dev-1", phase="dev")
    assert isinstance(event, AgentUsageEvent)
    assert event.payload.cache_read_input_tokens == 50


def test_json_round_trip_is_lossless() -> None:
    event = make_event("agent.usage", USAGE, seq=7)
    assert EVENT_ADAPTER.validate_json(EVENT_ADAPTER.dump_json(event)) == event


def test_unknown_event_type_is_rejected() -> None:
    with pytest.raises(ValidationError):
        make_event("agent.teleported", {})


def test_payload_must_match_type() -> None:
    with pytest.raises(ValidationError):
        make_event("agent.usage", {"from_agent": "ba", "to_agent": "architect", "summary": "x"})


def test_extra_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        make_event("agent.text", {"text": "hi", "surprise": True})


def test_seq_starts_at_one() -> None:
    with pytest.raises(ValidationError):
        make_event("agent.text", {"text": "hi"}, seq=0)


def test_small_tool_result_is_kept_whole() -> None:
    payload = AgentToolResultPayload.from_content("tu_1", "4 passed")
    assert (payload.content, payload.truncated, payload.original_size) == ("4 passed", False, None)


def test_huge_tool_result_is_truncated_with_flag() -> None:
    payload = AgentToolResultPayload.from_content("tu_1", "x" * 3_000_000, is_error=True)
    assert payload.truncated is True
    assert payload.original_size == 3_000_000
    assert payload.is_error is True
    assert len(payload.content.encode()) <= TOOL_RESULT_LIMIT_BYTES


def test_truncation_never_splits_a_multibyte_character() -> None:
    payload = AgentToolResultPayload.from_content("tu_1", "é" * 10_000)  # 20,000 bytes
    payload.content.encode("utf-8")  # must not raise
    assert payload.truncated is True
    assert set(payload.content) == {"é"}


BIG = "x" * 50_000


def test_tool_result_cap_is_enforced_by_the_type() -> None:
    event = make_event("agent.tool_result", {"tool_use_id": "t", "is_error": False, "content": BIG})
    assert event.payload.truncated is True
    assert event.payload.original_size == 50_000
    assert len(event.payload.content.encode()) <= TOOL_RESULT_LIMIT_BYTES


def test_capped_events_round_trip_unchanged() -> None:
    result = make_event(
        "agent.tool_result", {"tool_use_id": "t", "is_error": False, "content": BIG}
    )
    call = make_event(
        "agent.tool_call", {"tool_use_id": "t", "name": "Write", "input": {"content": BIG}}
    )
    for event in (result, call):
        assert EVENT_ADAPTER.validate_json(EVENT_ADAPTER.dump_json(event)) == event


def test_lone_surrogates_are_replaced_instead_of_raising() -> None:
    payload = AgentToolResultPayload.from_content("t", "ok \ud83d tail")
    assert payload.content == "ok ? tail"
    EVENT_ADAPTER.dump_json(make_event("agent.tool_result", payload.model_dump()))


def test_tool_call_input_strings_are_capped_recursively() -> None:
    event = make_event(
        "agent.tool_call",
        {
            "tool_use_id": "t",
            "name": "Edit",
            "input": {"file_path": "a.py", "edits": [{"old_string": BIG, "new_string": "z"}]},
        },
    )
    payload = event.payload
    assert payload.input_truncated is True
    capped = payload.input["edits"][0]["old_string"]
    assert capped.endswith(TRUNCATION_MARK)
    assert len(capped.encode()) <= TOOL_RESULT_LIMIT_BYTES
    assert payload.input["file_path"] == "a.py"
    assert payload.input["edits"][0]["new_string"] == "z"


def test_small_tool_call_is_untouched() -> None:
    event = make_event(
        "agent.tool_call", {"tool_use_id": "t", "name": "Bash", "input": {"command": "ls"}}
    )
    assert event.payload.input == {"command": "ls"}
    assert event.payload.input_truncated is False


def test_interrupted_carries_a_checkpoint_and_gates_list_their_actions() -> None:
    event = make_event("run.interrupted", {"last_checkpoint": "design_drafted"})
    assert event.payload.last_checkpoint is Checkpoint.DESIGN_DRAFTED  # type: ignore[union-attr]
    with pytest.raises(ValidationError):
        make_event("gate.opened", {"gate_id": "g", "gate": "escalation", "actions": []})
    gate = make_event(
        "gate.opened", {"gate_id": "g", "gate": "escalation", "actions": ["approve", "abort"]}
    )
    assert gate.payload.actions == ["approve", "abort"]  # type: ignore[union-attr]
    loop = make_event("loop.iteration", {"loop": "revise", "iteration": 2, "limit": None})
    assert loop.payload.limit is None  # type: ignore[union-attr]


def test_only_completed_failed_and_aborted_end_a_run() -> None:
    assert TERMINAL_EVENT_TYPES == {"run.completed", "run.failed", "run.aborted"}
    assert list(Checkpoint)[0] is Checkpoint.CLONED and list(Checkpoint)[-1] is Checkpoint.DONE
