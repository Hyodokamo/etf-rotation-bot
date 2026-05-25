"""Tests for Phase 2.4: Risk-ON defensive allocation consistency check."""
import pytest

from src.config_loader import RiskModeCheckConfig
from src.risk_mode_check import RiskModeCheckResult, check_risk_mode_consistency

_DEF_CAT = ["bond", "cash_like", "commodity", "fx"]


def _cfg(**kwargs) -> RiskModeCheckConfig:
    defaults = dict(
        enabled=True,
        risk_on_defensive_warning_threshold=0.60,
        risk_on_defensive_review_threshold=0.75,
        defensive_categories=_DEF_CAT,
    )
    defaults.update(kwargs)
    return RiskModeCheckConfig(**defaults)


def _weights(defensive_frac: float) -> dict[str, float]:
    """Simple 2-asset portfolio: VOO (equity) + SGOV (cash_like)."""
    return {"VOO": 1.0 - defensive_frac, "SGOV": defensive_frac}


_CAT = {"VOO": "core_equity", "SGOV": "cash_like"}


# ── Risk-ON checks ──────────────────────────────────────────────────────────


def test_risk_on_low_defensive_passes():
    result = check_risk_mode_consistency(_weights(0.40), _CAT, risk_off=False, cfg=_cfg())
    assert result.status == "PASS"
    assert result.risk_mode == "RISK_ON"


def test_risk_on_at_warning_boundary_passes():
    result = check_risk_mode_consistency(_weights(0.60), _CAT, risk_off=False, cfg=_cfg())
    assert result.status == "PASS"


def test_risk_on_above_warning_threshold_warns():
    result = check_risk_mode_consistency(_weights(0.65), _CAT, risk_off=False, cfg=_cfg())
    assert result.status == "PASS_WITH_CAUTION"


def test_risk_on_above_review_threshold_requires_review():
    result = check_risk_mode_consistency(_weights(0.85), _CAT, risk_off=False, cfg=_cfg())
    assert result.status == "REVIEW_REQUIRED"


def test_risk_on_defensive_weight_stored():
    result = check_risk_mode_consistency(_weights(0.70), _CAT, risk_off=False, cfg=_cfg())
    assert result.defensive_weight == pytest.approx(0.70)


# ── Risk-OFF checks ─────────────────────────────────────────────────────────


def test_risk_off_high_defensive_passes():
    result = check_risk_mode_consistency(_weights(0.90), _CAT, risk_off=True, cfg=_cfg())
    assert result.status == "PASS"
    assert result.risk_mode == "RISK_OFF"


def test_risk_off_always_passes_regardless_of_weight():
    for frac in [0.10, 0.50, 0.80, 0.99]:
        result = check_risk_mode_consistency(_weights(frac), _CAT, risk_off=True, cfg=_cfg())
        assert result.status == "PASS", f"Expected PASS for defensive_frac={frac}"


# ── Disabled check ───────────────────────────────────────────────────────────


def test_disabled_returns_na():
    result = check_risk_mode_consistency(
        _weights(0.90), _CAT, risk_off=False, cfg=_cfg(enabled=False)
    )
    assert result.status == "N/A"
    assert result.enabled is False


# ── Category detection ───────────────────────────────────────────────────────


def test_only_defensive_categories_counted():
    weights = {"VOO": 0.40, "TLT": 0.30, "SGOV": 0.20, "QQQM": 0.10}
    cat = {"VOO": "core_equity", "TLT": "bond", "SGOV": "cash_like", "QQQM": "growth_equity"}
    result = check_risk_mode_consistency(weights, cat, risk_off=False, cfg=_cfg())
    # defensive = TLT(0.30) + SGOV(0.20) = 0.50 → PASS
    assert result.defensive_weight == pytest.approx(0.50)
    assert result.status == "PASS"


def test_custom_defensive_categories():
    """Custom config with only bond as defensive."""
    weights = {"VOO": 0.40, "TLT": 0.30, "SGOV": 0.30}
    cat = {"VOO": "core_equity", "TLT": "bond", "SGOV": "cash_like"}
    cfg = _cfg(defensive_categories=["bond"])
    result = check_risk_mode_consistency(weights, cat, risk_off=False, cfg=cfg)
    # defensive = TLT(0.30) only
    assert result.defensive_weight == pytest.approx(0.30)
