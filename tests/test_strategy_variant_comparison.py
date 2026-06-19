"""Tests for Phase 2.8: Strategy variant comparison."""
import json
import tempfile
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from src.strategy_runner import (
    ComparisonResult,
    VariantResult,
    build_comparison_markdown,
    save_comparison_report,
)
from src.pre_trade_gate import GateCheckResult, PreTradeGateResult
from src.risk_gate import RiskGateResult
from src.risk_mode_check import RiskModeCheckResult


# ── helpers ──────────────────────────────────────────────────────────────────


def _make_risk_gate(risk_off: bool = False) -> RiskGateResult:
    return RiskGateResult(risk_off=risk_off, sp500_return=0.05, message="ok")


def _make_rmc(status: str = "PASS", dw: float = 0.20) -> RiskModeCheckResult:
    return RiskModeCheckResult(
        enabled=True, risk_mode="RISK_ON", defensive_weight=dw,
        status=status, message="ok",
    )


def _make_gate(status: str = "PASS", checks: list | None = None) -> PreTradeGateResult:
    return PreTradeGateResult(overall_status=status, checks=checks or [], enabled=True)


def _make_variant(
    name: str,
    weights: dict | None = None,
    excluded: list | None = None,
    gate_status: str = "PASS",
    rmc_status: str = "PASS",
    dw: float = 0.20,
    turnover: float | None = None,
) -> VariantResult:
    return VariantResult(
        variant_name=name,
        weights=weights or {"VOO": 0.60, "BND": 0.40},
        excluded_tickers=excluded or [],
        n_eligible=20,
        turnover=turnover,
        proposed_turnover=turnover,
        risk_gate=_make_risk_gate(),
        risk_mode_check=_make_rmc(rmc_status, dw),
        pre_trade_gate=_make_gate(gate_status),
        defensive_weight=dw,
    )


def _make_comparison(
    variants: list[VariantResult] | None = None,
) -> ComparisonResult:
    if variants is None:
        variants = [
            _make_variant("baseline_current", weights={"SGOV": 0.503, "UUP": 0.175, "BND": 0.170, "VOO": 0.152},
                         gate_status="FAIL", rmc_status="REVIEW_REQUIRED", dw=0.848,
                         excluded=[]),
            _make_variant("cash_fallback_separated", weights={"VOO": 0.35, "VTV": 0.30, "BND": 0.20, "QQQM": 0.15},
                         gate_status="PASS", rmc_status="PASS", dw=0.20,
                         excluded=["SGOV", "UUP"]),
        ]
    return ComparisonResult(run_date=date(2026, 5, 26), variants=variants)


# ── VariantResult.to_dict ─────────────────────────────────────────────────────


def test_variant_to_dict_contains_required_keys():
    v = _make_variant("baseline_current")
    d = v.to_dict()
    for key in ("variant_name", "weights", "excluded_tickers", "pre_trade_gate_status", "defensive_weight"):
        assert key in d, f"Missing key: {key}"


def test_variant_excluded_tickers_in_dict():
    v = _make_variant("cash_fallback_separated", excluded=["SGOV", "UUP"])
    d = v.to_dict()
    assert "SGOV" in d["excluded_tickers"]
    assert "UUP" in d["excluded_tickers"]


# ── comparison switches between variants ─────────────────────────────────────


def test_baseline_includes_sgov():
    v = _make_variant("baseline_current", weights={"SGOV": 0.503, "VOO": 0.497})
    assert "SGOV" in v.weights


def test_cash_fallback_separated_excludes_sgov():
    v = _make_variant("cash_fallback_separated", weights={"VOO": 0.60, "BND": 0.40}, excluded=["SGOV", "UUP"])
    assert "SGOV" not in v.weights
    assert "SGOV" in v.excluded_tickers


def test_cash_fallback_separated_excludes_uup():
    v = _make_variant("cash_fallback_separated", excluded=["SGOV", "UUP"])
    assert "UUP" in v.excluded_tickers


def test_final_allocation_within_4_assets():
    v = _make_variant("cash_fallback_separated", weights={"VOO": 0.35, "VTV": 0.30, "BND": 0.20, "QQQM": 0.15})
    assert len(v.weights) <= 4


# ── build_comparison_markdown ─────────────────────────────────────────────────


def test_comparison_md_contains_header():
    md = build_comparison_markdown(_make_comparison())
    assert "Strategy Variant 比較レポート" in md


def test_comparison_md_shows_both_variants():
    md = build_comparison_markdown(_make_comparison())
    assert "baseline_current" in md
    assert "cash_fallback_separated" in md


def test_comparison_md_shows_gate_status():
    md = build_comparison_markdown(_make_comparison())
    assert "FAIL" in md
    assert "PASS" in md


