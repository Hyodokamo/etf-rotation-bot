"""Tests for the Risk-ON allocation category constraint (allocation MVP).

Verifies that fixed_income / defensive are nudged to their caps with
score-weighted redistribution, weights stay summed to 1.0, the per-asset cap is
respected, Risk-OFF is a no-op, and an honest residual is left for the gate when
redistribution capacity is insufficient. No orders / quantities / auto-trade.
"""
from __future__ import annotations

import pytest

from src.allocation_constraints import apply_allocation_constraints
from src.config_loader import AllocationConstraintsConfig


# bond/cash_like = fixed_income; commodity/fx also defensive; equities are redistribution targets
_CATS = {
    "BND": "bond", "IEF": "bond", "TLT": "bond", "2561.T": "bond",
    "SGOV": "cash_like", "GLDM": "commodity", "UUP": "fx",
    "VOO": "core_equity", "VTV": "factor", "QQQM": "growth_equity", "VWO": "core_equity",
}


def _cfg(**kw) -> AllocationConstraintsConfig:
    base = {"enabled": True}
    base.update(kw)
    return AllocationConstraintsConfig(**base)


def _total(weights, cats, group):
    return sum(w for t, w in weights.items() if cats.get(t) in group)


def _apply(weights, *, risk_off=False, max_w=0.40, cfg=None, scores=None):
    cfg = cfg or _cfg()
    scores = scores or {t: 1.0 for t in weights}
    return apply_allocation_constraints(
        weights, _CATS, scores, cfg,
        risk_off=risk_off, max_weight_per_asset=max_w,
        defensive_categories=["bond", "cash_like", "commodity", "fx"],
    )


# ── 1. Risk-ON fixed_income over limit → reduced to <= limit ───────────────────

def test_fixed_income_reduced_to_cap():
    w = {"BND": 0.30, "IEF": 0.30, "VOO": 0.20, "VTV": 0.20}
    out, adj = _apply(w, max_w=0.40)
    assert adj.applied is True
    assert _total(out, _CATS, {"bond"}) == pytest.approx(0.40, abs=1e-6)
    assert adj.fully_satisfied is True


def test_excess_redistributed_to_allowed_equities():
    w = {"BND": 0.30, "IEF": 0.30, "VOO": 0.20, "VTV": 0.20}
    out, _ = _apply(w, max_w=0.40, scores={"BND": 1, "IEF": 1, "VOO": 3, "VTV": 1})
    # equities grew; VOO (higher score) grew more than VTV
    assert out["VOO"] > 0.20
    assert out["VTV"] > 0.20
    assert out["VOO"] > out["VTV"]


def test_weights_sum_to_one():
    w = {"BND": 0.30, "IEF": 0.30, "VOO": 0.20, "VTV": 0.20}
    out, _ = _apply(w, max_w=0.40)
    assert sum(out.values()) == pytest.approx(1.0, abs=1e-9)


# ── 2. per-asset cap respected ────────────────────────────────────────────────

def test_per_asset_cap_respected():
    w = {"BND": 0.30, "IEF": 0.30, "VOO": 0.20, "VTV": 0.20}
    out, _ = _apply(w, max_w=0.25)
    for t, weight in out.items():
        assert weight <= 0.25 + 1e-9


# ── 3. insufficient capacity → honest residual, still sums to 1.0 ─────────────

def test_insufficient_capacity_leaves_residual():
    # Reproduces the reported case: 3 bonds + 1 equity, max_w 0.25.
    w = {"BND": 0.378, "IEF": 0.225, "VTV": 0.215, "TLT": 0.182}
    out, adj = _apply(w, max_w=0.25)
    assert sum(out.values()) == pytest.approx(1.0, abs=1e-9)
    assert out["VTV"] <= 0.25 + 1e-9
    # fixed_income reduced but not all the way to 0.40 (only one equity to absorb)
    assert _total(out, _CATS, {"bond"}) < 0.785
    assert _total(out, _CATS, {"bond"}) > 0.40
    assert adj.fully_satisfied is False


# ── 4. Risk-OFF → no-op (Risk-ON limits must not apply) ───────────────────────

def test_risk_off_is_noop():
    w = {"BND": 0.60, "IEF": 0.20, "VOO": 0.20}
    out, adj = _apply(w, risk_off=True)
    assert out == w
    assert adj.applied is False
    assert adj.risk_off is True


# ── 5. disabled → no-op ───────────────────────────────────────────────────────

def test_disabled_is_noop():
    w = {"BND": 0.60, "VOO": 0.40}
    out, adj = _apply(w, cfg=_cfg(enabled=False))
    assert out == w
    assert adj.applied is False


# ── 6. already within limits → no change ─────────────────────────────────────

def test_within_limits_no_change():
    w = {"BND": 0.30, "VOO": 0.40, "VTV": 0.30}
    out, adj = _apply(w, max_w=0.40)
    assert adj.applied is False
    assert out == w


# ── 7. defensive cap covers commodity / fx, not only bonds ───────────────────

def test_defensive_cap_includes_commodity_and_fx():
    # bonds 0.20 + commodity 0.20 + fx 0.15 = 0.55 defensive > 0.40
    w = {"BND": 0.20, "GLDM": 0.20, "UUP": 0.15, "VOO": 0.25, "VTV": 0.20}
    out, adj = _apply(w, max_w=0.40)
    assert adj.applied is True
    assert _total(out, _CATS, {"bond", "cash_like", "commodity", "fx"}) == pytest.approx(0.40, abs=1e-6)
    assert sum(out.values()) == pytest.approx(1.0, abs=1e-9)


# ── 8. cash_fallback (cash_like) counts as fixed_income ───────────────────────

def test_cash_like_counts_as_fixed_income():
    w = {"BND": 0.30, "SGOV": 0.25, "VOO": 0.25, "VTV": 0.20}
    out, adj = _apply(w, max_w=0.45)
    # fixed_income = bond + cash_like = 0.55 → capped to 0.40
    assert _total(out, _CATS, {"bond", "cash_like"}) == pytest.approx(0.40, abs=1e-6)


# ── 9. no allowed targets → no-op, flagged not satisfied ─────────────────────

def test_no_allowed_targets_leaves_unchanged():
    w = {"BND": 0.60, "IEF": 0.40}  # all defensive, no equity to receive
    out, adj = _apply(w, max_w=0.40)
    assert out == w
    assert adj.fully_satisfied is False


# ── 10. slack_line wording (no order/auto-trade language) ────────────────────

def test_slack_line_present_when_applied():
    w = {"BND": 0.30, "IEF": 0.30, "VOO": 0.20, "VTV": 0.20}
    _, adj = _apply(w, max_w=0.40)
    line = adj.slack_line()
    assert line is not None
    assert "配分調整" in line
    for bad in ("買え", "売れ", "購入実行", "売却実行", "注文"):
        assert bad not in line


def test_slack_line_none_when_not_applied():
    w = {"BND": 0.30, "VOO": 0.70}
    _, adj = _apply(w, max_w=0.70)
    assert adj.slack_line() is None
