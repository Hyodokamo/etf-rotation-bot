from __future__ import annotations

from src.logger import logger


def compute_turnover(
    new_weights: dict[str, float], prev_weights: dict[str, float]
) -> float:
    """Compute one-way turnover: sum of positive weight increases."""
    all_tickers = set(new_weights) | set(prev_weights)
    buys = sum(
        max(new_weights.get(t, 0.0) - prev_weights.get(t, 0.0), 0.0)
        for t in all_tickers
    )
    return buys


def apply_turnover_limit(
    new_weights: dict[str, float],
    prev_weights: dict[str, float],
    max_turnover: float,
) -> dict[str, float]:
    """Blend new_weights toward prev_weights to keep turnover ≤ max_turnover.

    If current turnover already within limit, returns new_weights unchanged.
    Uses binary search on blend alpha to find the minimal alpha satisfying the constraint.
    """
    current_to = compute_turnover(new_weights, prev_weights)
    if current_to <= max_turnover:
        return new_weights

    # Binary search for alpha in [0, 1] where alpha=1 means full new portfolio
    lo, hi = 0.0, 1.0
    for _ in range(50):
        mid = (lo + hi) / 2
        blended = _blend(new_weights, prev_weights, mid)
        to = compute_turnover(blended, prev_weights)
        if to <= max_turnover:
            lo = mid
        else:
            hi = mid

    alpha = lo
    result = _blend(new_weights, prev_weights, alpha)
    actual_to = compute_turnover(result, prev_weights)
    logger.info(
        f"Turnover limited: {current_to:.1%} → {actual_to:.1%} (alpha={alpha:.3f})"
    )
    return result


def _blend(new: dict[str, float], prev: dict[str, float], alpha: float) -> dict[str, float]:
    """Linearly interpolate between prev (alpha=0) and new (alpha=1)."""
    all_tickers = set(new) | set(prev)
    blended = {
        t: alpha * new.get(t, 0.0) + (1 - alpha) * prev.get(t, 0.0)
        for t in all_tickers
    }
    total = sum(blended.values())
    if total > 0:
        blended = {t: w / total for t, w in blended.items()}
    return {t: w for t, w in blended.items() if w > 1e-6}
