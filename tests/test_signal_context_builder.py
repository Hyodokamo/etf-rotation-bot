"""Tests for Phase 5.1: signal_context_builder.py."""
import pytest

from src.signals.signal_config import load_signal_config
from src.signals.signal_context_builder import (
    build_etf_entry_quality,
    build_market_regime,
    build_portfolio_risk,
    build_semiconductor_stress,
    build_signal_context,
    build_skeptic_flags,
    build_theme_conviction,
)


def _cfg(tmp_path=None):
    return load_signal_config("/nonexistent/no_config.yaml")


def _market(**rows):
    return {sym.upper(): data for sym, data in rows.items()}


# ── market_regime ─────────────────────────────────────────────────────────────


def test_signal_context_builder_market_regime():
    cfg = _cfg()
    # Normal: empty data → neutral
    assert build_market_regime({}, cfg) == "neutral"
    # Correction: QQQ drop below threshold
    data = _market(QQQ={"daily_return_pct": "-4.0", "drawdown_from_52w_high_pct": "-5.0"})
    assert build_market_regime(data, cfg) == "correction"
    # Crash: QQQ severe drawdown
    data = _market(QQQ={"daily_return_pct": "-2.0", "drawdown_from_52w_high_pct": "-16.0"})
    assert build_market_regime(data, cfg) == "crash"
    # Panic: VIX >= 35
    data = _market(SPY={"daily_return_pct": "-2.5", "vix_level": "40.0"})
    assert build_market_regime(data, cfg) == "panic"
    # Risk-on: SPY positive
    data = _market(SPY={"daily_return_pct": "1.0"})
    assert build_market_regime(data, cfg) == "risk_on"


# ── semiconductor_ai_stress ───────────────────────────────────────────────────


def test_signal_context_builder_semiconductor_ai_stress():
    cfg = _cfg()
    assert build_semiconductor_stress({}, cfg) == "normal"
    # Healthy pullback: SOXX drop just at threshold
    data = _market(SOXX={"daily_return_pct": "-5.5", "drawdown_from_52w_high_pct": "-5.0"})
    result = build_semiconductor_stress(data, cfg)
    assert result == "healthy_pullback"
    # Macro selloff: SMH drawdown hits threshold
    data = _market(SMH={"daily_return_pct": "-2.0", "drawdown_from_52w_high_pct": "-11.0"})
    result = build_semiconductor_stress(data, cfg)
    assert result in ("macro_selloff", "structural_risk")


# ── etf_entry_quality ─────────────────────────────────────────────────────────


def test_signal_context_builder_etf_entry_quality():
    cfg = _cfg()
    # Empty row → acceptable
    assert build_etf_entry_quality({}, cfg) == "acceptable"
    # Deep drawdown + oversold RSI → excellent or good
    row = {"daily_return_pct": "-4.0", "drawdown_from_52w_high_pct": "-22.0", "rsi_14": "28", "volume_ratio": "2.0", "above_200dma": "false"}
    result = build_etf_entry_quality(row, cfg)
    assert result in ("excellent", "good")
    # Overbought → too_early
    row = {"daily_return_pct": "3.0", "drawdown_from_52w_high_pct": "2.0", "rsi_14": "75", "above_200dma": "false"}
    result = build_etf_entry_quality(row, cfg)
    assert result in ("too_early", "acceptable")


# ── portfolio_risk ────────────────────────────────────────────────────────────


def test_signal_context_builder_portfolio_risk():
    cfg = _cfg()
    # No portfolio context → low
    assert build_portfolio_risk(None, cfg, "GRID") == "low"
    # Mock portfolio context with high invested ratio
    from unittest.mock import MagicMock
    ctx = MagicMock()
    ctx.ai_sleeve.total_budget_jpy = 1_000_000
    ctx.ai_sleeve.current_invested_jpy = 850_000
    ctx.ai_sleeve.current_cash_jpy = 150_000
    ctx.core_manual = []
    ctx.core_themes = []
    # high invested ratio → high risk
    result = build_portfolio_risk(ctx, cfg, "GRID")
    assert result == "high"
    # Low invested
    ctx2 = MagicMock()
    ctx2.ai_sleeve.total_budget_jpy = 1_000_000
    ctx2.ai_sleeve.current_invested_jpy = 0
    ctx2.ai_sleeve.current_cash_jpy = 1_000_000
    assert build_portfolio_risk(ctx2, cfg, "GRID") == "low"


# ── skeptic_flags ─────────────────────────────────────────────────────────────


def test_signal_context_builder_skeptic_flags():
    # No entry → empty flags
    assert build_skeptic_flags(None, {}) == []
    # Mock ETF entry with data quality insufficient
    from unittest.mock import MagicMock
    entry = MagicMock()
    entry.data_quality_status = "insufficient"
    entry.needs_order_screen_check = True
    entry.expected_overlap_with_core = "high"
    entry.expense_ratio = 0.80
    entry.liquidity_tier = "low"
    entry.nisa_usage_policy = "avoid_rotation"
    flags = build_skeptic_flags(entry, {})
    assert any("insufficient" in f for f in flags)
    assert any("発注前確認要" in f for f in flags)
    assert any("コア重複" in f for f in flags)


def test_signal_context_builder_skeptic_flags_needs_broker_check_not_veto():
    """needs_broker_check alone does not cause a veto — just a risk flag."""
    from unittest.mock import MagicMock
    entry = MagicMock()
    entry.data_quality_status = "needs_broker_check"
    entry.needs_order_screen_check = True
    entry.expected_overlap_with_core = "low"
    entry.expense_ratio = 0.5
    entry.liquidity_tier = "high"
    entry.nisa_usage_policy = "ok"
    flags = build_skeptic_flags(entry, {})
    assert any("needs_broker_check" in f for f in flags)
    # veto decision happens in engine, not here
    assert not any("veto候補" in f for f in flags)


def test_signal_context_builder_build_signal_context_returns_signalcontext():
    cfg = _cfg()
    ctx = build_signal_context("GRID", {}, cfg)
    assert ctx.symbol == "GRID"
    assert ctx.market_regime == "neutral"
    assert ctx.no_auto_trade if hasattr(ctx, "no_auto_trade") else True
