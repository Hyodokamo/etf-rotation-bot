"""Phase 5.1: deterministic signal context builder.

Builds SignalContext from market data + ETF master + portfolio context.
No LLM calls. All outputs are deterministic classification strings.

Sections:
1. market_regime    — risk_on / neutral / correction / crash / panic
2. semiconductor_stress — normal / healthy_pullback / macro_selloff / earnings_warning / structural_risk
3. etf_entry_quality   — too_early / acceptable / good / excellent
4. portfolio_risk       — low / medium / high
5. theme_conviction     — weak / moderate / strong
6. skeptic_flags        — list[str]
"""
from __future__ import annotations

from src.signals.crash_detector import _num
from src.signals.signal_models import SignalContext, SignalSide


def build_market_regime(
    market_data: dict[str, dict],
    config,
) -> str:
    """Classify current market regime from SPY/QQQ/VIX data."""
    from src.signals.crash_detector import _vix_from_data
    mt = config.market_triggers

    spy = market_data.get("SPY", {})
    qqq = market_data.get("QQQ", {}) or market_data.get("QQQM", {})

    qqq_dd = _num(qqq, "drawdown_from_52w_high_pct")
    spy_drop = _num(spy, "daily_return_pct")
    qqq_drop = _num(qqq, "daily_return_pct")
    vix_val = _vix_from_data(market_data)

    if vix_val is not None and vix_val >= mt.vix_panic_level:
        return "panic"
    if qqq_dd is not None and qqq_dd <= mt.qqq_severe_drawdown_from_high_pct:
        return "crash"
    correction_trigger = (
        (qqq_dd is not None and qqq_dd <= mt.qqq_drawdown_from_high_pct)
        or (qqq_drop is not None and qqq_drop <= mt.qqq_daily_drop_pct)
        or (vix_val is not None and vix_val >= mt.vix_stress_level)
    )
    if correction_trigger:
        return "correction"
    if spy_drop is not None and spy_drop >= 0.5:
        return "risk_on"
    return "neutral"


def build_semiconductor_stress(
    market_data: dict[str, dict],
    config,
) -> str:
    """Classify semiconductor/AI stress level from SOXX/SMH data."""
    tt = config.theme_triggers
    drops: list[float] = []
    drawdowns: list[float] = []

    for sym in ("SOXX", "SMH"):
        row = market_data.get(sym, {})
        if not row:
            continue
        d = _num(row, "daily_return_pct")
        dd = _num(row, "drawdown_from_52w_high_pct")
        if d is not None:
            drops.append(d)
        if dd is not None:
            drawdowns.append(dd)

    if not drops and not drawdowns:
        return "normal"

    min_drop = min(drops) if drops else 0.0
    min_dd = min(drawdowns) if drawdowns else 0.0

    if min_dd <= tt.semiconductor_drawdown_from_high_pct * 2:
        return "structural_risk"
    if min_drop <= tt.semiconductor_daily_drop_pct * 1.5:
        return "earnings_warning"
    if min_dd <= tt.semiconductor_drawdown_from_high_pct:
        return "macro_selloff"
    if min_drop <= tt.semiconductor_daily_drop_pct:
        return "healthy_pullback"
    return "normal"


def build_etf_entry_quality(
    symbol_row: dict,
    config,
) -> str:
    """Score entry quality for a specific ETF from its market data row."""
    if not symbol_row:
        return "acceptable"

    daily = _num(symbol_row, "daily_return_pct", 0.0)
    dd = _num(symbol_row, "drawdown_from_52w_high_pct", 0.0)
    above_200 = str(symbol_row.get("above_200dma", "")).strip().lower()
    rsi = _num(symbol_row, "rsi_14")
    vol_ratio = _num(symbol_row, "volume_ratio", 1.0)

    score = 0
    # Drawdown = opportunity
    if dd is not None:
        if dd <= -20:
            score += 2
        elif dd <= -10:
            score += 1
        elif dd >= 0:
            score -= 1
    # Today's drop = better entry price
    if daily is not None:
        if daily <= -3:
            score += 1
        elif daily >= 2:
            score -= 1
    # Below 200DMA = caution
    if above_200 in ("false", "0", "no"):
        score -= 1
    # RSI oversold = better entry
    if rsi is not None:
        if rsi <= 30:
            score += 2
        elif rsi <= 40:
            score += 1
        elif rsi >= 70:
            score -= 1
    # High volume confirms the move
    if vol_ratio is not None and vol_ratio >= 1.5:
        score += 1

    if score >= 4:
        return "excellent"
    if score >= 2:
        return "good"
    if score >= 0:
        return "acceptable"
    return "too_early"


