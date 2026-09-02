"""Scoring engine validation: multiplicative formula + security floor."""

import json
from pathlib import Path

import pytest

from dhc.scoring.scorer import (
    Finding,
    ModuleScore,
    SecurityFloorExceeded,
    band,
    compute_dhc_v,
    make_report,
    score_functionality,
    score_security,
    write_report,
)


# --- functionality ---


def test_functionality_full_pass():
    s = score_functionality(1.0, 1.0, 1.0)
    assert s == 100.0


def test_functionality_full_pass_no_ui():
    s = score_functionality(1.0, 1.0, None)
    assert s == 100.0


def test_functionality_zero():
    assert score_functionality(0, 0, 0) == 0.0
    assert score_functionality(0, 0, None) == 0.0


def test_functionality_half_pass_with_ui():
    s = score_functionality(0.5, 0.5, 0.5)
    assert 49.0 < s < 51.0


def test_functionality_reweights_when_ui_missing():
    s_full = score_functionality(1.0, 1.0, None)
    s_partial = score_functionality(1.0, 0.0, None)
    assert s_full == 100.0
    assert s_partial == 50.0


def test_functionality_validates_ranges():
    with pytest.raises(ValueError):
        score_functionality(1.5, 1.0, 1.0)
    with pytest.raises(ValueError):
        score_functionality(1.0, -0.1, 1.0)


# --- security ---


def test_security_clean_harness_scores_100():
    score, floor = score_security([])
    assert score == 100.0
    assert floor is False


def test_security_single_low_deducts_5():
    score, _ = score_security([Finding("c1", "low", "missing CSP")])
    assert score == 95.0


def test_security_single_medium_deducts_10():
    score, _ = score_security([Finding("c1", "medium", "verbose error")])
    assert score == 90.0


def test_security_single_high_deducts_30():
    score, _ = score_security([Finding("c4", "high", "path traversal")])
    assert score == 70.0


def test_security_single_critical_zeroes_score():
    score, _ = score_security([Finding("c4", "critical", "RCE")])
    assert score == 0.0


def test_security_below_50_triggers_floor():
    score, floor = score_security(
        [
            Finding("c4", "high", "x"),
            Finding("c4", "high", "y"),
        ]
    )
    assert score == 40.0
    assert floor is True


def test_security_deductions_clamp_at_zero():
    findings = [Finding("c4", "critical", f"r{i}") for i in range(3)]
    score, _ = score_security(findings)
    assert score == 0.0


# --- DHC-V multiplicative ---


def test_dhc_v_full_passes():
    assert compute_dhc_v(100.0, 100.0) == 100.0


def test_dhc_v_functional_but_insecure_is_zero():
    """A 100% functional but 0% secure harness must score 0, not 50."""
    assert compute_dhc_v(100.0, 0.0) == 0.0


def test_dhc_v_secure_but_broken_is_zero():
    """A 0% functional but 100% secure harness must score 0, not 50.
    This is the auditor's 'secure but broken' attack."""
    assert compute_dhc_v(0.0, 100.0) == 0.0


def test_dhc_v_below_security_floor_is_zero():
    assert compute_dhc_v(80.0, 49.0) == 0.0
    assert compute_dhc_v(80.0, 30.0) == 0.0


def test_dhc_v_at_exact_floor_is_zero():
    """Security score of exactly 49 must trigger the floor (< 50, not <=)."""
    assert compute_dhc_v(80.0, 49.999) == 0.0


def test_dhc_v_just_above_floor_is_multiplicative():
    assert compute_dhc_v(80.0, 50.0) == 40.0
    assert compute_dhc_v(90.0, 60.0) == 54.0


# --- bands ---


def test_band_production_ready():
    assert band(80) == "production_ready"
    assert band(100) == "production_ready"


def test_band_experimental():
    assert band(50) == "experimental"
    assert band(79.99) == "experimental"


def test_band_unsafe():
    assert band(0) == "unsafe"
    assert band(49.99) == "unsafe"


# --- end-to-end report ---


def test_make_report_clean_harness():
    modules = [
        ModuleScore(module="c1", functionality=100.0, security=100.0),
        ModuleScore(module="c4", functionality=100.0, security=100.0),
    ]
    r = make_report(modules)
    assert r.dhc_v == 100.0
    assert r.security == 100.0
    assert r.security_floor_triggered is False


def test_make_report_insecure_harness_is_zero():
    modules = [
        ModuleScore(module="c1", functionality=100.0, security=100.0),
    ]
    findings = [Finding("c4", "critical", "RCE")]
    r = make_report(modules, findings=findings)
    assert r.dhc_v == 0.0
    assert r.security == 0.0
    assert r.security_floor_triggered is False  # critical zeroes the score outright


def test_make_report_below_floor_forces_zero():
    modules = [
        ModuleScore(
            module="c4",
            functionality=80.0,
            security=40.0,
            findings=[Finding("c4", "high", "x"), Finding("c4", "high", "y")],
        ),
    ]
    r = make_report(modules)
    assert r.dhc_v == 0.0
    assert r.security_floor_triggered is True


def test_write_report_roundtrip(tmp_path: Path):
    modules = [ModuleScore(module="c1", functionality=100.0, security=100.0)]
    r = make_report(modules)
    p = tmp_path / "report.json"
    write_report(r, p)
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["dhc_v"] == 100.0
    assert "modules" in data
    assert "findings" in data
    assert "bands" in data


def test_write_report_default_path(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    modules = [ModuleScore(module="c1", functionality=100.0, security=100.0)]
    r = make_report(modules)
    write_report(r)
    assert (tmp_path / "dhc-v-report.json").exists()


# --- auditor's explicit no-additive-scoring guard ---


def test_dhc_v_never_uses_additive_formula():
    """The auditor banned additive scoring. The multiplicative form
    is the only correct path. This test guards against accidental
    re-introduction of a 0.5*f + 0.5*s style formula by ensuring the
    boundary cases hit zero, not 50."""
    assert compute_dhc_v(0.0, 100.0) != 50.0
    assert compute_dhc_v(100.0, 0.0) != 50.0
    assert compute_dhc_v(50.0, 50.0) == 25.0  # multiplicative, not 50
