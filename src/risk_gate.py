from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from src.config_loader import RiskConfig
from src.logger import logger


@dataclass
class RiskGateResult:
    risk_off: bool
    sp500_return: float | None
    message: str


def evaluate_risk_gate(prices: pd.DataFrame, cfg: RiskConfig) -> RiskGateResult:
    """Check if the market is in risk-off territory based on sp500 recent return."""
    ticker = cfg.risk_off_ticker
    if ticker not in prices.columns:
        logger.warning(f"Risk-off ticker {ticker} not in prices, risk gate skipped")
        return RiskGateResult(risk_off=False, sp500_return=None, message="Risk gate skipped (no data)")

    series = prices[ticker].dropna()
    window = cfg.risk_off_window

    if len(series) <= window:
        logger.warning(f"Insufficient data for risk gate calculation ({len(series)} rows)")
        return RiskGateResult(risk_off=False, sp500_return=None, message="Risk gate skipped (insufficient data)")

    ret = float(series.iloc[-1] / series.iloc[-window - 1] - 1.0)
    is_risk_off = ret < cfg.risk_off_threshold

    msg = (
        f"Risk-OFF: {ticker} {window}d return = {ret:.2%} < threshold {cfg.risk_off_threshold:.2%}"
        if is_risk_off
        else f"Risk-ON: {ticker} {window}d return = {ret:.2%}"
    )
    logger.info(msg)
    return RiskGateResult(risk_off=is_risk_off, sp500_return=ret, message=msg)


EQUITY_CATEGORIES = {"core_equity", "growth_equity", "theme_equity", "emerging_equity", "small_cap", "factor", "sector"}


def apply_risk_gate(
    weights: dict[str, float],
    ticker_to_category: dict[str, str],
    gate: RiskGateResult,
    equity_cap: float,
) -> dict[str, float]:
    """Cap total equity weight when risk-off, redistributing excess within the current portfolio.

    Excess weight is redistributed only to tickers already held (cash_like first,
    then other non-equity proportionally). No new tickers are added to the portfolio.
    """
    if not gate.risk_off:
        return weights

    adjusted = dict(weights)
    equity_tickers = [t for t in adjusted if ticker_to_category.get(t) in EQUITY_CATEGORIES]
    total_equity = sum(adjusted[t] for t in equity_tickers)

    if total_equity <= equity_cap:
        return adjusted

    scale = equity_cap / total_equity
    excess = total_equity - equity_cap

    for t in equity_tickers:
        adjusted[t] *= scale

    # Redistribute excess to cash_like positions already in the portfolio
    cash_in_portfolio = [t for t in adjusted if ticker_to_category.get(t) == "cash_like"]
    if cash_in_portfolio:
        per_cash = excess / len(cash_in_portfolio)
        for t in cash_in_portfolio:
            adjusted[t] += per_cash
    else:
        # No cash held — redistribute proportionally to non-equity positions
        non_equity = [t for t in adjusted if t not in equity_tickers]
        total_non_equity = sum(adjusted[t] for t in non_equity)
        if total_non_equity > 0:
            for t in non_equity:
                adjusted[t] += excess * (adjusted[t] / total_non_equity)
        # else: all-equity portfolio; normalize will handle the residual

    # Always normalize to guarantee weights sum exactly to 1.0
    total = sum(adjusted.values())
    if total > 0:
        adjusted = {t: w / total for t, w in adjusted.items()}

    logger.info(f"Risk gate applied: equity scaled from {total_equity:.1%} to {equity_cap:.1%}")
    return adjusted
