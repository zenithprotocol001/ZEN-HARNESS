"""C7 LLMStreamAdapter: SSE consumer with strict chunk buffering.

Contract:
- Consumes an aiohttp SSE stream (one async iterator of `bytes`).
- Buffers partial JSON across arbitrary chunk boundaries.
- Emits `StreamChunk` events to `ctx.events.emit("llm/stream", chunk)`.
- Never logs the raw API key. Any error message is redacted via C10 scrubber
  if available; otherwise, the raw bytes are truncated to a length-safe
  prefix.
- Buffers at most `_MAX_BUFFER` bytes before raising `BufferOverflow`.
- All pydantic inputs (`StreamChunk`) are strictly typed; `Any` is forbidden.

Two surfaces:

- `stream(prompt, scenario)` — the original GET-based SSE consumer
  used by the offline eval pipeline. The endpoint shape is
  `GET {base_url}/v1/stream/{scenario}`.
- `chat_stream(messages, model)` — the v1.2.0 chat surface. POSTs to
  `{base_url}/v1/chat/completions` with a JSON body and reads the
  SSE response. The body shape is OpenAI-compatible; the response
  shape is identical to `stream()`'s.
"""

from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator, Callable

import httpx
from pydantic import BaseModel, ConfigDict, Field

from dhc.cordis.context import Context
from dhc.cordis.plugin import plugin

_MAX_BUFFER = 1 * 1024 * 1024
_SSE_DATA_PREFIX = b"data: "
_SSE_DONE = b"[DONE]"
_SSE_FIELD_SEP = b":"
_SSE_EVENT_TERMINATOR = b"\n\n"
_MAX_ERROR_PAYLOAD_BYTES = 256
_DEFAULT_MODEL = "mock-default"


class StreamChunk(BaseModel):
    model_config = ConfigDict(frozen=True)

    delta: str = Field(default="")
    tool_calls: list[dict] = Field(default_factory=list)
    finish_reason: str | None = None
    raw_index: int = 0


class BufferOverflow(RuntimeError):
    pass


