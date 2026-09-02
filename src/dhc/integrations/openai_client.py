"""v1.3.0 OpenAI provider client (ADR-0009).

POSTs to `https://api.openai.com/v1/chat/completions` with
`Authorization: Bearer {api_key}`. The response is OpenAI SSE; the
existing C7 `_consume_sse` is reused for parsing.

Retry policy: per ADR-0008 (3 attempts, 1s/2s linear, 5xx + net
only). The retry loop is implemented inline (per ADR-0008 §
"Consequences" — duplication is preferred over hiding the control
flow).
"""
from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator

import httpx

from dhc.integrations.base import LLMProvider, ProviderError, RetryConfig
from dhc.modules.c7_llm_stream_adapter.service import StreamChunk

_OPENAI_URL = "https://api.openai.com/v1/chat/completions"
_DEFAULT_TEMPERATURE = 0.7
_DEFAULT_MAX_TOKENS = 4096


class OpenAIClient(LLMProvider):
    provider_name = "openai"

    def __init__(self, base_url: str = _OPENAI_URL) -> None:
        # `base_url` is overridable for tests; production code uses
        # the module-level constant.
        self._base_url = base_url.rstrip("/")
        self._chat_url = f"{self._base_url}/v1/chat/completions"

    async def chat_stream(
        self,
        messages: list[dict],
        model: str,
        api_key: str,
        retry_config: RetryConfig | None = None,
    ) -> AsyncIterator[StreamChunk]:
        rc = retry_config or RetryConfig()
        body = {
            "model": model,
            "messages": list(messages),
            "stream": True,
            "temperature": _DEFAULT_TEMPERATURE,
            "max_tokens": _DEFAULT_MAX_TOKENS,
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
        timeout = httpx.Timeout(rc.connect_timeout_s, read=rc.read_timeout_s)

        last_exc: Exception | None = None
        for attempt in range(rc.max_attempts):
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    async with client.stream(
                        "POST", self._chat_url, json=body, headers=headers
                    ) as resp:
                        if 400 <= resp.status_code < 500:
                            # 4xx: do not retry.
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
                        # 2xx: stream.
                        async for chunk in _consume_openai_sse(resp):
                            yield chunk
                        return
            except (httpx.RequestError, asyncio.TimeoutError) as exc:
                last_exc = exc
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
        # Unreachable, but be defensive.
        raise ProviderError(
            f"openai client exhausted retries: {last_exc}",
            provider=self.provider_name,
            model=model,
        )


async def _consume_openai_sse(resp: httpx.Response) -> AsyncIterator[StreamChunk]:
    """Parse OpenAI SSE. Each event line is `data: {json}` or
    `data: [DONE]`. Deltas carry `choices[0].delta.content` and
    optionally `choices[0].delta.tool_calls`.
    """
    buffer = b""
    index = 0
    async for raw in resp.aiter_bytes():
        buffer += raw
        while b"\n\n" in buffer:
            event, buffer = buffer.split(b"\n\n", 1)
            for line in event.split(b"\n"):
                line = line.strip()
                if not line.startswith(b"data:"):
                    continue
                payload = line[len(b"data:"):].strip()
                if payload == b"[DONE]":
                    return
                try:
                    obj = json.loads(payload)
                except json.JSONDecodeError as exc:
                    raise ProviderError(
                        f"openai sse: bad json: {exc}",
                        provider="openai",
                    ) from exc
                choices = obj.get("choices") or []
                if not choices:
                    continue
                choice = choices[0]
                delta = (choice.get("delta") or {}).get("content") or ""
                tool_calls = (choice.get("delta") or {}).get("tool_calls") or []
                finish = choice.get("finish_reason")
                if delta or tool_calls or finish:
                    yield StreamChunk(
                        delta=delta,
                        tool_calls=tool_calls,
                        finish_reason=finish,
                        raw_index=index,
                    )
                    index += 1
