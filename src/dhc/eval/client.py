"""OpenAI-compatible /v1/chat/completions client.

Works with any provider that speaks the OpenAI chat-completions
schema: OpenAI, Zhipu (GLM), DeepSeek, vLLM, Ollama (with the
OpenAI compatibility shim), etc.

Configuration is purely via constructor arguments; no global
state. The client is async (uses `httpx.AsyncClient`) so it can
be composed with the rest of the eval loop without blocking.
"""

from __future__ import annotations

import json
from typing import Any

import httpx


class LLMClientError(RuntimeError):
    """Raised for any unrecoverable LLM API failure."""


class LLMClient:
    """Minimal async client for OpenAI-compatible chat completions."""

    def __init__(
        self,
        base_url: str = "https://api.openai.com/v1",
        api_key: str = "",
        model: str = "gpt-4o-mini",
        timeout_sec: float = 60.0,
        max_retries: int = 2,
    ) -> None:
        if not api_key:
            raise LLMClientError("api_key is required")
        if not base_url:
            raise LLMClientError("base_url is required")
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._timeout = timeout_sec
        self._max_retries = max(0, int(max_retries))
        self._client = httpx.AsyncClient(
            timeout=timeout_sec,
            headers={"Authorization": f"Bearer {api_key}"},
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "LLMClient":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()

    async def chat(
        self,
        prompt: str,
        *,
        system: str = "You are a helpful coding assistant.",
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> str:
        """Send a single chat-completion request and return the assistant's text."""
        url = f"{self._base_url}/chat/completions"
        body = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "temperature": float(temperature),
            "max_tokens": int(max_tokens),
        }
        last_exc: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                resp = await self._client.post(url, json=body)
                if resp.status_code >= 500 and attempt < self._max_retries:
                    last_exc = LLMClientError(f"server {resp.status_code}: {resp.text[:200]}")
                    continue
                if resp.status_code >= 400:
                    raise LLMClientError(
                        f"client {resp.status_code}: {resp.text[:400]}"
                    )
                data = resp.json()
                return _extract_assistant_text(data)
            except httpx.TimeoutException as exc:
                last_exc = LLMClientError(f"timeout: {exc}")
                continue
            except httpx.HTTPError as exc:
                last_exc = LLMClientError(f"http: {exc}")
                continue
        raise LLMClientError(f"all retries exhausted: {last_exc}")


def _extract_assistant_text(payload: dict) -> str:
    """Pull the assistant text out of a chat-completion response.

    Supports both the standard `choices[0].message.content` shape and
    a couple of common variants (`text` instead of `content`,
    `delta` for streamed batches).
    """
    choices = payload.get("choices") or []
    if not choices:
        raise LLMClientError("response contained no choices")
    first = choices[0]
    msg = first.get("message") or first
    content = msg.get("content") or msg.get("text") or ""
    if isinstance(content, list):
        # Some providers return content as a list of segments.
        parts: list[str] = []
        for seg in content:
            if isinstance(seg, dict):
                text = seg.get("text") or ""
                if text:
                    parts.append(text)
            elif isinstance(seg, str):
                parts.append(seg)
        content = "".join(parts)
    if not isinstance(content, str):
        raise LLMClientError(f"unexpected assistant content type: {type(content).__name__}")
    return content
