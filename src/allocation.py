from __future__ import annotations

import pandas as pd

from src.config_loader import AllocationConfig, RiskConfig
from src.logger import logger


def compute_allocation(
    scores: pd.Series,
    indicators: pd.DataFrame,
    ticker_to_category: dict[str, str],
    alloc_cfg: AllocationConfig,
    risk_cfg: RiskConfig,
) -> dict[str, float]:
    """Translate scores into portfolio weights respecting per-asset and category caps.

    Algorithm:
    1. Select top_n tickers by score (positive scores only)
    2. Assign raw weights:
       - score_proportional: weight ∝ score
       - equal_weight: equal
       - inverse_volatility: weight ∝ 1/vol
    3. Clip at max_weight_per_asset
    4. Clip category totals at max_category_weights
    5. Normalize to 1.0
    """
    if scores.empty:
        return {}

    candidates = scores[scores > 0].sort_values(ascending=False)
    if candidates.empty:
        logger.warning("All scores are non-positive; using top 5 by raw score")
        candidates = scores.sort_values(ascending=False).head(5)

    top = candidates.head(alloc_cfg.top_n)

    if len(top) < alloc_cfg.min_assets:
        extra = scores.sort_values(ascending=False).iloc[len(top) : alloc_cfg.min_assets]
        top = pd.concat([top, extra])

    raw_weights = _raw_weights(top, indicators, alloc_cfg.method)

    weights = _apply_asset_cap(raw_weights, risk_cfg.max_weight_per_asset)
    weights = _apply_category_cap(weights, ticker_to_category, risk_cfg.max_category_weights)
    weights = _normalize(weights)
    weights = _apply_min_weight(weights, risk_cfg.min_weight_per_selected)

    logger.info(f"Allocation (pre-trim): {len(weights)} assets, top3={list(weights.items())[:3]}")
    return weights


def _raw_weights(
    top: pd.Series, indicators: pd.DataFrame, method: str
) -> dict[str, float]:
    if method == "equal_weight":
        n = len(top)
        return {t: 1.0 / n for t in top.index}

    if method == "inverse_volatility" and "vol_20d" in indicators.columns:
        vols = indicators.loc[top.index, "vol_20d"].fillna(indicators["vol_20d"].median())
        vols = vols.clip(lower=0.01)
        inv_vol = 1.0 / vols
        total = inv_vol.sum()
        return {t: float(inv_vol[t] / total) for t in top.index}

    # score_proportional (default)
    total = top.sum()
    return {t: float(top[t] / total) for t in top.index}


def _apply_asset_cap(weights: dict[str, float], cap: float) -> dict[str, float]:
    adjusted = dict(weights)
    iterations = 0
    while True:
        over = {t: w for t, w in adjusted.items() if w > cap}
        if not over or iterations > 20:
            break
        excess = sum(w - cap for w in over.values())
        for t in over:
            adjusted[t] = cap
        under = {t: w for t, w in adjusted.items() if w < cap}
        if not under:
            break
        total_under = sum(under.values())
        for t in under:
            adjusted[t] += excess * (under[t] / total_under)
        iterations += 1
    return adjusted


def _apply_category_cap(
    weights: dict[str, float],
    ticker_to_category: dict[str, str],
    max_cat: dict[str, float],
) -> dict[str, float]:
    if not max_cat:
        return weights

    adjusted = dict(weights)

    for _ in range(100):
        excess = 0.0
        excess_tickers: set[str] = set()

        for cat, cap in max_cat.items():
            cat_tickers = [t for t, c in ticker_to_category.items() if c == cat and t in adjusted]
            total_cat = sum(adjusted[t] for t in cat_tickers)
            if total_cat <= cap + 1e-10:
                continue
            excess += total_cat - cap
            scale = cap / total_cat
            for t in cat_tickers:
                adjusted[t] *= scale
            excess_tickers.update(cat_tickers)

        if excess < 1e-10:
            break

        free_tickers = [t for t in adjusted if t not in excess_tickers]
        total_free = sum(adjusted[t] for t in free_tickers)
        if total_free <= 0:
            break
        for t in free_tickers:
            adjusted[t] += excess * (adjusted[t] / total_free)

    return adjusted


def resolve_max_assets(n_eligible: int, max_portfolio_assets: int) -> int:
    """Determine the effective asset count cap based on eligible ETF count.

    eligible >= 8  → max_portfolio_assets
    eligible 4–7   → 3
    eligible 1–3   → n_eligible (hold all)
    eligible == 0  → 1 (fallback cash slot)
    """
    if n_eligible == 0:
        return 1
    if n_eligible >= 8:
        return max_portfolio_assets
    if n_eligible >= 4:
        return min(3, max_portfolio_assets)
    return n_eligible


def trim_to_max_assets(weights: dict[str, float], max_assets: int) -> dict[str, float]:
    """Keep only the top max_assets positions by weight and renormalize."""
    if len(weights) <= max_assets:
        return weights
    top = dict(sorted(weights.items(), key=lambda x: -x[1])[:max_assets])
    return _normalize(top)


def _normalize(weights: dict[str, float]) -> dict[str, float]:
    total = sum(weights.values())
    if total == 0:
        return weights
    return {t: w / total for t, w in weights.items()}


def _apply_min_weight(weights: dict[str, float], min_w: float) -> dict[str, float]:
    below = [t for t, w in weights.items() if w < min_w]
    if not below:
        return weights

    adjusted = dict(weights)
    for t in below:
        del adjusted[t]

    return _normalize(adjusted)
