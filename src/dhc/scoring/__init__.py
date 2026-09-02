"""DHC scoring engine: multiplicative DHC-V with security hard floor."""

from dhc.scoring.scorer import (
    Finding,
    ModuleScore,
    ScoringReport,
    SecurityFloorExceeded,
    compute_dhc_v,
    score_functionality,
    score_security,
    write_report,
)

__all__ = [
    "Finding",
    "ModuleScore",
    "ScoringReport",
    "SecurityFloorExceeded",
    "compute_dhc_v",
    "score_functionality",
    "score_security",
    "write_report",
]
