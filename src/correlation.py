from __future__ import annotations

import pandas as pd


def compute_correlation(prices: pd.DataFrame, window: int = 252) -> pd.DataFrame:
    """Compute rolling Pearson correlation of daily log returns over the last `window` days."""
    if prices.empty:
        return pd.DataFrame()

    log_ret = prices.apply(lambda s: s.dropna().pct_change().dropna())
    recent = log_ret.iloc[-window:]
    corr = recent.corr()
    return corr


def highly_correlated_pairs(
    corr: pd.DataFrame, threshold: float = 0.85
) -> list[tuple[str, str, float]]:
    """Return list of (ticker_a, ticker_b, correlation) pairs above threshold."""
    pairs: list[tuple[str, str, float]] = []
    tickers = corr.columns.tolist()
    for i, a in enumerate(tickers):
        for b in tickers[i + 1 :]:
            val = corr.loc[a, b]
            if not pd.isna(val) and abs(val) >= threshold:
                pairs.append((a, b, round(float(val), 4)))
    return sorted(pairs, key=lambda x: -abs(x[2]))
