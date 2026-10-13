import httpx
from fastapi import FastAPI

from app.agents.runner import SdkAgentRunner
from app.llm.registry import ProxyRegistry


async def test_app_starts_the_proxy_on_loopback(app: FastAPI) -> None:
    assert isinstance(app.state.registry, ProxyRegistry)
    assert isinstance(app.state.runner, SdkAgentRunner)
    assert app.state.proxy.base_url.startswith("http://127.0.0.1:")
    async with httpx.AsyncClient(base_url=app.state.proxy.base_url) as client:
        assert (await client.get("/api/hello")).status_code == 200
