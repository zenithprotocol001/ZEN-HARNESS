"""v1.3.0 OpenRouter provider client (ADR-0009).

OpenRouter is OpenAI-compatible: same request/response shape, same
auth header (`Authorization: Bearer ...`). The client reuses the
OpenAI SSE parser.
"""
from __future__ import annotations

import asyncio
from typing import AsyncIterator

import httpx

from dhc.integrations.base import LLMProvider, ProviderError, RetryConfig
from dhc.integrations.openai_client import _consume_openai_sse
from dhc.modules.c7_llm_stream_adapter.service import StreamChunk

_OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


class OpenRouterClient(LLMProvider):
    provider_name = "openrouter"

    def __init__(self, base_url: str = _OPENROUTER_URL) -> None:
        self._base_url = base_url.rstrip("/")
        self._chat_url = f"{self._base_url}/api/v1/chat/completions"

    async def chat_stream(
        self,
        messages: list[dict],
        model: str,
        api_key: str,
        retry_config: RetryConfig | None = None,
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        top_p: float | None = None,
    ) -> AsyncIterator[StreamChunk]:
        rc = retry_config or RetryConfig()
        body: dict = {
            "model": model,
            "messages": list(messages),
            "stream": True,
            # v1.3.1: ask for usage. OpenRouter's OpenAI-compatible
            # endpoint honors `stream_options.include_usage` and
            # emits a usage-only chunk at the end.
            "stream_options": {"include_usage": True},
        }
        if temperature is not None:
            body["temperature"] = temperature
        if max_tokens is not None:
            body["max_tokens"] = max_tokens
        if top_p is not None:
            body["top_p"] = top_p
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
        timeout = httpx.Timeout(rc.connect_timeout_s, read=rc.read_timeout_s)

        for attempt in range(rc.max_attempts):
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    async with client.stream(
                        "POST", self._chat_url, json=body, headers=headers
                    ) as resp:
                        if 400 <= resp.status_code < 500:
                            text = (await resp.aread()).decode("utf-8", "replace")
                            raise ProviderError(
                                f"4xx error: {resp.status_code} {text[:200]}",
                                status=resp.status_code,
                                provider=self.provider_name,
                                model=model,
                            )
                        if resp.status_code >= 500:
                            if attempt < rc.max_attempts - 1 and rc.retry_on_5xx:
                                await asyncio.sleep(rc.backoff_seconds[attempt])
                                continue
                            raise ProviderError(
                                f"5xx after {rc.max_attempts} attempts: {resp.status_code}",
                                status=resp.status_code,
                                provider=self.provider_name,
                                model=model,
                            )
                        async for chunk in _consume_openai_sse(resp):
                            yield chunk
                        return
            except (httpx.RequestError, asyncio.TimeoutError) as exc:
                if not rc.retry_on_network_error:
                    raise ProviderError(
                        f"network error: {exc}",
                        provider=self.provider_name,
                        model=model,
                    ) from exc
                if attempt < rc.max_attempts - 1:
                    await asyncio.sleep(rc.backoff_seconds[attempt])
                    continue
                raise ProviderError(
                    f"network error after {rc.max_attempts} attempts: {exc}",
                    provider=self.provider_name,
                    model=model,
                ) from exc
            except ProviderError:
                raise
        raise ProviderError(
            f"openrouter client exhausted retries",
            provider=self.provider_name,
            model=model,
        )
