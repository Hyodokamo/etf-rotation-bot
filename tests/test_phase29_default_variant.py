"""Tests for Phase 2.9: cash_fallback_separated as default strategy."""
import json
import tempfile
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd
import pytest

from src.config_loader import load_config
from src.asset_role import filter_ranking_scores, get_ranking_tickers
from src.report_builder import build_report
from src.slack_client import build_slack_summary
from src.risk_gate import RiskGateResult
from src.risk_mode_check import RiskModeCheckResult
from src.pre_trade_gate import PreTradeGateResult


# ── fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def cfg():
    return load_config("config.yaml")


def _risk_gate(risk_off: bool = False) -> RiskGateResult:
    return RiskGateResult(risk_off=risk_off, sp500_return=0.05, message="ok")


def _rmc(status: str = "PASS", dw: float = 0.20) -> RiskModeCheckResult:
    return RiskModeCheckResult(
        enabled=True, risk_mode="RISK_ON", defensive_weight=dw,
        status=status, message="ok",
    )


def _gate(status: str = "PASS") -> PreTradeGateResult:
    return PreTradeGateResult(overall_status=status, checks=[], enabled=True)


def _make_prices(tickers: list[str], n: int = 300) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=n, freq="B")
    return pd.DataFrame({t: [100.0 + i * 0.1 for i in range(n)] for t in tickers}, index=idx)


def _make_scores(tickers: list[str]) -> pd.Series:
    return pd.Series({t: float(i + 1) for i, t in enumerate(tickers)})


def _make_indicators(tickers: list[str]) -> pd.DataFrame:
    return pd.DataFrame({"vol_20d": [0.15] * len(tickers)}, index=tickers)


# ── 1. Default variant is cash_fallback_separated ─────────────────────────────


def test_config_default_variant_is_cash_fallback_separated(cfg):
    assert cfg.strategy_variant.name == "cash_fallback_separated"


# ── 2. Normal run: include_in_momentum_ranking=false ETFs excluded ─────────────


def test_sgov_excluded_from_ranking_in_normal_run(cfg):
    all_tickers = [a.ticker for a in cfg.production_assets()]
    scores = _make_scores(all_tickers)
    filtered, excluded = filter_ranking_scores(scores, cfg)
    assert "SGOV" not in filtered.index
    assert "SGOV" in excluded


def test_uup_excluded_from_ranking_in_normal_run(cfg):
    all_tickers = [a.ticker for a in cfg.production_assets()]
    scores = _make_scores(all_tickers)
    filtered, excluded = filter_ranking_scores(scores, cfg)
    assert "UUP" not in filtered.index
    assert "UUP" in excluded


# ── 3. Comparison mode: baseline_current still available ──────────────────────


def test_compare_variants_includes_baseline_current(cfg):
    from src.strategy_runner import COMPARISON_VARIANTS
    assert "baseline_current" in COMPARISON_VARIANTS
    assert "cash_fallback_separated" in COMPARISON_VARIANTS


def test_get_ranking_tickers_baseline_includes_sgov(cfg):
    tickers = get_ranking_tickers(cfg, variant_name="baseline_current")
    assert "SGOV" in tickers


def test_get_ranking_tickers_cash_fallback_excludes_sgov(cfg):
    tickers = get_ranking_tickers(cfg, variant_name="cash_fallback_separated")
    assert "SGOV" not in tickers


def test_get_ranking_tickers_cash_fallback_excludes_uup(cfg):
    tickers = get_ranking_tickers(cfg, variant_name="cash_fallback_separated")
    assert "UUP" not in tickers


# ── 4. run_log.json contains strategy_variant ─────────────────────────────────


def test_save_run_log_includes_strategy_variant():
    from main import _save_run_log
    with tempfile.TemporaryDirectory() as tmp:
        _save_run_log(
            output_dir=tmp,
            run_date=date(2026, 5, 26),
            weights={"VOO": 0.60, "BND": 0.40},
            audit_result=None,
            elapsed_ok=True,
            strategy_variant="cash_fallback_separated",
        )
        log = json.loads((Path(tmp) / "run_log.json").read_text(encoding="utf-8"))
        assert log["strategy_variant"] == "cash_fallback_separated"


def test_save_run_log_strategy_variant_none_when_not_passed():
    from main import _save_run_log
    with tempfile.TemporaryDirectory() as tmp:
        _save_run_log(
            output_dir=tmp,
            run_date=date(2026, 5, 26),
            weights={"VOO": 0.60, "BND": 0.40},
            audit_result=None,
            elapsed_ok=True,
        )
        log = json.loads((Path(tmp) / "run_log.json").read_text(encoding="utf-8"))
        assert "strategy_variant" in log
        assert log["strategy_variant"] is None


# ── 5. report.md contains strategy_variant ────────────────────────────────────


def test_report_contains_strategy_variant(cfg):
    tickers = get_ranking_tickers(cfg, variant_name="cash_fallback_separated")[:4]
    weights = {t: 0.25 for t in tickers}
    scores = _make_scores(tickers)
    indicators = _make_indicators(tickers)
    prices = _make_prices(tickers)

    report = build_report(
        cfg=cfg,
        weights=weights,
        scores=scores,
        indicators=indicators,
        prices=prices,
        risk_gate=_risk_gate(),
        prev_weights=None,
        turnover=None,
        run_date=date(2026, 5, 26),
        strategy_variant="cash_fallback_separated",
    )
    assert "cash_fallback_separated" in report
    assert "戦略バリアント" in report


def test_report_strategy_variant_falls_back_to_cfg(cfg):
    tickers = get_ranking_tickers(cfg, variant_name="cash_fallback_separated")[:4]
    weights = {t: 0.25 for t in tickers}
    scores = _make_scores(tickers)
    indicators = _make_indicators(tickers)
    prices = _make_prices(tickers)

    report = build_report(
        cfg=cfg,
        weights=weights,
        scores=scores,
        indicators=indicators,
        prices=prices,
        risk_gate=_risk_gate(),
        prev_weights=None,
        turnover=None,
        run_date=date(2026, 5, 26),
        # strategy_variant not passed → falls back to cfg.strategy_variant.name
    )
    assert "cash_fallback_separated" in report


# ── 6. Slack contains strategy_variant ────────────────────────────────────────


def test_slack_contains_strategy_variant():
    msg = build_slack_summary(
        weights={"VOO": 0.60, "BND": 0.40},
        risk_off=False,
        turnover=0.10,
        report_path="outputs/report.md",
        strategy_variant="cash_fallback_separated",
    )
    assert "cash_fallback_separated" in msg


def test_slack_without_strategy_variant_omits_line():
    msg = build_slack_summary(
        weights={"VOO": 0.60, "BND": 0.40},
        risk_off=False,
        turnover=0.10,
        report_path="outputs/report.md",
        strategy_variant=None,
    )
    assert "戦略:" not in msg


# ── 7. Existing tests still pass (smoke) ─────────────────────────────────────


def test_baseline_variant_still_runnable_in_comparison(cfg):
    """Ensure baseline_current is still a valid variant name (not removed)."""
    from src.asset_role import filter_ranking_scores
    all_tickers = [a.ticker for a in cfg.production_assets()]
    scores = _make_scores(all_tickers)
    filtered, excluded = filter_ranking_scores(scores, cfg, variant_name="baseline_current")
    assert "SGOV" in filtered.index
    assert excluded == []
