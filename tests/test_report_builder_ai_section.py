"""Tests for the AI audit section in build_report."""
from datetime import date
from unittest.mock import MagicMock

import pandas as pd
import pytest

from src.report_builder import build_report
from src.risk_gate import RiskGateResult
from src.schemas import AuditStatus, LlmAuditResult


def _make_cfg():
    cfg = MagicMock()
    cfg.universe.assets = []
    cfg.report.top_n_display = 5
    cfg.risk.risk_off_window = 60
    cfg.report.include_correlation_matrix = False
    return cfg


def _make_audit(**kwargs) -> LlmAuditResult:
    defaults = {"status": AuditStatus.PASS, "summary": "テスト監査", "apply_adjustment": False}
    defaults.update(kwargs)
    return LlmAuditResult.model_validate(defaults)


def _build_report(audit_result=None, weights=None):
    weights = weights or {"VOO": 1.0}
    scores = pd.Series({t: 5.0 for t in weights})
    indicators = pd.DataFrame({"vol_20d": [0.15] * len(weights)}, index=list(weights))
    prices = pd.DataFrame({t: [100.0] for t in weights})
    risk_gate = RiskGateResult(risk_off=False, sp500_return=0.05, message="Risk-ON")
    return build_report(
        cfg=_make_cfg(),
        weights=weights,
        scores=scores,
        indicators=indicators,
        prices=prices,
        risk_gate=risk_gate,
        prev_weights=None,
        turnover=None,
        run_date=date(2026, 5, 24),
        audit_result=audit_result,
    )


# ── AI audit section presence ────────────────────────────────────────────────


def test_no_ai_section_when_audit_is_none():
    report = _build_report(audit_result=None)
    assert "AI監査結果" not in report


def test_ai_section_shows_status():
    audit = _make_audit(status=AuditStatus.REVIEW_REQUIRED)
    report = _build_report(audit_result=audit)
    assert "REVIEW_REQUIRED" in report


def test_ai_section_shows_summary():
    audit = _make_audit(summary="ポートフォリオ集中リスクあり")
    report = _build_report(audit_result=audit)
    assert "ポートフォリオ集中リスクあり" in report


# ── Adjustment table — valid adjustments ────────────────────────────────────


def test_valid_adjustment_shows_valid_label():
    audit = _make_audit(
        adjustments=[
            {"ticker": "VOO", "current_weight": 0.50, "suggested_weight": 0.53, "reason": "minor up"}
        ]
    )
    # Mark as valid (within ±5%)
    audit.adjustments[0].valid = True
    report = _build_report(audit_result=audit, weights={"VOO": 0.50})
    assert "有効" in report
    assert "❌ 無効" not in report


def test_invalid_adjustment_shows_invalid_label():
    audit = _make_audit(
        adjustments=[
            {"ticker": "VOO", "current_weight": 0.50, "suggested_weight": 0.10, "reason": "cut big"}
        ]
    )
    audit.adjustments[0].valid = False
    audit.adjustments_invalidated = True
    report = _build_report(audit_result=audit, weights={"VOO": 0.50})
    assert "❌ 無効" in report


def test_invalidation_notice_shown_when_adjustments_invalidated():
    audit = _make_audit(
        adjustments=[
            {"ticker": "SGOV", "current_weight": 0.50, "suggested_weight": 0.20, "reason": "reduce"}
        ]
    )
    audit.adjustments[0].valid = False
    audit.adjustments_invalidated = True
    report = _build_report(audit_result=audit, weights={"SGOV": 0.50})
    assert "±5%制約を超えるAI参考調整案は無効化しました" in report


def test_no_invalidation_notice_when_all_valid():
    audit = _make_audit(
        adjustments=[
            {"ticker": "VOO", "current_weight": 0.50, "suggested_weight": 0.53, "reason": "small"}
        ]
    )
    audit.adjustments_invalidated = False
    report = _build_report(audit_result=audit, weights={"VOO": 0.50})
    assert "±5%制約を超えるAI参考調整案は無効化しました" not in report


def test_adjustment_table_shows_delta():
    audit = _make_audit(
        adjustments=[
            {"ticker": "VOO", "current_weight": 0.50, "suggested_weight": 0.20, "reason": "cut"}
        ]
    )
    audit.adjustments[0].valid = False
    audit.adjustments_invalidated = True
    report = _build_report(audit_result=audit, weights={"VOO": 0.50})
    # Delta should appear: |0.50 - 0.20| = 0.30 = 30.0%
    assert "30.0%" in report


# ── ai_adjustment_applied invariant ─────────────────────────────────────────


def test_report_contains_disclaimer():
    audit = _make_audit()
    report = _build_report(audit_result=audit)
    assert "実配分に反映しません" in report or "自動売買は行いません" in report
