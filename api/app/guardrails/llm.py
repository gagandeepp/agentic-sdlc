"""A NeMo Guardrails LLM backend that calls Claude through the run's metering proxy.

NeMo 0.24's default framework speaks only OpenAI-compatible APIs. This adapter implements its
LLMModel protocol against the Anthropic Messages API instead, so a guardrails check is billed to
the run like any agent and shows up in the GUI as the `guardrails` agent.
"""

from collections.abc import AsyncIterator
from typing import Any

import httpx
from nemoguardrails.types import LLMResponse, LLMResponseChunk

from app.agents.runner import HAIKU_MODEL

ANTHROPIC_VERSION = "2023-06-01"


class ProxyLLMError(RuntimeError):
    pass


def _messages(prompt: Any) -> tuple[str | None, list[dict[str, str]]]:
    if isinstance(prompt, str):
        return None, [{"role": "user", "content": prompt}]
    system: list[str] = []
    messages: list[dict[str, str]] = []
    for message in prompt:
        role = str(getattr(message.role, "value", message.role))
        content = (
            message.content if isinstance(message.content, str) else str(message.content or "")
        )
        if role == "system":
            system.append(content)
        else:
            messages.append(
                {"role": "assistant" if role == "assistant" else "user", "content": content}
            )
    return ("\n\n".join(system) or None), messages


class ProxyLLM:
    def __init__(
        self, http: httpx.AsyncClient, base_url: str, token: str, model: str = HAIKU_MODEL
    ) -> None:
        self._http = http
        self._base_url = base_url
        self._token = token
        self._model = model

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def provider_name(self) -> str:
        return "anthropic"

    @property
    def provider_url(self) -> str:
        return self._base_url

    async def generate_async(
        self, prompt: Any, *, stop: list[str] | None = None, **kwargs: Any
    ) -> Any:
        system, messages = _messages(prompt)
        body: dict[str, Any] = {
            "model": self._model,
            "max_tokens": int(kwargs.get("max_tokens") or 16),
            "messages": messages,
        }
        if system:
            body["system"] = system
        if kwargs.get("temperature") is not None:
            body["temperature"] = float(kwargs["temperature"])
        stops = [s for s in stop or [] if s.strip()]
        if stops:
            body["stop_sequences"] = stops
        headers = {"x-api-key": self._token, "anthropic-version": ANTHROPIC_VERSION}
        try:
            response = await self._http.post(
                f"{self._base_url}/v1/messages", json=body, headers=headers, timeout=60.0
            )
        except httpx.HTTPError as exc:
            raise ProxyLLMError(f"guardrails LLM call failed: {type(exc).__name__}") from exc
        if response.status_code != 200:
            raise ProxyLLMError(f"guardrails LLM call failed with HTTP {response.status_code}")
        data = response.json()
        text = "".join(
            block.get("text", "")
            for block in data.get("content", [])
            if block.get("type") == "text"
        )
        return LLMResponse(content=text, model=data.get("model"))

    async def stream_async(
        self, prompt: Any, *, stop: list[str] | None = None, **kwargs: Any
    ) -> AsyncIterator[Any]:
        response = await self.generate_async(prompt, stop=stop, **kwargs)
        yield LLMResponseChunk(delta_content=response.content)
