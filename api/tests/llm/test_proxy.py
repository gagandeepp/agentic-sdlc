from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx
import pytest
from pydantic import SecretStr

from app.events.models import Phase
from app.llm.pricing import Usage, cost_usd, load_pricing
from app.llm.proxy import UsageRecord, create_proxy_app
from app.llm.registry import Grant, ProxyRegistry
from app.llm.server import EmbeddedServer
from tests.fakes.upstream import FakeUpstream, error_reply, json_reply, text_reply

REAL_KEY = "sk-ant-test-REALKEY"
SONNET = "claude-sonnet-5-5"
BODY = {
    "model": SONNET,
    "max_tokens": 128000,
    "stream": True,
    "messages": [{"role": "user", "content": "hi"}],
}


@dataclass
class Env:
    upstream: FakeUpstream
    registry: ProxyRegistry
    grant: Grant
    records: list[tuple[Grant, UsageRecord]]
    client: httpx.AsyncClient


@pytest.fixture
async def env() -> AsyncIterator[Env]:
    async with FakeUpstream() as upstream, httpx.AsyncClient() as http:
        registry = ProxyRegistry(load_pricing())
        registry.open_run("run_1", SecretStr(REAL_KEY), budget_usd=5.0)
        grant = registry.grant(
            run_id="run_1", agent_id="ba", phase=Phase.BA, allowed_models={SONNET}
        )
        records: list[tuple[Grant, UsageRecord]] = []

        async def sink(g: Grant, record: UsageRecord) -> None:
            records.append((g, record))

        proxy = EmbeddedServer(create_proxy_app(registry, http, upstream.base_url, sink))
        await proxy.start()
        headers = {
            "Authorization": f"Bearer {grant.token}",
            "anthropic-beta": "claude-code-20250219",
        }
        async with httpx.AsyncClient(base_url=proxy.base_url, headers=headers) as client:
            yield Env(upstream, registry, grant, records, client)
        await proxy.stop()
        await registry.aclose()


async def test_hello_is_answered_locally(env: Env) -> None:
    assert (await env.client.head("/api/hello")).status_code == 200
    assert (await env.client.get("/api/hello")).status_code == 200
    assert env.upstream.requests == []


async def test_other_api_routes_are_not_forwarded(env: Env) -> None:
    assert (await env.client.post("/v1/messages/batches", json=BODY)).status_code == 404
    assert (await env.client.get("/v1/files")).status_code == 404
    assert env.upstream.requests == []


async def test_unknown_token_is_rejected(env: Env) -> None:
    response = await env.client.post(
        "/v1/messages", json=BODY, headers={"Authorization": "Bearer agt_nope"}
    )
    assert response.status_code == 401
    assert env.upstream.requests == []


async def test_models_outside_the_grant_are_rejected(env: Env) -> None:
    response = await env.client.post("/v1/messages", json={**BODY, "model": "claude-opus-5-5"})
    assert response.status_code == 400
    assert response.headers["x-should-retry"] == "false"
    assert env.upstream.requests == []


async def test_stream_passes_through_with_the_real_key_and_is_metered(env: Env) -> None:
    response = await env.client.post("/v1/messages", params={"beta": "true"}, json=BODY)
    assert response.status_code == 200
    assert b"message_stop" in response.content
    sent = env.upstream.requests[0]
    assert sent.headers["x-api-key"] == REAL_KEY
    assert "authorization" not in sent.headers
    assert sent.query == "beta=true"
    assert sent.headers["anthropic-beta"] == "claude-code-20250219"
    assert REAL_KEY not in str(response.headers)

    await env.registry.drain_and_revoke(env.grant.token)
    [(grant, record)] = env.records
    expected = cost_usd(
        load_pricing(),
        SONNET,
        Usage(input_tokens=1000, cache_read_input_tokens=500, output_tokens=200),
    )
    assert grant.agent_id == "ba"
    assert (record.usage.input_tokens, record.usage.output_tokens) == (1000, 200)
    assert record.cost_usd == pytest.approx(expected)
    assert record.context_tokens == 1500
    assert record.context_max == 1_000_000
    budget = env.registry.budget("run_1")
    assert budget.spent_usd == pytest.approx(expected)
    assert budget.reserved_usd == pytest.approx(0.0)


async def test_events_split_across_tiny_chunks_are_still_metered(env: Env) -> None:
    env.upstream.replies.append(text_reply("split", chunk_size=7))
    await env.client.post("/v1/messages", json=BODY)
    await env.registry.drain_and_revoke(env.grant.token)
    assert env.records[0][1].usage.output_tokens == 200


async def test_budget_exhaustion_blocks_before_anything_is_forwarded(env: Env) -> None:
    env.registry.open_run("run_poor", SecretStr(REAL_KEY), budget_usd=0.000001)
    poor = env.registry.grant(
        run_id="run_poor", agent_id="dev", phase=Phase.DEV, allowed_models={SONNET}
    )
    response = await env.client.post(
        "/v1/messages", json=BODY, headers={"Authorization": f"Bearer {poor.token}"}
    )
    assert response.status_code == 400
    assert response.headers["x-should-retry"] == "false"
    assert "budget exhausted" in response.text
    assert env.registry.budget("run_poor").exhausted
    assert env.upstream.requests == []


async def test_upstream_errors_pass_through_unmetered(env: Env) -> None:
    env.upstream.replies.append(error_reply(429, {"retry-after": "3"}))
    response = await env.client.post("/v1/messages", json=BODY)
    assert response.status_code == 429
    assert response.headers["retry-after"] == "3"
    await env.registry.drain_and_revoke(env.grant.token)
    assert env.records == []
    assert env.registry.budget("run_1").reserved_usd == pytest.approx(0.0)


async def _hang_up_after_first_chunk(env: Env) -> None:
    async with env.client.stream("POST", "/v1/messages", json=BODY) as response:
        async for _ in response.aiter_raw():
            break  # read one chunk, then hang up
    await env.registry.drain_and_revoke(env.grant.token)


async def test_disconnect_after_the_usage_header_charges_exact_usage(env: Env) -> None:
    # The first chunk holds the whole message_start event.
    env.upstream.replies.append(text_reply("slow", chunk_size=600, delay=0.05))
    await _hang_up_after_first_chunk(env)
    [(_, record)] = env.records
    assert record.usage.input_tokens == 1000
    assert env.registry.budget("run_1").reserved_usd == pytest.approx(0.0)


async def test_disconnect_before_any_usage_still_charges_the_prompt_estimate(env: Env) -> None:
    env.upstream.replies.append(text_reply("slow", chunk_size=20, delay=0.05))
    await _hang_up_after_first_chunk(env)
    [(_, record)] = env.records
    assert record.usage.input_tokens > 0  # estimated from the request size, never free
    assert record.cost_usd > 0
    assert env.registry.budget("run_1").reserved_usd == pytest.approx(0.0)


async def test_count_tokens_is_forwarded_but_not_charged(env: Env) -> None:
    response = await env.client.post(
        "/v1/messages/count_tokens", json={"model": SONNET, "messages": []}
    )
    assert response.json() == {"input_tokens": 42}
    await env.registry.drain_and_revoke(env.grant.token)
    assert env.records == []


async def test_non_streaming_json_responses_are_metered(env: Env) -> None:
    env.upstream.replies.append(json_reply(output_tokens=9))
    response = await env.client.post("/v1/messages", json={**BODY, "stream": False})
    assert response.json()["usage"]["output_tokens"] == 9
    await env.registry.drain_and_revoke(env.grant.token)
    assert env.records[0][1].usage.output_tokens == 9