def build_portfolio_risk(
    portfolio_context,
    config,
    symbol: str = "",
    theme: str = "",
) -> str:
    """Classify portfolio risk for adding this ETF to the AI sleeve."""
    if portfolio_context is None:
        return "low"

    ai_sleeve = portfolio_context.ai_sleeve
    total = ai_sleeve.total_budget_jpy
    invested = ai_sleeve.current_invested_jpy
    cash = ai_sleeve.current_cash_jpy
    rules = config.candidate_rules

    if total <= 0:
        return "low"

    invested_ratio = invested / total
    if cash <= rules.block_if_ai_sleeve_cash_jpy_lte:
        return "high"
    if invested_ratio >= 0.8:
        return "high"

    # Core overlap check
    if theme or symbol:
        try:
            from src.portfolio_context import detect_core_overlap
            check_theme = theme or symbol.lower()
            overlap = detect_core_overlap(check_theme, portfolio_context, symbol)
            if overlap and overlap.overlaps_with and not overlap.diversifying:
                return "high" if len(overlap.overlaps_with) >= 2 else "medium"
        except Exception:
            pass

    if invested_ratio >= 0.5:
        return "medium"
    return "low"


def build_theme_conviction(etf_entry) -> str:
    """Classify theme conviction from ETF master entry."""
    if etf_entry is None:
        return "moderate"
    dq = getattr(etf_entry, "data_quality_status", "") or ""
    if dq in ("insufficient", "needs_primary_research"):
        return "weak"
    status = getattr(etf_entry, "universe_status", "") or ""
    role = getattr(etf_entry, "role_in_ai_sleeve", "") or ""
    if status == "active_core":
        return "strong" if any(w in role for w in ("本命", "中核", "conviction")) else "moderate"
    if status in ("research", "support"):
        return "moderate"
    if status in ("fallback", "low_priority"):
        return "weak"
    return "moderate"


def build_skeptic_flags(etf_entry, symbol_row: dict) -> list[str]:
    """Build skeptic flag list from ETF master + market data."""
    flags: list[str] = []
    if etf_entry is None:
        return flags

    dq = getattr(etf_entry, "data_quality_status", "") or ""
    needs_check = getattr(etf_entry, "needs_order_screen_check", False)
    overlap = getattr(etf_entry, "expected_overlap_with_core", "") or ""
    liquidity = getattr(etf_entry, "liquidity_tier", "") or ""
    nisa = getattr(etf_entry, "nisa_usage_policy", "") or ""
    expense_raw = getattr(etf_entry, "expense_ratio", None)

    if dq == "insufficient":
        flags.append("data_quality=insufficient(veto候補)")
    elif dq in ("needs_broker_check", "needs_primary_research"):
        flags.append(f"data_quality={dq}")

    if needs_check:
        flags.append("発注前確認要(needs_order_screen_check=true)")

    if overlap in ("high", "very_high"):
        flags.append(f"コア重複={overlap}")

    if expense_raw is not None:
        try:
            if float(str(expense_raw).replace("%", "")) >= 0.70:
                flags.append(f"高コスト(expense_ratio={expense_raw})")
        except (TypeError, ValueError):
            pass

    if liquidity in ("low", "very_low"):
        flags.append(f"低流動性(liquidity_tier={liquidity})")

    if nisa in ("avoid_rotation", "avoid"):
        flags.append(f"NISA方針={nisa}")

    return flags


def build_signal_context(
    symbol: str,
    market_data: dict[str, dict],
    config,
    portfolio_context=None,
    etf_master: dict | None = None,
) -> SignalContext:
    """Build complete SignalContext for one ETF symbol. Deterministic — no LLM."""
    from src.etf_master import get_entry, build_enrichment
    from src.signals.crash_detector import detect_crash_triggers

    symbol_upper = symbol.upper()
    symbol_row = market_data.get(symbol_upper, {})

    regime = build_market_regime(market_data, config)
    semi_stress = build_semiconductor_stress(market_data, config)
    entry_quality = build_etf_entry_quality(symbol_row, config)

    master_entry = get_entry(etf_master or {}, symbol)
    theme = getattr(master_entry, "theme", "") or ""

    port_risk = build_portfolio_risk(portfolio_context, config, symbol, theme)
    theme_conv = build_theme_conviction(master_entry)
    skeptic = build_skeptic_flags(master_entry, symbol_row)
    triggers = detect_crash_triggers(market_data, config, symbol)

    enrichment: dict = {}
    if etf_master is not None:
        try:
            enrichment = build_enrichment(master_entry)
        except Exception:
            pass

    pctx_summary: dict = {}
    if portfolio_context is not None:
        s = portfolio_context.ai_sleeve
        pctx_summary = {
            "total_budget_jpy": s.total_budget_jpy,
            "current_cash_jpy": s.current_cash_jpy,
            "current_invested_jpy": s.current_invested_jpy,
            "cash_ratio": s.cash_ratio,
            "core_themes": portfolio_context.core_themes,
            "core_holding_count": len(portfolio_context.core_manual),
            "existing_related_holdings": [
                h.symbol for h in portfolio_context.existing_related_holdings
            ],
        }

    return SignalContext(
        symbol=symbol,
        signal_side=SignalSide.BUY,
        market_regime=regime,
        semiconductor_stress=semi_stress,
        etf_entry_quality=entry_quality,
        portfolio_risk=port_risk,
        theme_conviction=theme_conv,
        skeptic_flags=skeptic,
        crash_triggers=triggers,
        etf_master_metadata=enrichment,
        portfolio_context_summary=pctx_summary,
        market_data=dict(symbol_row),
    )
