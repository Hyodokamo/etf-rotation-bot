"""Tests for Phase 2.6: Deterministic pre-trade constraint gate."""
import json
import tempfile
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd
import pytest

from src.pre_trade_gate import (
    GateCheckResult,
    PreTradeGateResult,
    _resolve_overall_status,
    check_category_limit,
    check_nisa_policy,
    check_risk_mode_alignment,
    check_single_asset_limit,
    check_turnover,
    run_pre_trade_gate,
    save_pre_trade_gate_result,
)
from src.risk_gate import RiskGateResult
from src.risk_mode_check import RiskModeCheckResult


# ── helpers ──────────────────────────────────────────────────────────────────


def _make_gate(**kwargs) -> PreTradeGateResult:
    defaults = {"overall_status": "PASS", "checks": [], "enabled": True}
    defaults.update(kwargs)
    return PreTradeGateResult(**defaults)


def _make_risk_mode(
    status: str = "REVIEW_REQUIRED",
    defensive_weight: float = 0.848,
) -> RiskModeCheckResult:
    return RiskModeCheckResult(
        enabled=True,
        risk_mode="RISK_ON",
        defensive_weight=defensive_weight,
        status=status,
        message=f"Risk-ON but defensive allocation is {defensive_weight:.1%}.",
    )


def _make_cfg(**overrides):
    cfg = MagicMock()
    cfg.pre_trade_gate.enabled = True
    cfg.pre_trade_gate.single_asset_limit = 0.45
    cfg.pre_trade_gate.category_limits = {"cash_like": 0.40, "fx": 0.10}
    cfg.risk.max_weight_per_asset = 0.25
    cfg.risk.max_category_weights = {"cash_like": 0.40, "fx": 0.10}
    cfg.turnover.effective_limit = 0.20
    cfg.risk_mode_checks.defensive_categories = ["bond", "cash_like", "commodity", "fx"]
    cfg.risk_mode_checks.risk_on_defensive_review_threshold = 0.75
    cfg.risk_mode_checks.risk_on_defensive_warning_threshold = 0.60
    for k, v in overrides.items():
        setattr(cfg.pre_trade_gate, k, v)
    return cfg


# ── single_asset_limit_check ─────────────────────────────────────────────────


def test_single_asset_exceeds_limit_fails():
    result = check_single_asset_limit({"SGOV": 0.503}, limit=0.45)
    assert result.status == "FAIL"
    assert result.check_id == "single_asset_limit_check"
    assert "SGOV" in result.affected_assets


def test_single_asset_within_limit_passes():
    result = check_single_asset_limit({"VOO": 0.30, "SGOV": 0.30}, limit=0.45)
    assert result.status == "PASS"
    assert result.affected_assets == []


def test_single_asset_exactly_at_limit_passes():
    result = check_single_asset_limit({"VOO": 0.45}, limit=0.45)
    assert result.status == "PASS"


# ── category_limit_check ─────────────────────────────────────────────────────


def test_category_exceeds_limit_fails():
    weights = {"SGOV": 0.503}
    cat = {"SGOV": "cash_like"}
    result = check_category_limit(weights, cat, {"cash_like": 0.40})
    assert result.status == "FAIL"
    assert result.check_id == "category_limit_check"
    assert "SGOV" in result.affected_assets


def test_category_within_limit_passes():
    weights = {"SGOV": 0.30, "VOO": 0.70}
    cat = {"SGOV": "cash_like", "VOO": "core_equity"}
    result = check_category_limit(weights, cat, {"cash_like": 0.40})
    assert result.status == "PASS"


def test_category_limit_check_no_limits_passes():
    weights = {"SGOV": 0.99}
    cat = {"SGOV": "cash_like"}
    result = check_category_limit(weights, cat, {})
    assert result.status == "PASS"


# ── risk_mode_alignment_check ─────────────────────────────────────────────────


