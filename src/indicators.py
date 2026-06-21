from __future__ import annotations

import numpy as np
import pandas as pd

from src.config_loader import ScoringConfig
from src.logger import logger


def compute_indicators(prices: pd.DataFrame, cfg: ScoringConfig) -> pd.DataFrame:
    """Compute momentum, trend, and volatility indicators for each ticker.

    Returns a DataFrame indexed by ticker with columns:
        mom_1m, mom_3m, mom_6m, mom_12m,
        sma50_signal, sma200_signal,
        vol_20d
    """
    tickers = prices.columns.tolist()
    records: list[dict] = []

    w = cfg.momentum_windows
    windows = {
        "mom_1m": w.mom_1m,
        "mom_3m": w.mom_3m,
        "mom_6m": w.mom_6m,
        "mom_12m": w.mom_12m,
    }

    for ticker in tickers:
        series = prices[ticker].dropna()
        if len(series) < w.mom_12m:
            logger.debug(f"{ticker}: insufficient rows for 12m momentum, skipping")
            continue

        row: dict = {"ticker": ticker}

        for col, n in windows.items():
            if len(series) > n:
                row[col] = series.iloc[-1] / series.iloc[-n - 1] - 1.0
            else:
                row[col] = np.nan

        for sma_w in cfg.trend_sma_windows:
            col = f"sma{sma_w}_signal"
            if len(series) >= sma_w:
                sma = series.rolling(sma_w).mean().iloc[-1]
                row[col] = 1.0 if series.iloc[-1] > sma else 0.0
            else:
                row[col] = np.nan

        vol_w = cfg.volatility_window
        if len(series) > vol_w:
            log_ret = np.log(series / series.shift(1)).dropna()
            row["vol_20d"] = float(log_ret.iloc[-vol_w:].std() * np.sqrt(252))
        else:
            row["vol_20d"] = np.nan

        records.append(row)

    df = pd.DataFrame(records).set_index("ticker") if records else pd.DataFrame()
    return df
