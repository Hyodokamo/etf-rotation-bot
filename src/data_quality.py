from __future__ import annotations

import pandas as pd

from src.logger import logger


class DataQualityReport:
    def __init__(self) -> None:
        self.warnings: list[str] = []
        self.dropped_tickers: list[str] = []


def check_and_clean(
    prices: pd.DataFrame, min_history_days: int = 260
) -> tuple[pd.DataFrame, DataQualityReport]:
    """Remove tickers with insufficient history or too many gaps.

    Returns cleaned DataFrame and a report of issues found.
    """
    report = DataQualityReport()

    to_drop: list[str] = []
    for ticker in prices.columns:
        series = prices[ticker].dropna()

        if len(series) < min_history_days:
            msg = f"{ticker}: only {len(series)} days of data (need {min_history_days}), dropping"
            logger.warning(msg)
            report.warnings.append(msg)
            to_drop.append(ticker)
            continue

        gap_ratio = prices[ticker].isna().mean()
        if gap_ratio > 0.05:
            msg = f"{ticker}: {gap_ratio:.1%} missing values — using forward-fill"
            logger.warning(msg)
            report.warnings.append(msg)

    cleaned = prices.drop(columns=to_drop).ffill()
    report.dropped_tickers = to_drop

    if to_drop:
        logger.warning(f"Dropped tickers due to insufficient data: {to_drop}")

    return cleaned, report