def test_risk_mode_alignment_review_required():
    from src.config_loader import RiskModeCheckConfig
    rmc = _make_risk_mode(status="REVIEW_REQUIRED", defensive_weight=0.848)
    cfg = RiskModeCheckConfig()
    result = check_risk_mode_alignment(rmc, risk_mode_cfg=cfg)
    assert result.status == "REVIEW_REQUIRED"
    assert result.check_id == "risk_mode_alignment_check"
    assert result.limit == pytest.approx(0.75)


def test_risk_mode_alignment_pass_when_disabled():
    disabled = RiskModeCheckResult(
        enabled=False, risk_mode="RISK_ON", defensive_weight=0.0,
        status="N/A", message="disabled",
    )
    result = check_risk_mode_alignment(disabled)
    assert result.status == "N/A"


def test_risk_mode_alignment_pass_with_caution():
    from src.config_loader import RiskModeCheckConfig
    rmc = _make_risk_mode(status="PASS_WITH_CAUTION", defensive_weight=0.65)
    result = check_risk_mode_alignment(rmc, risk_mode_cfg=RiskModeCheckConfig())
    assert result.status == "PASS_WITH_CAUTION"
    assert result.limit == pytest.approx(0.60)


def test_risk_mode_alignment_affected_assets_detected():
    from src.config_loader import RiskModeCheckConfig
    rmc = _make_risk_mode()
    weights = {"SGOV": 0.50, "VOO": 0.50}
    cat = {"SGOV": "cash_like", "VOO": "core_equity"}
    result = check_risk_mode_alignment(
        rmc,
        weights=weights,
        ticker_to_category=cat,
        defensive_categories=["cash_like"],
        risk_mode_cfg=RiskModeCheckConfig(),
    )
    assert "SGOV" in result.affected_assets
    assert "VOO" not in result.affected_assets


# ── turnover_check ────────────────────────────────────────────────────────────


def test_turnover_within_limit_passes():
    result = check_turnover(actual_turnover=0.15, limit=0.20)
    assert result.status == "PASS"


def test_turnover_exceeds_limit_fails():
    result = check_turnover(actual_turnover=0.25, limit=0.20)
    assert result.status == "FAIL"


def test_turnover_none_returns_na():
    result = check_turnover(actual_turnover=None, limit=0.20)
    assert result.status == "N/A"


# ── _resolve_overall_status ───────────────────────────────────────────────────


def test_overall_status_fail_wins_over_all():
    checks = [
        GateCheckResult("a", "PASS", "HIGH", "ok"),
        GateCheckResult("b", "REVIEW_REQUIRED", "MEDIUM", "check"),
        GateCheckResult("c", "FAIL", "HIGH", "bad"),
    ]
    assert _resolve_overall_status(checks) == "FAIL"


def test_overall_status_review_required_over_caution():
    checks = [
        GateCheckResult("a", "PASS_WITH_CAUTION", "HIGH", "caution"),
        GateCheckResult("b", "REVIEW_REQUIRED", "MEDIUM", "review"),
    ]
    assert _resolve_overall_status(checks) == "REVIEW_REQUIRED"


def test_overall_status_info_severity_ignored():
    checks = [
        GateCheckResult("nisa", "N/A", "INFO", "nisa info"),
        GateCheckResult("to", "PASS", "LOW", "ok"),
    ]
    assert _resolve_overall_status(checks) == "PASS"


def test_overall_status_all_na_returns_pass():
    checks = [GateCheckResult("x", "N/A", "LOW", "disabled")]
    assert _resolve_overall_status(checks) == "PASS"


# ── save_pre_trade_gate_result ────────────────────────────────────────────────


def test_save_creates_file():
    gate = _make_gate(overall_status="REVIEW_REQUIRED")
    with tempfile.TemporaryDirectory() as tmp:
        path = save_pre_trade_gate_result(gate, tmp)
        assert Path(path).exists()
        assert Path(path).name == "pre_trade_gate_result.json"


