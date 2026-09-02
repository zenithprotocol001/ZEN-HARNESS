"""v1.3.0 LLM provider abstraction (ADR-0009).

Defines the `LLMProvider` abstract base, the `RetryConfig` frozen
dataclass, and the `ProviderError` exception. Concrete clients
(OpenAI, Anthropic, OpenRouter) live in sibling modules.

The v1.2.x mock LLM is NOT a subclass of `LLMProvider`; the C7
wrapper short-circuits on model == "mock-llm/default" before
calling `provider_client_for`.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import AsyncIterator, ClassVar

from dhc.modules.c7_llm_stream_adapter.service import StreamChunk


@dataclass(frozen=True)
class RetryConfig:
    """Per ADR-0008. Defaults: 3 attempts, 1s/2s linear backoff.

    `backoff_seconds` is a tuple of length `max_attempts - 1`; the
    last attempt has no backoff after it. Defaults are linear
    (1.0, 2.0); exponential curves are equivalent for
    `max_attempts=3` so the simpler representation is used.
    """

    max_attempts: int = 3
    backoff_seconds: tuple[float, ...] = (1.0, 2.0)
    retry_on_5xx: bool = True
    retry_on_network_error: bool = True
    retry_on_4xx: bool = False
    connect_timeout_s: float = 5.0
    read_timeout_s: float = 30.0

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        if len(self.backoff_seconds) != self.max_attempts - 1:
            raise ValueError(
                f"backoff_seconds must have length max_attempts-1 "
                f"(got {len(self.backoff_seconds)} for {self.max_attempts} attempts)"
            )


class ProviderError(RuntimeError):
    """Raised on final (non-retryable) failure.

    The chat WS handler closes with code 1011 when it sees this.
    `status` is the HTTP status if known; `provider` and `model`
    are the dispatch context for log correlation.
    """

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        provider: str = "",
        model: str = "",
    ) -> None:
        super().__init__(message)
        self.status = status
        self.provider = provider
        self.model = model


class LLMProvider(ABC):
    """Abstract base for live LLM providers (ADR-0009).

    The C7 wrapper is the only caller. The mock LLM is not a
    subclass; see module docstring.
    """

    provider_name: ClassVar[str]

    @abstractmethod
    async def chat_stream(
        self,
        messages: list[dict],
        model: str,
        api_key: str,
        retry_config: RetryConfig | None = None,
    ) -> AsyncIterator[StreamChunk]:
        """Stream chat completion.

        `messages` is the OpenAI shape ([{role, content}, ...]).
        `model` is the full model id (e.g. "openai/gpt-4o-mini").
        `api_key` is the per-model secret value.

        Yields `StreamChunk` instances. The caller infers "done"
        from stream end. Must NOT raise inside the iterator for
        transient failures — the retry loop handles those and
        raises `ProviderError` only on final failure.
        """
        raise NotImplementedError
        yield  # pragma: no cover  (makes this a generator for type checkers)
