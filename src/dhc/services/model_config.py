"""dhc.services.model_config: per-session LLM config (ADR-0011).

The `ModelConfig` frozen dataclass holds the four knobs a real
LLM provider exposes: `temperature`, `max_tokens`, `top_p`, and
`system_prompt`. Value ranges are enforced in `__post_init__` so
the data class boundary is the single point of validation; the
HTTP layer just constructs the object and lets the constructor
raise on bad input.

`ModelConfigStore` wraps `SecretsService` and provides
`get_config` / `set_config` keyed by `session_id`. A missing
config returns a fresh `ModelConfig()` (defaults), so the
routes never 404.

The on-disk format is a JSON object encrypted with the v0x02
envelope (ADR-0010). The key naming is `model_config_{session_id}`.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from dhc.cordis.secrets import SecretsService


@dataclass(frozen=True)
class ModelConfig:
    """Per-session LLM configuration.

    Ranges:
    - `temperature ∈ [0.0, 2.0]` — most providers accept 0..2.
    - `max_tokens ∈ [1, 8192]` — chosen to match the OpenAI
      `gpt-4o-mini` ceiling and the Anthropic default.
    - `top_p ∈ [0.0, 1.0]` — nucleus sampling threshold.
    - `system_prompt: str` — no length cap; the providers enforce
      their own context-length limits downstream.
    """

    temperature: float = 0.7
    max_tokens: int = 4096
    top_p: float = 1.0
    system_prompt: str = "You are a helpful assistant."

    def __post_init__(self) -> None:
        if not (0.0 <= self.temperature <= 2.0):
            raise ValueError(
                f"temperature must be in [0.0, 2.0] (got {self.temperature})"
            )
        if not isinstance(self.max_tokens, int) or not (1 <= self.max_tokens <= 8192):
            raise ValueError(
                f"max_tokens must be an int in [1, 8192] (got {self.max_tokens!r})"
            )
        if not (0.0 <= self.top_p <= 1.0):
            raise ValueError(f"top_p must be in [0.0, 1.0] (got {self.top_p})")
        if not isinstance(self.system_prompt, str):
            raise TypeError("system_prompt must be a str")

    def to_dict(self) -> dict:
        return {
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "top_p": self.top_p,
            "system_prompt": self.system_prompt,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ModelConfig":
        return cls(
            temperature=float(d.get("temperature", 0.7)),
            max_tokens=int(d.get("max_tokens", 4096)),
            top_p=float(d.get("top_p", 1.0)),
            system_prompt=str(d.get("system_prompt", "You are a helpful assistant.")),
        )


def _key(session_id: str) -> str:
    if not isinstance(session_id, str) or not session_id:
        raise ValueError("session_id must be a non-empty string")
    return f"model_config_{session_id}"


class ModelConfigStore:
    """Read/write `ModelConfig` objects keyed by session id.

    The store is a thin wrapper over `SecretsService`; the values
    are JSON-encoded and encrypted with the v0x02 envelope.
    """

    def __init__(self, secrets: SecretsService) -> None:
        self._secrets = secrets

    def get_config(self, session_id: str) -> ModelConfig:
        """Return the config for `session_id`, or a default
        `ModelConfig()` if no config is stored."""
        # Validate up front so an empty/invalid id is a hard error
        # rather than silently returning the default.
        key = _key(session_id)
        try:
            blob = self._secrets.get_raw(key)
        except Exception:
            return ModelConfig()
        if blob is None:
            return ModelConfig()
        try:
            data = json.loads(blob.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            # Corrupt config: return the default rather than crash.
            return ModelConfig()
        return ModelConfig.from_dict(data)

    def set_config(self, session_id: str, config: ModelConfig) -> None:
        """Persist `config` for `session_id` (overwrites any prior
        value)."""
        data = json.dumps(config.to_dict(), separators=(",", ":")).encode("utf-8")
        self._secrets.put_raw(_key(session_id), data)


__all__ = ["ModelConfig", "ModelConfigStore"]
