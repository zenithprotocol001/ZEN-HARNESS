"""v1.3.0 Anthropic provider client (ADR-0009).

POSTs to `https://api.anthropic.com/v1/messages` with
`x-api-key: {api_key}` and `anthropic-version: 2023-06-01`. The
response is Anthropic SSE which is a sequence of typed events
(`message_start`, `content_block_start`, `content_block_delta`,
`content_block_stop`, `message_stop`).

Anthropic-specific status codes:
- 529 = overloaded (treated as 5xx for retry).
- 4xx = bad request (do not retry).
"""
from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator

import httpx

from dhc.integrations.base import LLMProvider, ProviderError, RetryConfig
from dhc.modules.c7_llm_stream_adapter.service import StreamChunk

_ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
_ANTHROPIC_VERSION = "2023-06-01"
_DEFAULT_TEMPERATURE = 0.7
_DEFAULT_MAX_TOKENS = 4096


class AnthropicClient(LLMProvider):
    provider_name = "anthropic"

    def __init__(self, base_url: str = _ANTHROPIC_URL) -> None:
        self._base_url = base_url.rstrip("/")
        self._messages_url = f"{self._base_url}/v1/messages"

    async def chat_stream(
        self,
        messages: list[dict],
        model: str,
        api_key: str,
        retry_config: RetryConfig | None = None,
    ) -> AsyncIterator[StreamChunk]:
        rc = retry_config or RetryConfig()
        # Anthropic expects the system message separately.
        system_text = ""
        chat_messages: list[dict] = []
        for m in messages:
            if m.get("role") == "system":
                system_text += (m.get("content") or "")
            else:
                chat_messages.append(m)
        body: dict = {
            "model": model,
            "messages": chat_messages,
            "max_tokens": _DEFAULT_MAX_TOKENS,
            "temperature": _DEFAULT_TEMPERATURE,
            "stream": True,
        }
        if system_text:
            body["system"] = system_text
        headers = {
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": _ANTHROPIC_VERSION,
        }
        timeout = httpx.Timeout(rc.connect_timeout_s, read=rc.read_timeout_s)

        for attempt in range(rc.max_attempts):
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    async with client.stream(
                        "POST", self._messages_url, json=body, headers=headers
                    ) as resp:
                        if 400 <= resp.status_code < 500:
                            text = (await resp.aread()).decode("utf-8", "replace")
                            raise ProviderError(
                                f"4xx error: {resp.status_code} {text[:200]}",
                                status=resp.status_code,
                                provider=self.provider_name,
                                model=model,
                            )
                        if resp.status_code >= 500 or resp.status_code == 529:
                            if attempt < rc.max_attempts - 1 and rc.retry_on_5xx:
                                await asyncio.sleep(rc.backoff_seconds[attempt])
                                continue
                            raise ProviderError(
                                f"5xx after {rc.max_attempts} attempts: {resp.status_code}",
                                status=resp.status_code,
                                provider=self.provider_name,
                                model=model,
                            )
                        async for chunk in _consume_anthropic_sse(resp):
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
            f"anthropic client exhausted retries",
            provider=self.provider_name,
            model=model,
        )


async def _consume_anthropic_sse(resp: httpx.Response) -> AsyncIterator[StreamChunk]:
    """Parse Anthropic SSE.

    Events (one per `event: <type>` line, payload in `data:` line):
    - `message_start`:     {message: {id, ...}}      — initial
    - `content_block_start`: {index, content_block: {type, ...}}
    - `content_block_delta`: {index, delta: {type: "text_delta", text: "..."} | {type: "input_json_delta", partial_json: "..."}}
    - `content_block_stop`:   {index}
    - `message_delta`:        {delta: {stop_reason: "end_turn"}}
    - `message_stop`:         {}
    """
    buffer = b""
    pending_event: bytes | None = None
    index = 0
    async for raw in resp.aiter_bytes():
        buffer += raw
        while b"\n\n" in buffer:
            event_block, buffer = buffer.split(b"\n\n", 1)
            event_name: bytes | None = None
            data_lines: list[bytes] = []
            for line in event_block.split(b"\n"):
                line = line.strip()
                if line.startswith(b"event:"):
                    event_name = line[len(b"event:"):].strip()
                elif line.startswith(b"data:"):
                    data_lines.append(line[len(b"data:"):].strip())
            if not data_lines:
                continue
            payload = b"\n".join(data_lines)
            try:
                obj = json.loads(payload)
            except json.JSONDecodeError as exc:
                raise ProviderError(
                    f"anthropic sse: bad json: {exc}",
                    provider="anthropic",
                ) from exc
            evt = (event_name or b"").decode("ascii", "replace")
            if evt == "content_block_delta":
                delta_obj = obj.get("delta") or {}
                d_type = delta_obj.get("type")
                if d_type == "text_delta":
                    text = delta_obj.get("text") or ""
                    if text:
                        yield StreamChunk(
                            delta=text, raw_index=index,
                        )
                        index += 1
                elif d_type == "input_json_delta":
                    # Tool use partial JSON; surface as a tool_call.
                    yield StreamChunk(
                        delta="",
                        tool_calls=[{
                            "index": obj.get("index", 0),
                            "function": {
                                "arguments": delta_obj.get("partial_json", ""),
                            },
                        }],
                        raw_index=index,
                    )
                    index += 1
            elif evt == "message_delta":
                stop = (obj.get("delta") or {}).get("stop_reason")
                if stop:
                    yield StreamChunk(
                        delta="", finish_reason=stop, raw_index=index,
                    )
                    index += 1
            elif evt == "message_stop":
                return