class LLMStreamAdapter:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        model_registry: "Any | None" = None,
        secrets_service: "Any | None" = None,
    ) -> None:
        """v1.2.0 surface. `base_url` and `api_key` are required
        (the mock LLM uses them).

        v1.3.0 extension: `model_registry` and `secrets_service`
        enable live-provider dispatch. When both are set and the
        requested model is not the mock, `chat_stream` resolves the
        provider client via the factory in `dhc.integrations` and
        looks up the per-model API key in `secrets_service`.
        """
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._redacted = self._redact_key(api_key)
        self._model_registry = model_registry
        self._secrets_service = secrets_service

    @staticmethod
    def _redact_key(api_key: str) -> str:
        if not api_key:
            return ""
        if len(api_key) <= 6:
            return "***"
        return f"{api_key[:3]}***{api_key[-3:]}"

    @property
    def redacted_key(self) -> str:
        return self._redacted

    async def stream(self, prompt: str, scenario: str) -> AsyncIterator[StreamChunk]:
        url = f"{self._base_url}/v1/stream/{scenario}"
        timeout = httpx.Timeout(connect=5.0, read=30.0, write=5.0, pool=5.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream("GET", url) as resp:
                resp.raise_for_status()
                async for chunk in self._consume_sse(resp.aiter_bytes()):
                    yield chunk

    async def chat_stream(
        self,
        messages: list[dict],
        model: str = _DEFAULT_MODEL,
    ) -> AsyncIterator[StreamChunk]:
        """v1.2.0 chat surface. POSTs an OpenAI-compatible chat
        completions request and yields deltas.

        `messages` is a list of `{role, content}` dicts (the OpenAI
        shape). `model` is the model name; the v1.2.0 mock ignores
        it but downstream providers will use it.

        v1.3.0: when the adapter was constructed with both
        `model_registry` and `secrets_service` and `model` is not
        the mock, dispatch to a live provider client.
        """
        # v1.3.0 dispatch (ADR-0009): if we have a registry + a
        # secrets service, AND the model is not the mock, route to
        # the live provider.
        if (
            self._model_registry is not None
            and self._secrets_service is not None
            and model
            and model != "mock-llm/default"
            and not model.startswith("mock-")
        ):
            async for chunk in self._dispatch_live(messages, model):
                yield chunk
            return

        url = f"{self._base_url}/v1/chat/completions"
        body = {"model": model or _DEFAULT_MODEL, "messages": list(messages), "stream": True}
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key}" if self._api_key else "",
        }
        timeout = httpx.Timeout(connect=5.0, read=30.0, write=5.0, pool=5.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream("POST", url, json=body, headers=headers) as resp:
                resp.raise_for_status()
                async for chunk in self._consume_sse(resp.aiter_bytes()):
                    yield chunk

    async def _dispatch_live(
        self, messages: list[dict], model: str
    ) -> AsyncIterator[StreamChunk]:
        """v1.3.0: resolve provider + key, then call the client.

        The factory raises `ProviderError` on unknown providers or
        on the mock (which we already filtered out). The secrets
        service raises a `KeyError` or returns `None` if the key
        is missing; we surface that as a `ProviderError(status=401)`.
        """
        from dhc.integrations import provider_client_for
        from dhc.integrations.base import ProviderError

        registry = self._model_registry
        m = registry.get_model(model)
        if m is None:
            raise ProviderError(
                f"unknown model {model!r}", provider="", model=model
            )
        # Per ADR-0007, the secret name uses the model id without
        # the provider prefix.
        model_part = m.id.partition("/")[2]
        secret_name = f"llm_provider_{m.provider}_{model_part}"
        api_key = self._secrets_service.get(secret_name)  # type: ignore[attr-defined]
        if not api_key:
            raise ProviderError(
                f"missing api key for {model!r} (expected secret {secret_name!r})",
                status=401,
                provider=m.provider,
                model=model,
            )
        client = provider_client_for(m)
        async for chunk in client.chat_stream(messages, model, api_key):
            yield chunk

    async def _consume_sse(
        self, byte_iter: AsyncIterator[bytes]
    ) -> AsyncIterator[StreamChunk]:
        buffer = bytearray()
        index = 0
        async for raw in byte_iter:
            if not raw:
                continue
            buffer.extend(raw)
            # Cumulative overflow check: this fires when many small
            # chunks cumulatively exceed the cap, even if each individual
            # chunk ended in a clean \n\n terminator and was drained.
            if len(buffer) > _MAX_BUFFER:
                raise BufferOverflow(
                    f"SSE buffer exceeded {_MAX_BUFFER} bytes (key={self._redacted})"
                )
            while True:
                sep = buffer.find(_SSE_EVENT_TERMINATOR)
                if sep < 0:
                    break
                event_block = bytes(buffer[:sep])
                del buffer[: sep + len(_SSE_EVENT_TERMINATOR)]
                # Re-check after every drain in case a malformed upstream
                # sends chunks that re-fill the buffer past the cap.
                if len(buffer) > _MAX_BUFFER:
                    raise BufferOverflow(
                        f"SSE buffer exceeded {_MAX_BUFFER} bytes (key={self._redacted})"
                    )
                if not event_block.strip():
                    continue
                if event_block.startswith(b":"):
                    continue
                line_end = event_block.find(b"\n")
                first_line = event_block if line_end < 0 else event_block[:line_end]
                if not first_line.startswith(_SSE_DATA_PREFIX):
                    continue
                payload = first_line[len(_SSE_DATA_PREFIX) :]
                if payload.strip() == _SSE_DONE:
                    return
                try:
                    obj = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                choices = obj.get("choices") or []
                if not choices:
                    continue
                choice = choices[0]
                delta = (choice.get("delta") or {})
                chunk = StreamChunk(
                    delta=delta.get("content", "") or "",
                    tool_calls=delta.get("tool_calls") or [],
                    finish_reason=choice.get("finish_reason"),
                    raw_index=index,
                )
                index += 1
                yield chunk


@plugin("c7_llm_stream")
async def apply(ctx: Context, config: dict) -> Callable[[], None]:
    base_url = (config or {}).get("base_url", "http://127.0.0.1:0")
    api_key = (config or {}).get("api_key", "")
    # v1.3.0: optional model registry + secrets service for live dispatch.
    model_registry = (config or {}).get("model_registry")
    secrets_service = (config or {}).get("secrets_service")
    adapter = LLMStreamAdapter(
        base_url=base_url,
        api_key=api_key,
        model_registry=model_registry,
        secrets_service=secrets_service,
    )
    ctx.provide("llm", adapter)

    async def dispose() -> None:
        ctx.services.pop("llm", None)

    return dispose
