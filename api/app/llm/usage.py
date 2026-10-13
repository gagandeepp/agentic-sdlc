"""Usage extraction from Anthropic Messages API responses (SSE stream or JSON body)."""

import json
from typing import Any


class SseUsageTap:
    """Fed the raw SSE bytes as they pass through; keeps the model and the running usage.

    `message_start` carries input/cache usage; `message_delta` carries cumulative output (and
    sometimes updated input) counts, so delta values overlay, never add to, earlier ones.
    """

    def __init__(self) -> None:
        self._buffer = b""
        self.model: str | None = None
        self.usage: dict[str, Any] = {}

    def feed(self, chunk: bytes) -> None:
        self._buffer += chunk.replace(b"\r\n", b"\n")
        while b"\n\n" in self._buffer:
            block, self._buffer = self._buffer.split(b"\n\n", 1)
            self._handle(block)

    def _handle(self, block: bytes) -> None:
        data = b"\n".join(
            line[5:].strip() for line in block.split(b"\n") if line.startswith(b"data:")
        )
        if not data:
            return
        try:
            payload = json.loads(data)
        except ValueError:
            return
        if not isinstance(payload, dict):
            return
        if payload.get("type") == "message_start":
            message = payload.get("message") or {}
            self.model = message.get("model") or self.model
            self._overlay(message.get("usage") or {})
        elif payload.get("type") == "message_delta":
            self._overlay(payload.get("usage") or {})

    def _overlay(self, usage: dict[str, Any]) -> None:
        for key, value in usage.items():
            if value is not None:
                self.usage[key] = value


def usage_from_json(body: bytes) -> tuple[str | None, dict[str, Any]]:
    try:
        payload = json.loads(body)
    except ValueError:
        return None, {}
    if not isinstance(payload, dict):
        return None, {}
    return payload.get("model"), dict(payload.get("usage") or {})