def test_save_valid_json():
    check = GateCheckResult(
        check_id="single_asset_limit_check",
        status="FAIL",
        severity="HIGH",
        message="SGOV exceeds limit.",
        value=0.503,
        limit=0.45,
        affected_assets=["SGOV"],
    )
    gate = _make_gate(overall_status="FAIL", checks=[check])
    with tempfile.TemporaryDirectory() as tmp:
        path = save_pre_trade_gate_result(gate, tmp)
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        assert data["overall_status"] == "FAIL"
        assert data["checks"][0]["check_id"] == "single_asset_limit_check"
        assert data["checks"][0]["affected_assets"] == ["SGOV"]


# ── run_pre_trade_gate ────────────────────────────────────────────────────────


def test_run_pre_trade_gate_pass():
    cfg = _make_cfg()
    result = run_pre_trade_gate(
        weights={"VOO": 0.60, "TLT": 0.40},
        ticker_to_category={"VOO": "core_equity", "TLT": "bond"},
        cfg=cfg,
    )
    assert result.enabled is True
    assert result.overall_status in ("PASS", "PASS_WITH_CAUTION", "REVIEW_REQUIRED", "FAIL")


def test_run_pre_trade_gate_disabled_returns_na():
    cfg = _make_cfg()
    cfg.pre_trade_gate.enabled = False
    result = run_pre_trade_gate(
        weights={"SGOV": 1.0},
        ticker_to_category={"SGOV": "cash_like"},
        cfg=cfg,
    )
    assert result.overall_status == "N/A"
    assert result.enabled is False


def test_run_pre_trade_gate_cash_like_exceeds_limit_gives_fail():
    cfg = _make_cfg()
    result = run_pre_trade_gate(
        weights={"SGOV": 0.60},
        ticker_to_category={"SGOV": "cash_like"},
        cfg=cfg,
    )
    assert result.overall_status == "FAIL"
    statuses = {c.check_id: c.status for c in result.checks}
    assert statuses["category_limit_check"] == "FAIL"


# ── audit_context includes pre_trade_gate ─────────────────────────────────────


def test_audit_context_includes_pre_trade_gate():
    from src.audit_context_builder import build_audit_context

    cfg = MagicMock()
    cfg.universe.assets = []
    cfg.risk.max_weight_per_asset = 0.25
    cfg.risk.max_category_weights = {}
    cfg.turnover.effective_limit = 0.20
    cfg.turnover.mode_label = "通常運用"

    gate = PreTradeGateResult(
        overall_status="FAIL",
        checks=[GateCheckResult("single_asset_limit_check", "FAIL", "HIGH", "SGOV exceeds.", 0.503, 0.45, ["SGOV"])],
        enabled=True,
    )
    risk_gate = RiskGateResult(risk_off=False, sp500_return=0.05, message="Risk-ON")

    context = build_audit_context(
        cfg=cfg,
        weights={"SGOV": 0.503},
        scores=pd.Series({"SGOV": 1.0}),
        indicators=pd.DataFrame(),
        risk_gate=risk_gate,
        prev_weights=None,
        turnover=None,
        run_date=date(2026, 5, 24),
        pre_trade_gate=gate,
    )
    assert "pre_trade_gate" in context
    assert context["pre_trade_gate"]["overall_status"] == "FAIL"
    assert context["pre_trade_gate"]["checks"][0]["check_id"] == "single_asset_limit_check"


def test_audit_context_no_pre_trade_gate_when_disabled():
    from src.audit_context_builder import build_audit_context

    cfg = MagicMock()
    cfg.universe.assets = []
    cfg.risk.max_weight_per_asset = 0.25
    cfg.risk.max_category_weights = {}
    cfg.turnover.effective_limit = 0.20
    cfg.turnover.mode_label = "通常運用"

    gate = PreTradeGateResult(overall_status="N/A", checks=[], enabled=False)
    risk_gate = RiskGateResult(risk_off=False, sp500_return=0.05, message="Risk-ON")

    context = build_audit_context(
        cfg=cfg,
        weights={},
        scores=pd.Series(dtype=float),
        indicators=pd.DataFrame(),
        risk_gate=risk_gate,
        prev_weights=None,
        turnover=None,
        run_date=date(2026, 5, 24),
        pre_trade_gate=gate,
    )
    assert "pre_trade_gate" not in context


