import numpy as np
import pandas as pd

from src.config_loader import ScoringConfig
from src.logger import logger


def compute_scores(indicators: pd.DataFrame, cfg: ScoringConfig) -> pd.Series:
    """Compute a composite score per ticker from momentum, trend, and optionally vol.

    Steps:
    1. Rank each momentum window as a percentile (0–1, higher = better)
    2. Weighted sum of momentum ranks
    3. Apply trend bonus for each SMA the price is above
    4. Optionally adjust by inverse volatility

    Returns a Series indexed by ticker.
    """
    if indicators.empty:
        return pd.Series(dtype=float)

    w = cfg.momentum_weights
    weight_map = {
        "mom_1m": w.mom_1m,
        "mom_3m": w.mom_3m,
        "mom_6m": w.mom_6m,
        "mom_12m": w.mom_12m,
    }

    momentum_cols = [c for c in weight_map if c in indicators.columns]
    if not momentum_cols:
        raise ValueError("No momentum columns found in indicators")

    ranks = indicators[momentum_cols].rank(pct=True, na_option="bottom")
    total_weight = sum(weight_map[c] for c in momentum_cols)
    raw_mom = sum(ranks[c] * weight_map[c] for c in momentum_cols) / total_weight

    trend_bonus = pd.Series(0.0, index=indicators.index)
    for sma_w in cfg.trend_sma_windows:
        col = f"sma{sma_w}_signal"
        if col in indicators.columns:
            trend_bonus += indicators[col].fillna(0.0) * cfg.trend_bonus_per_sma

    scores = raw_mom * (1.0 + trend_bonus)

    if cfg.vol_adjust and "vol_20d" in indicators.columns:
        vol = indicators["vol_20d"].fillna(indicators["vol_20d"].median())
        vol = vol.clip(lower=0.01)
        scores = scores / vol

    scores = scores.rename("score")
    logger.info(f"Scores computed for {len(scores)} tickers (min={scores.min():.4f}, max={scores.max():.4f})")
    return scores
