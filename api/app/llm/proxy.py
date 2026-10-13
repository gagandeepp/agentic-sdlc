"""Credential-injecting, metering proxy between Claude Code sessions and the Anthropic API.

Agents get a placeholder bearer token; the proxy swaps it for the run's real key, meters usage
from the response, and enforces the run budget. Only the endpoints Claude Code needs exist.
"""

import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from app.llm.pricing import Usage, context_window, cost_usd, estimate_reservation
from app.llm.registry import BUDGET_EXHAUSTED_MESSAGE, Grant, ProxyRegistry
from app.llm.usage import SseUsageTap, usage_from_json

log = logging.getLogger(__name__)

# Never forwarded upstream: the agent's own credentials and per-hop headers.
_DROP_REQUEST_HEADERS = {
    "authorization",
    "x-api-key",
    "host",
    "content-length",
    "accept-encoding",
    "connection",
    "transfer-encoding",
    "cookie",
}
_DROP_RESPONSE_HEADERS = {
    "connection",
    "keep-alive",
    "transfer-encoding",
    "content-length",
    "content-encoding",
}


@dataclass(frozen=True)
class UsageRecord:
    model: str
    usage: Usage
    cost_usd: float
    context_tokens: int
    context_max: int
    spent_usd: float
    budget_usd: float


UsageSink = Callable[[Grant, UsageRecord], Awaitable[None]]


def _error(status: int, message: str, error_type: str = "invalid_request_error") -> JSONResponse:
    return JSONResponse(
        {"type": "error", "error": {"type": error_type, "message": message}},
        status_code=status,
        headers={"x-should-retry": "false"},
    )


def _token(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.headers.get("x-api-key")


def _model_of(body: bytes) -> str:
    try:
        payload = json.loads(body)
    except ValueError:
        return ""
    return str(payload.get("model", "")) if isinstance(payload, dict) else ""


def create_proxy_app(
    registry: ProxyRegistry, http: httpx.AsyncClient, upstream: str, on_usage: UsageSink
) -> Starlette:
    async def settle(grant: Grant, model: str, raw_usage: dict[str, Any], reserved: float) -> None:
        # The run may have been revoked while this stream was finishing.
        budget = registry.budget(grant.run_id) if registry.is_open(grant.run_id) else None
        usage = Usage.from_api(raw_usage)
        cost = cost_usd(registry.pricing, model, usage) if raw_usage else 0.0
        if budget is not None:
            budget.settle(reserved, cost)
        if not raw_usage or budget is None:
            return
        record = UsageRecord(
            model=model,
            usage=usage,
            cost_usd=cost,
            context_tokens=usage.prompt_tokens,
            context_max=context_window(registry.pricing, model),
            spent_usd=budget.spent_usd,
            budget_usd=budget.budget_usd,
        )
        try:
            await on_usage(grant, record)
        except Exception:
            log.exception("could not record usage for %s/%s", grant.run_id, grant.agent_id)

    async def finish_stream(
        response: httpx.Response,
        grant: Grant,
        tap: SseUsageTap,
        model: str,
        reserved: float,
        body_bytes: int,
    ) -> None:
        await response.aclose()  # also stops generation if the agent hung up early
        raw_usage = tap.usage or {
            # The agent hung up before the usage header arrived. Anthropic bills the prompt
            # either way, so charge an estimate (about 4 bytes per token) rather than nothing.
            "input_tokens": body_bytes // 4
        }
        await settle(grant, tap.model or model, raw_usage, reserved)

    async def hello(_: Request) -> Response:
        return Response(status_code=200)

    async def not_found(_: Request) -> Response:
        return _error(404, "not available through the agentic-sdlc proxy", "not_found_error")

    async def forward(request: Request, *, metered: bool) -> Response:
        grant = registry.lookup(_token(request))
        if grant is None:
            return _error(401, "unknown or revoked agent token", "authentication_error")
        body = await request.body()
        model = _model_of(body)
        if model not in grant.allowed_models:
            return _error(400, f"model {model!r} is not allowed for this agent")
        budget = registry.budget(grant.run_id)
        reserved = estimate_reservation(registry.pricing, model, len(body)) if metered else 0.0
        if metered and not budget.try_reserve(reserved):
            return _error(400, BUDGET_EXHAUSTED_MESSAGE)
        headers = {
            name: value
            for name, value in request.headers.items()
            if name.lower() not in _DROP_REQUEST_HEADERS
        }
        headers["x-api-key"] = registry.api_key(grant.run_id).get_secret_value()
        headers["accept-encoding"] = "identity"
        registry.begin(grant.token)
        try:
            upstream_response = await http.send(
                http.build_request(
                    "POST",
                    f"{upstream}{request.url.path}",
                    params=request.query_params,
                    headers=headers,
                    content=body,
                ),
                stream=True,
            )
        except httpx.HTTPError as exc:
            budget.release(reserved)
            registry.end(grant.token)
            message = f"upstream unavailable: {type(exc).__name__}"
            return JSONResponse(
                {"type": "error", "error": {"type": "api_error", "message": message}},
                status_code=502,
            )
        response_headers = {
            name: value
            for name, value in upstream_response.headers.items()
            if name.lower() not in _DROP_RESPONSE_HEADERS
        }
        content_type = upstream_response.headers.get("content-type", "")
        streaming = "text/event-stream" in content_type
        if not metered or upstream_response.status_code != 200 or not streaming:
            content = await upstream_response.aread()
            await upstream_response.aclose()
            if metered and upstream_response.status_code == 200:
                json_model, raw_usage = usage_from_json(content)
                registry.spawn(grant.token, settle(grant, json_model or model, raw_usage, reserved))
            else:
                budget.release(reserved)
                registry.end(grant.token)
            return Response(
                content, status_code=upstream_response.status_code, headers=response_headers
            )

        tap = SseUsageTap()

        async def stream() -> AsyncIterator[bytes]:
            try:
                async for chunk in upstream_response.aiter_raw():
                    tap.feed(chunk)
                    yield chunk
            finally:
                # Runs on completion and on client disconnect. A cancelled response can't
                # await, so settling happens in a tracked task.
                registry.spawn(
                    grant.token,
                    finish_stream(upstream_response, grant, tap, model, reserved, len(body)),
                )

        return StreamingResponse(stream(), status_code=200, headers=response_headers)

    async def messages(request: Request) -> Response:
        return await forward(request, metered=True)

    async def count_tokens(request: Request) -> Response:
        return await forward(request, metered=False)

    return Starlette(
        routes=[
            Route("/v1/messages", messages, methods=["POST"]),
            Route("/v1/messages/count_tokens", count_tokens, methods=["POST"]),
            Route("/api/hello", hello, methods=["GET", "HEAD"]),
            Route(
                "/{path:path}",
                not_found,
                methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"],
            ),
        ]
    )
