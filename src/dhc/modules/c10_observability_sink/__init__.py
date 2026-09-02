"""C10 ObservabilitySink reference implementation."""

from dhc.modules.c10_observability_sink.service import ObservabilitySink, apply, scrub_pii

__all__ = ["ObservabilitySink", "apply", "scrub_pii"]
