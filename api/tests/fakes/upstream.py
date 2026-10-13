"""A scriptable stand-in for api.anthropic.com, served on a loopback port."""

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from app.llm.server import EmbeddedServer

Reply = Callable[[str], Response]


@dataclass
class Recorded:
    method: str
    path: str
    query: str
    headers: dict[str, str]
    body: dict[str, Any] = field(default_factory=dict)


def sse_events(
    model: str,
    blocks: list[dict[str, Any]],
    stop_reason: str,
    *,
    input_tokens: int = 1000,
    cache_read: int = 500,
    output_tokens: int = 200,
) -> bytes:
    start_usage = {
        "input_tokens": input_tokens,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": cache_read,
        "output_tokens": 1,
    }
    events: list[tuple[str, dict[str, Any]]] = [
        (
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": "msg_fake",
                    "type": "message",
                    "role": "assistant",
                    "model": model,
                    "content": [],
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": start_usage,
                },
            },
        )
    ]
    for index, block in enumerate(blocks):
        if block["type"] == "text":
            start = {"type": "text", "text": ""}
            delta = {"type": "text_delta", "text": block["text"]}
        else:
            start = {"type": "tool_use", "id": block["id"], "name": block["name"], "input": {}}
            delta = {"type": "input_json_delta", "partial_json": json.dumps(block["input"])}
        events.append(
            (
                "content_block_start",
                {"type": "content_block_start", "index": index, "content_block": start},
            )
        )
        events.append(
            ("content_block_delta", {"type": "content_block_delta", "index": index, "delta": delta})
        )
        events.append(("content_block_stop", {"type": "content_block_stop", "index": index}))
    events.append(
        (
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": stop_reason, "stop_sequence": None},
                "usage": {"output_tokens": output_tokens},
            },
        )
    )
    events.append(("message_stop", {"type": "message_stop"}))
    return b"".join(
        f"event: {name}\ndata: {json.dumps(data)}\n\n".encode() for name, data in events
    )


def _stream(body: bytes, chunk_size: int, delay: float) -> StreamingResponse:
    async def chunks() -> AsyncIterator[bytes]:
        for start in range(0, len(body), chunk_size):
            if delay:
                await asyncio.sleep(delay)
            yield body[start : start + chunk_size]

    return StreamingResponse(chunks(), media_type="text/event-stream")


def text_reply(
    text: str = "done", *, chunk_size: int = 1 << 20, delay: float = 0.0, **usage: int
) -> Reply:
    def reply(model: str) -> Response:
        body = sse_events(model, [{"type": "text", "text": text}], "end_turn", **usage)
        return _stream(body, chunk_size, delay)

    return reply


def tool_reply(tool_id: str, name: str, tool_input: dict[str, Any], **usage: int) -> Reply:
    blocks = [{"type": "tool_use", "id": tool_id, "name": name, "input": tool_input}]

    def reply(model: str) -> Response:
        return _stream(sse_events(model, blocks, "tool_use", **usage), 1 << 20, 0.0)

    return reply


def error_reply(status: int, headers: dict[str, str] | None = None) -> Reply:
    body = {"type": "error", "error": {"type": "rate_limit_error", "message": "slow down"}}

    def reply(model: str) -> Response:
        return JSONResponse(body, status_code=status, headers=headers or {})

    return reply


def json_reply(output_tokens: int = 9, text: str = "hi") -> Reply:
    def reply(model: str) -> Response:
        return JSONResponse(
            {
                "id": "msg_json",
                "type": "message",
                "model": model,
                "content": [{"type": "text", "text": text}],
                "usage": {"input_tokens": 40, "output_tokens": output_tokens},
            }
        )

    return reply


class FakeUpstream:
    """Each POST /v1/messages pops the next scripted reply (default: a short text answer)."""

    def __init__(self) -> None:
        self.requests: list[Recorded] = []
        self.replies: list[Reply] = []
        routes = [Route("/{path:path}", self._handle, methods=["GET", "POST", "HEAD"])]
        self._server = EmbeddedServer(Starlette(routes=routes))

    @property
    def base_url(self) -> str:
        return self._server.base_url

    async def __aenter__(self) -> "FakeUpstream":
        await self._server.start()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._server.stop()

    async def _handle(self, request: Request) -> Response:
        raw = await request.body()
        body = json.loads(raw) if raw else {}
        self.requests.append(
            Recorded(
                request.method, request.url.path, request.url.query, dict(request.headers), body
            )
        )
        if request.url.path.endswith("/count_tokens"):
            return JSONResponse({"input_tokens": 42})
        reply = self.replies.pop(0) if self.replies else text_reply()
        return reply(str(body.get("model", "")))
