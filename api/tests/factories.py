from datetime import UTC, datetime
from typing import Any

from app.events.models import EVENT_ADAPTER, RunEvent


def make_event(
    event_type: str,
    payload: dict[str, Any],
    *,
    run_id: str = "run_test",
    seq: int = 1,
    phase: str | None = None,
    agent_id: str | None = None,
) -> RunEvent:
    return EVENT_ADAPTER.validate_python(
        {
            "run_id": run_id,
            "seq": seq,
            "ts": datetime.now(UTC),
            "type": event_type,
            "phase": phase,
            "agent_id": agent_id,
            "payload": payload,
        }
    )
