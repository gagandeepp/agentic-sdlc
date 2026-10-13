from typing import Any

from app.events.models import EVENT_CLASSES
from app.schema import api_json_schema


def _walk(node: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(node, dict):
        found.append(node)
        for value in node.values():
            found.extend(_walk(value))
    elif isinstance(node, list):
        for value in node:
            found.extend(_walk(value))
    return found


def test_schema_exposes_run_event_union_and_run_record() -> None:
    schema = api_json_schema()
    defs = schema["$defs"]
    assert schema["properties"]["event"] == {"$ref": "#/$defs/RunEvent"}
    assert schema["properties"]["run"] == {"$ref": "#/$defs/RunRecord"}
    assert schema["properties"]["artifact"] == {"$ref": "#/$defs/ArtifactRecord"}
    assert len(defs["RunEvent"]["oneOf"]) == len(EVENT_CLASSES) == 30
    assert {"RunStatus", "Phase", "AgentUsageEvent", "AgentUsagePayload"} <= set(defs)


def test_every_event_requires_its_literal_type_and_defaulted_fields() -> None:
    defs = api_json_schema()["$defs"]
    for cls in EVENT_CLASSES:
        event_schema = defs[cls.__name__]
        assert "const" in event_schema["properties"]["type"]
        assert {"type", "payload", "phase", "agent_id"} <= set(event_schema["required"])


def test_property_titles_are_stripped_but_title_fields_survive() -> None:
    schema = api_json_schema()
    for node in _walk(schema):
        for prop in node.get("properties", {}).values():
            assert "title" not in prop
    assert "title" in schema["$defs"]["ArtifactPayload"]["properties"]
