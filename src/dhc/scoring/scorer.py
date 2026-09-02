"""DHC scoring engine: multiplicative DHC-V with security hard floor.

Formula (auditor-mandated, no additive scoring):
    if security_score < 50:
        dhc_v = 0.0
    else:
        dhc_v = functionality_score * (security_score / 100.0)

Functionality (0-100) weights:
    unit_pass_rate          * 40
    turn_completion_rate    * 40
    ui_streaming_fidelity   * 20

If Playwright is unavailable, `ui_streaming_fidelity` is null and
the remaining two components are re-weighted to 50/50.

Security (0-100): starts at 100 and deducts per finding.
    Critical  -100  (and forces DHC-V -> 0 immediately, independent of
                     the multiplicative path; a single critical hit
                     means the harness is unsafe to deploy).
    High      -30
    Medium    -10
    Low       -5
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


class SecurityFloorExceeded(Exception):
    """Raised when security_score < 50 forces DHC-V to 0."""


@dataclass(frozen=True)
class Finding:
    module: str
    severity: str  # critical | high | medium | low
    description: str

    def deduction(self) -> int:
        return {
            "critical": 100,
            "high": 30,
            "medium": 10,
            "low": 5,
        }.get(self.severity, 0)


@dataclass
class ModuleScore:
    module: str
    functionality: float
    security: float
    findings: list[Finding] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class ScoringReport:
    dhc_v: float
    functionality: float
    security: float
    security_floor_triggered: bool
    modules: list[ModuleScore]
    findings: list[Finding]
    bands: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "dhc_v": self.dhc_v,
            "functionality": self.functionality,
            "security": self.security,
            "security_floor_triggered": self.security_floor_triggered,
            "modules": [
                {
                    "module": m.module,
                    "functionality": m.functionality,
                    "security": m.security,
                    "findings": [asdict(f) for f in m.findings],
                    "notes": list(m.notes),
                }
                for m in self.modules
            ],
            "findings": [asdict(f) for f in self.findings],
            "bands": self.bands,
        }


def score_functionality(
    unit_pass_rate: float,
    turn_completion_rate: float,
    ui_streaming_fidelity: float | None = None,
) -> float:
    """Compute the functionality subscore in [0, 100].

    If `ui_streaming_fidelity` is None (Playwright unavailable), the
    weights are re-balanced: 0.5 unit + 0.5 turn. The function never
    silently fakes a UI score.
    """
    if not 0.0 <= unit_pass_rate <= 1.0:
        raise ValueError("unit_pass_rate must be in [0, 1]")
    if not 0.0 <= turn_completion_rate <= 1.0:
        raise ValueError("turn_completion_rate must be in [0, 1]")
    if ui_streaming_fidelity is None:
        score = 50.0 * unit_pass_rate + 50.0 * turn_completion_rate
    else:
        if not 0.0 <= ui_streaming_fidelity <= 1.0:
            raise ValueError("ui_streaming_fidelity must be in [0, 1] or None")
        score = (
            40.0 * unit_pass_rate
            + 40.0 * turn_completion_rate
            + 20.0 * ui_streaming_fidelity
        )
    return round(score, 4)


def score_security(findings: list[Finding]) -> tuple[float, bool]:
    """Return (security_score, floor_triggered).

    A single critical finding forces score -> 0 directly; the
    "post-deduction < 50" floor flag is NOT set, because the score
    was zeroed by the critical itself, not by falling below the
    50-point floor after deduction.
    """
    score = 100.0
    floor_triggered = False
    has_critical = False

    for f in findings:
        if f.severity == "critical":
            has_critical = True
            break
        elif f.severity == "high":
            score -= 30
        elif f.severity == "medium":
            score -= 10
        elif f.severity == "low":
            score -= 5

    if has_critical:
        # Critical zeroes the score outright; floor is NOT triggered.
        return 0.0, False

    if score < 50:
        floor_triggered = True

    return max(0.0, score), floor_triggered


def compute_dhc_v(
    functionality: float,
    security: float,
    security_floor_triggered: bool = False,
) -> float:
    if security_floor_triggered or security < 50:
        return 0.0
    return round(functionality * (security / 100.0), 4)


def band(dhc_v: float) -> str:
    if dhc_v >= 80:
        return "production_ready"
    if dhc_v >= 50:
        return "experimental"
    return "unsafe"


def write_report(
    report: ScoringReport,
    path: str | Path = "dhc-v-report.json",
) -> None:
    p = Path(path)
    p.write_text(json.dumps(report.to_dict(), indent=2, sort_keys=True), encoding="utf-8")


def make_report(
    module_scores: list[ModuleScore],
    findings: list[Finding] | None = None,
) -> ScoringReport:
    findings = findings or [f for m in module_scores for f in m.findings]
    functionality = (
        round(
            sum(m.functionality for m in module_scores) / max(1, len(module_scores)),
            4,
        )
        if module_scores
        else 0.0
    )
    security, floor_triggered = score_security(findings)
    dhc_v = compute_dhc_v(functionality, security, floor_triggered)
    return ScoringReport(
        dhc_v=dhc_v,
        functionality=functionality,
        security=security,
        security_floor_triggered=floor_triggered,
        modules=module_scores,
        findings=findings,
        bands={"production_ready": ">=80", "experimental": "50-79", "unsafe": "<50"},
    )
