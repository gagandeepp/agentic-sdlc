"""JSON Schema for the API contract; `make types` turns it into web/src/types/api.ts.

Run: uv run python -m app.schema
"""

import json
from typing import Any

from pydantic import TypeAdapter

from app.events.models import EVENT_ADAPTER
from app.store.artifacts import ArtifactRecord
from app.store.mongo import RunRecord


def _strip_property_titles(node: Any) -> Any:
    # json-schema-to-typescript turns every property title into a named alias
    # (RunId, Seq1, Type2, ...); without titles it emits clean inline types.
    if isinstance(node, list):
        return [_strip_property_titles(item) for item in node]
    if not isinstance(node, dict):
        return node
    out = {key: _strip_property_titles(value) for key, value in node.items()}
    props = out.get("properties")
    if isinstance(props, dict):
        out["properties"] = {
            name: {k: v for k, v in prop.items() if k != "title"}
            if isinstance(prop, dict)
            else prop
            for name, prop in props.items()
        }
    return out


def api_json_schema() -> dict[str, Any]:
    refs, top = TypeAdapter.json_schemas(
        [
            ("event", "serialization", EVENT_ADAPTER),
            ("run", "serialization", TypeAdapter(RunRecord)),
            ("artifact", "serialization", TypeAdapter(ArtifactRecord)),
        ]
    )
    defs = dict(top["$defs"])
    defs["RunEvent"] = {"title": "RunEvent", **refs[("event", "serialization")]}
    schema = {
        "title": "Api",
        "type": "object",
        "additionalProperties": False,
        "required": ["event", "run", "artifact"],
        "properties": {
            "event": {"$ref": "#/$defs/RunEvent"},
            "run": refs[("run", "serialization")],
            "artifact": refs[("artifact", "serialization")],
        },
        "$defs": defs,
    }
    result: dict[str, Any] = _strip_property_titles(schema)
    return result


if __name__ == "__main__":
    print(json.dumps(api_json_schema(), indent=2, sort_keys=True))
