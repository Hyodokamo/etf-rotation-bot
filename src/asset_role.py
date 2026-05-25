"""Phase 2.7: Asset role filtering for strategy variants.

Controls which ETFs participate in momentum ranking vs. serve as fallback/defensive
assets based on per-asset role configuration and the active strategy variant.
"""
from __future__ import annotations

import pandas as pd

from src.config_loader import AppConfig


def filter_ranking_scores(
    scores: pd.Series,
    cfg: AppConfig,
    variant_name: str | None = None,
) -> tuple[pd.Series, list[str]]:
    """Filter scores based on strategy variant and per-asset role flags.

    Returns:
        (filtered_scores, excluded_tickers)
        - filtered_scores: scores for assets eligible for momentum ranking
        - excluded_tickers: tickers removed from ranking (not a superset of fallback)

    In ``baseline_current`` mode, scores are returned unchanged.
    In any other mode, assets with ``include_in_momentum_ranking=False`` are excluded
    from the ranking pool.  Fallback assets are re-injected when the eligible count
    drops below ``cfg.allocation.min_assets``.
    """
    effective_variant = variant_name or cfg.strategy_variant.name

    if effective_variant == "baseline_current":
        return scores, []

    asset_map = {a.ticker: a for a in cfg.universe.assets}
    excluded: list[str] = []
    fallback: list[str] = []

    for ticker in list(scores.index):
        asset = asset_map.get(ticker)
        if asset and not asset.include_in_momentum_ranking:
            excluded.append(ticker)
            if asset.include_in_fallback:
                fallback.append(ticker)

    filtered = scores.drop(excluded, errors="ignore")

    # Re-inject fallback tickers only when NO positive-score assets remain
    eligible = int((filtered > 0).sum())
    if eligible == 0 and fallback:
        positive = filtered[filtered > 0]
        min_score = float(positive.min()) * 0.5 if not positive.empty else 1e-3
        for t in fallback:
            if t in scores.index and t not in filtered.index:
                filtered[t] = max(min_score, 1e-4)

    return filtered, excluded


def get_ranking_tickers(cfg: AppConfig, variant_name: str | None = None) -> list[str]:
    """Return the list of tickers eligible for momentum ranking for this variant."""
    effective_variant = variant_name or cfg.strategy_variant.name
    if effective_variant == "baseline_current":
        return [a.ticker for a in cfg.production_assets()]
    return [
        a.ticker for a in cfg.production_assets()
        if a.include_in_momentum_ranking
    ]


def get_fallback_tickers(cfg: AppConfig) -> list[str]:
    """Return tickers designated as fallback assets (e.g., SGOV)."""
    return [a.ticker for a in cfg.production_assets() if a.include_in_fallback]
