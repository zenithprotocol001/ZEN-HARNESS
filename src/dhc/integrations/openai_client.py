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
            # v1.3.1: ask OpenAI to include the usage block on the
            # terminating chunk. The OpenAI parser surfaces it as
            # a `StreamChunk` with `usage` populated.
            "stream_options": {"include_usage": True},
        }
        # v1.3.1 (ADR-0011): only include knobs when explicitly
        # set; `None` means "use the provider default" (which we
        # leave unspecified rather than hard-code).
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

    v1.3.1: when the request body includes
    `stream_options.include_usage` (which the live OpenAI client
    sets), the final chunk has an empty `choices` array and a
    `usage` object. We surface it as a terminating `StreamChunk`
    with `delta=""` and `usage` populated.
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
                usage = obj.get("usage")
                # If the server returned usage in a choices-less
                # chunk (the v1.3.1 path), surface it as a
                # terminating chunk.
                if not choices and usage:
                    yield StreamChunk(
                        delta="",
                        finish_reason="stop",
                        raw_index=index,
                        usage={
                            "prompt_tokens": int(usage.get("prompt_tokens", 0)),
                            "completion_tokens": int(usage.get("completion_tokens", 0)),
                            "total_tokens": int(usage.get("total_tokens", 0)),
                        },
                    )
                    index += 1
                    continue
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
