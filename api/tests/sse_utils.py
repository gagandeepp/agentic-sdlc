import json
from typing import Any


def parse_sse(body: str) -> list[tuple[int, dict[str, Any]]]:
    """Return (id, data) for every SSE message block; comments and retry lines are skipped."""
    messages: list[tuple[int, dict[str, Any]]] = []
    for block in body.split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line)
        if "id" in fields and "data" in fields:
            messages.append((int(fields["id"]), json.loads(fields["data"])))
    return messages
