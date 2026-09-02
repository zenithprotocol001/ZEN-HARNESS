"""C5 AgentRegistry reference implementation."""

from dhc.modules.c5_agent_registry.service import (
    AgentManifest,
    AgentRegistry,
    UnauthorizedScope,
    apply,
    compute_signature,
    verify_signature,
)

__all__ = [
    "AgentManifest",
    "AgentRegistry",
    "UnauthorizedScope",
    "apply",
    "compute_signature",
    "verify_signature",
]
