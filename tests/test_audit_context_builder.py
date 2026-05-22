from datetime import date

import numpy as np
import pandas as pd
import pytest

from src.audit_context_builder import build_audit_context
from src.config_loader import load_config
from src.risk_gate import RiskGateResult


@pytest.fixture
def cfg():
    return load_config("config.yaml")


@pytest.fixture
def sample_weights():
    return {"VOO": 0.40, "TLT": 0.35, "SGOV": 0.25}


@pytest.fixture
def sample_scores(sample_weights):
    return pd.Series({"VOO": 0.8, "TLT": 0.6, "SGOV": 0.4})


@pytest.fixture
def sample_indicators(sample_weights):
    idx = list(sample_weights.keys())
    data = {
        "mom_1m": [0.02, -0.01, 0.001],
        "mom_3m": [0.05, 0.01, 0.002],
        "mom_6m": [0.10, 0.02, 0.003],
        "mom_12m": [0.15, 0.03, 0.004],
        "vol_20d": [0.18, 0.10, 0.02],
    }
    return pd.DataFrame(data, index=idx)


@pytest.fixture
def risk_gate_on():
    return RiskGateResult(risk_off=False, sp500_return=0.05, message="risk on")


def test_context_keys(cfg, sample_weights, sample_scores, sample_indicators, risk_gate_on):
    ctx = build_audit_context(
        cfg=cfg,
        weights=sample_weights,
        scores=sample_scores,
        indicators=sample_indicators,
        risk_gate=risk_gate_on,
        prev_weights=None,
        turnover=None,
        run_date=date(2026, 5, 1),
    )
    assert "run_date" in ctx
    assert "risk_mode" in ctx
    assert "allocation" in ctx
    assert "constraints" in ctx
    assert "category_summary" in ctx


def test_run_date_format(cfg, sample_weights, sample_scores, sample_indicators, risk_gate_on):
    ctx = build_audit_context(
        cfg=cfg,
        weights=sample_weights,
        scores=sample_scores,
        indicators=sample_indicators,
        risk_gate=risk_gate_on,
        prev_weights=None,
        turnover=None,
        run_date=date(2026, 5, 1),
    )
    assert ctx["run_date"] == "2026-05-01"


def test_risk_mode_risk_on(cfg, sample_weights, sample_scores, sample_indicators, risk_gate_on):
    ctx = build_audit_context(
        cfg=cfg,
        weights=sample_weights,
        scores=sample_scores,
        indicators=sample_indicators,
        risk_gate=risk_gate_on,
        prev_weights=None,
        turnover=None,
        run_date=date(2026, 5, 1),
    )
    assert ctx["risk_mode"] == "risk_on"


def test_risk_mode_risk_off(cfg, sample_weights, sample_scores, sample_indicators):
    gate = RiskGateResult(risk_off=True, sp500_return=-0.10, message="risk off")
    ctx = build_audit_context(
        cfg=cfg,
        weights=sample_weights,
        scores=sample_scores,
        indicators=sample_indicators,
        risk_gate=gate,
        prev_weights=None,
        turnover=None,
        run_date=date(2026, 5, 1),
    )
    assert ctx["risk_mode"] == "risk_off"


def test_allocation_sorted_by_weight(cfg, sample_weights, sample_scores, sample_indicators, risk_gate_on):
    ctx = build_audit_context(
        cfg=cfg,
        weights=sample_weights,
        scores=sample_scores,
        indicators=sample_indicators,
        risk_gate=risk_gate_on,
        prev_weights=None,
        turnover=None,
        run_date=date(2026, 5, 1),
    )
    weights_in_order = [e["weight"] for e in ctx["allocation"]]
    assert weights_in_order == sorted(weights_in_order, reverse=True)


def test_prev_allocation_included(cfg, sample_weights, sample_scores, sample_indicators, risk_gate_on):
    prev = {"VOO": 0.50, "TLT": 0.50}
    ctx = build_audit_context(
        cfg=cfg,
        weights=sample_weights,
        scores=sample_scores,
        indicators=sample_indicators,
        risk_gate=risk_gate_on,
        prev_weights=prev,
        turnover=0.10,
        run_date=date(2026, 5, 1),
    )
    assert ctx["prev_allocation"] is not None
    assert ctx["turnover"] == pytest.approx(0.10)


def test_no_prev_allocation(cfg, sample_weights, sample_scores, sample_indicators, risk_gate_on):
    ctx = build_audit_context(
        cfg=cfg,
        weights=sample_weights,
        scores=sample_scores,
        indicators=sample_indicators,
        risk_gate=risk_gate_on,
        prev_weights=None,
        turnover=None,
        run_date=date(2026, 5, 1),
    )
    assert ctx["prev_allocation"] is None
    assert ctx["turnover"] is None
