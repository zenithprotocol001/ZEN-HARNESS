"""Mock LLM SSE server fixtures + scripted JSONL scenarios."""

from dhc.fixtures.mock_llm.scripts import (
    FRAGMENTED_CHUNKS,
    HAPPY_3_STEP,
    INFINITE_NEED_MORE_INFO,
    TAMPERED_HMAC_BODY,
    TAMPERED_HMAC_DIGEST,
    TAMPERED_HMAC_TIMESTAMP,
    VALID_HMAC_BODY,
    VALID_HMAC_DIGEST,
    VALID_HMAC_NONCE,
    VALID_HMAC_TIMESTAMP,
)
from dhc.fixtures.mock_llm.server import MockLLMServer, create_app

__all__ = [
    "MockLLMServer",
    "create_app",
    "HAPPY_3_STEP",
    "FRAGMENTED_CHUNKS",
    "INFINITE_NEED_MORE_INFO",
    "VALID_HMAC_BODY",
    "VALID_HMAC_DIGEST",
    "VALID_HMAC_NONCE",
    "VALID_HMAC_TIMESTAMP",
    "TAMPERED_HMAC_BODY",
    "TAMPERED_HMAC_DIGEST",
    "TAMPERED_HMAC_TIMESTAMP",
]
