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
    """Cap total equity weight when risk-off, redistributing to cash-like assets."""
    if not gate.risk_off:
        return weights

    adjusted = dict(weights)
    equity_tickers = [t for t, c in ticker_to_category.items() if c in EQUITY_CATEGORIES]
    total_equity = sum(adjusted.get(t, 0.0) for t in equity_tickers)

    if total_equity <= equity_cap:
        return adjusted

    scale = equity_cap / total_equity
    excess = total_equity - equity_cap
    cash_tickers = [t for t, c in ticker_to_category.items() if c == "cash_like"]

    for t in equity_tickers:
        adjusted[t] = adjusted.get(t, 0.0) * scale

    if cash_tickers:
        per_cash = excess / len(cash_tickers)
        for t in cash_tickers:
            adjusted[t] = adjusted.get(t, 0.0) + per_cash

    logger.info(f"Risk gate applied: equity scaled from {total_equity:.1%} to {equity_cap:.1%}")
    return adjusted