# ── report.md contains Pre-Trade Gate ────────────────────────────────────────


def _make_report_cfg():
    cfg = MagicMock()
    cfg.universe.assets = []
    cfg.report.top_n_display = 5
    cfg.risk.risk_off_window = 60
    cfg.report.include_correlation_matrix = False
    return cfg


def test_report_contains_pre_trade_gate_section():
    from src.report_builder import build_report

    gate = PreTradeGateResult(
        overall_status="FAIL",
        checks=[
            GateCheckResult("single_asset_limit_check", "FAIL", "HIGH", "SGOV exceeds.", 0.503, 0.45, ["SGOV"]),
            GateCheckResult("category_limit_check", "FAIL", "HIGH", "cash_like exceeds.", 0.503, 0.40, ["SGOV"]),
        ],
        enabled=True,
    )
    report = build_report(
        cfg=_make_report_cfg(),
        weights={"SGOV": 0.503},
        scores=pd.Series({"SGOV": 1.0}),
        indicators=pd.DataFrame({"vol_20d": [0.10]}, index=["SGOV"]),
        prices=pd.DataFrame({"SGOV": [100.0]}),
        risk_gate=RiskGateResult(risk_off=False, sp500_return=0.05, message="Risk-ON"),
        prev_weights=None,
        turnover=None,
        run_date=date(2026, 5, 24),
        pre_trade_gate=gate,
    )
    assert "Pre-Trade Gate" in report
    assert "FAIL" in report
    assert "single_asset_limit_check" in report
    assert "手動確認必須" in report


def test_report_no_pre_trade_gate_section_when_none():
    from src.report_builder import build_report

    report = build_report(
        cfg=_make_report_cfg(),
        weights={"VOO": 1.0},
        scores=pd.Series({"VOO": 1.0}),
        indicators=pd.DataFrame({"vol_20d": [0.10]}, index=["VOO"]),
        prices=pd.DataFrame({"VOO": [100.0]}),
        risk_gate=RiskGateResult(risk_off=False, sp500_return=0.05, message="Risk-ON"),
        prev_weights=None,
        turnover=None,
        run_date=date(2026, 5, 24),
        pre_trade_gate=None,
    )
    assert "Pre-Trade Gate" not in report


# ── Slack contains Pre-Trade Gate ─────────────────────────────────────────────


def test_slack_contains_pre_trade_gate_summary():
    from src.slack_client import build_slack_summary

    gate = PreTradeGateResult(
        overall_status="REVIEW_REQUIRED",
        checks=[
            GateCheckResult(
                "risk_mode_alignment_check", "REVIEW_REQUIRED", "MEDIUM",
                "Risk-ON but defensive allocation is 84.8%.", 0.848, 0.75,
            )
        ],
        enabled=True,
    )
    msg = build_slack_summary(
        weights={"SGOV": 1.0},
        risk_off=False,
        turnover=None,
        report_path="outputs/report.md",
        pre_trade_gate=gate,
    )
    assert "Pre-Trade Gate" in msg
    assert "REVIEW_REQUIRED" in msg


def test_slack_pre_trade_gate_shows_problem_messages():
    from src.slack_client import build_slack_summary

    gate = PreTradeGateResult(
        overall_status="FAIL",
        checks=[
            GateCheckResult("category_limit_check", "FAIL", "HIGH", "cash_like超過"),
        ],
        enabled=True,
    )
    msg = build_slack_summary(
        weights={"SGOV": 0.503},
        risk_off=False,
        turnover=None,
        report_path="outputs/report.md",
        pre_trade_gate=gate,
    )
    assert "cash_like超過" in msg


def test_slack_no_pre_trade_gate_when_disabled():
    from src.slack_client import build_slack_summary

    gate = PreTradeGateResult(overall_status="N/A", checks=[], enabled=False)
    msg = build_slack_summary(
        weights={"VOO": 1.0},
        risk_off=False,
        turnover=None,
        report_path="outputs/report.md",
        pre_trade_gate=gate,
    )
    assert "Pre-Trade Gate" not in msg