def test_comparison_md_shows_sgov_adoption():
    md = build_comparison_markdown(_make_comparison())
    assert "SGOV" in md


def test_comparison_md_shows_defensive_weight():
    md = build_comparison_markdown(_make_comparison())
    assert "84.8%" in md or "0.848" in md or "84.8" in md


def test_comparison_md_contains_disclaimer():
    md = build_comparison_markdown(_make_comparison())
    assert "自動売買" in md


def test_comparison_md_allocation_detail():
    md = build_comparison_markdown(_make_comparison())
    assert "配分詳細" in md
    assert "VOO" in md


# ── save_comparison_report ────────────────────────────────────────────────────


def test_save_creates_md_and_json():
    cmp = _make_comparison()
    with tempfile.TemporaryDirectory() as tmp:
        md_path, json_path = save_comparison_report(cmp, tmp, date(2026, 5, 26))
        assert Path(md_path).exists()
        assert Path(json_path).exists()
        assert Path(md_path).name == "strategy_variant_comparison.md"
        assert Path(json_path).name == "strategy_variant_comparison.json"


def test_save_json_is_valid():
    cmp = _make_comparison()
    with tempfile.TemporaryDirectory() as tmp:
        _, json_path = save_comparison_report(cmp, tmp, date(2026, 5, 26))
        data = json.loads(Path(json_path).read_text(encoding="utf-8"))
        assert data["run_date"] == "2026-05-26"
        assert len(data["variants"]) == 2


# ── backtest module basics ────────────────────────────────────────────────────


def test_backtest_empty_result_for_insufficient_data():
    from src.backtest import run_backtest, _empty_result
    # Very thin prices (< _MIN_HISTORY_DAYS)
    dates = pd.date_range("2026-01-01", periods=30, freq="B")
    prices = pd.DataFrame({"VOO": [100.0 + i for i in range(30)]}, index=dates)

    cfg = MagicMock()
    cfg.scoring.momentum_windows.mom_1m = 21
    cfg.scoring.momentum_windows.mom_3m = 63
    cfg.scoring.momentum_windows.mom_6m = 126
    cfg.scoring.momentum_windows.mom_12m = 252
    cfg.scoring.momentum_weights.mom_1m = 0.10
    cfg.scoring.momentum_weights.mom_3m = 0.20
    cfg.scoring.momentum_weights.mom_6m = 0.30
    cfg.scoring.momentum_weights.mom_12m = 0.40
    cfg.scoring.trend_sma_windows = [50, 200]
    cfg.scoring.trend_bonus_per_sma = 0.05
    cfg.scoring.volatility_window = 20
    cfg.scoring.vol_adjust = False
    cfg.universe.assets = []
    cfg.allocation.top_n = 5
    cfg.allocation.min_assets = 1
    cfg.allocation.method = "equal_weight"
    cfg.risk.max_weight_per_asset = 0.45
    cfg.risk.max_category_weights = {}
    cfg.risk.min_weight_per_selected = 0.01
    cfg.risk.risk_off_ticker = "VOO"
    cfg.risk.risk_off_window = 60
    cfg.risk.risk_off_threshold = -0.07
    cfg.risk.risk_off_equity_cap = 0.40
    cfg.strategy_variant.name = "baseline_current"
    cfg.global_settings.max_portfolio_assets = 4
    cfg.turnover.effective_limit = 0.20
    cfg.risk_mode_checks.defensive_categories = ["bond", "cash_like"]
    cfg.risk_mode_checks.enabled = False
    cfg.risk_mode_checks.risk_on_defensive_warning_threshold = 0.60
    cfg.risk_mode_checks.risk_on_defensive_review_threshold = 0.75
    cfg.pre_trade_gate.enabled = False

    result = run_backtest(prices, cfg, {"VOO": "core_equity"}, n_months=6)
    assert result.n_months == 0  # insufficient data → empty result


def test_build_backtest_markdown_empty():
    from src.backtest import build_backtest_markdown, _empty_result
    result = _empty_result("baseline_current")
    md = build_backtest_markdown([result])
    assert "バックテスト" in md


def test_build_backtest_markdown_shows_metrics():
    from src.backtest import build_backtest_markdown, BacktestResult
    results = [
        BacktestResult("baseline_current", 12, 0.08, 0.08, -0.15, 0.75, 0.58, 0.70, 0.80, 8),
        BacktestResult("cash_fallback_separated", 12, 0.12, 0.12, -0.10, 0.95, 0.67, 0.30, 0.10, 2),
    ]
    md = build_backtest_markdown(results)
    assert "baseline_current" in md
    assert "cash_fallback_separated" in md
    assert "シャープレシオ" in md
    assert "SGOV" in md
