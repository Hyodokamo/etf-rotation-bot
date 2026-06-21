"""Risk-ON category constraint adjustment (allocation MVP).

Applied to the FINAL allocation (after trim_to_max_assets) and BEFORE the
Pre-Trade Gate. In Risk-ON, caps fixed_income / defensive category totals at the
configured limits and redistributes the excess to allowed (non-defensive) assets
by score, respecting the per-asset cap and keeping weights summed to 1.0.

This is a *preventive nudge*, not a replacement for the Pre-Trade Gate:
- The Pre-Trade Gate remains the authoritative last line of defense.
- If the final asset set cannot absorb the excess (e.g. only one equity survived
  the max-asset trim), the defensive total is reduced only as far as capacity
  allows and any residual violation is left for the gate to FAIL on (honest).

Advisory only — never trades, never sizes orders, never touches Signal Review.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ConstraintAdjustment:
    applied: bool = False
    risk_off: bool = False
    fixed_income_before: float = 0.0
    fixed_income_after: float = 0.0
    defensive_before: float = 0.0
    defensive_after: float = 0.0
    fully_satisfied: bool = True
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "applied": self.applied,
            "risk_off": self.risk_off,
            "fixed_income_before": round(self.fixed_income_before, 4),
            "fixed_income_after": round(self.fixed_income_after, 4),
            "defensive_before": round(self.defensive_before, 4),
            "defensive_after": round(self.defensive_after, 4),
            "fully_satisfied": self.fully_satisfied,
            "notes": self.notes,
        }

    def slack_line(self) -> str | None:
        """One concise line for Slack/report, or None when nothing was adjusted."""
        if not self.applied:
            return None
        tail = "" if self.fully_satisfied else "（容量不足で一部のみ・Gateで最終判定）"
        return (
            f"配分調整: Risk-ON防御資産上限により "
            f"fixed_income {self.fixed_income_before:.1%}→{self.fixed_income_after:.1%}"
            f"／defensive {self.defensive_before:.1%}→{self.defensive_after:.1%}"
            f"（許容カテゴリへ再配分）{tail}"
        )


def _group_total(weights: dict[str, float], tickers: set[str]) -> float:
    return sum(w for t, w in weights.items() if t in tickers)


def _water_fill(
    new: dict[str, float],
    amount: float,
    rooms: dict[str, float],
    score_w: dict[str, float],
) -> float:
    """Distribute ``amount`` into ``new`` across keys in ``rooms`` by score, capped
    by each key's remaining room. Returns the unplaceable leftover (>=0)."""
    rooms = dict(rooms)
    remaining = amount
    for _ in range(64):
        if remaining <= 1e-12:
            break
        active = [t for t, r in rooms.items() if r > 1e-12]
        if not active:
            break
        ws = {t: max(score_w.get(t, 0.0), 0.0) for t in active}
        wtot = sum(ws.values())
        if wtot <= 0:
            ws = {t: 1.0 for t in active}
            wtot = float(len(active))
        progressed = 0.0
        for t in active:
            share = remaining * (ws[t] / wtot)
            give = min(share, rooms[t])
            new[t] = new.get(t, 0.0) + give
            rooms[t] -= give
            progressed += give
        remaining -= progressed
        if progressed <= 1e-15:
            break
    return max(remaining, 0.0)


def _enforce_cap(
    weights: dict[str, float],
    group: set[str],
    cap: float,
    allowed: set[str],
    scores: dict[str, float],
    max_w: float,
) -> tuple[dict[str, float], float, float]:
    """Reduce ``group`` total to <= cap, moving the placeable excess into ``allowed``.

    Returns (new_weights, group_before, group_after). Sum is preserved.
    """
    before = _group_total(weights, group)
    if before <= cap + 1e-9 or before <= 0:
        return weights, before, before

    rooms = {t: max(0.0, max_w - weights[t]) for t in allowed if t in weights}
    excess = before - cap
    capacity = sum(rooms.values())
    placeable = min(excess, capacity)

    new = dict(weights)
    scale = (before - placeable) / before
    for t in group:
        if t in new:
            new[t] *= scale

    if placeable > 1e-12 and rooms:
        _water_fill(new, placeable, rooms, {t: scores.get(t, 0.0) for t in rooms})

    after = _group_total(new, group)
    return new, before, after


def apply_allocation_constraints(
    weights: dict[str, float],
    ticker_to_category: dict[str, str],
    scores,
    cfg,
    *,
    risk_off: bool,
    max_weight_per_asset: float,
    defensive_categories: list[str] | None = None,
) -> tuple[dict[str, float], ConstraintAdjustment]:
    """Apply the Risk-ON fixed_income / defensive caps with score-weighted redistribution.

    No-op when disabled or in Risk-OFF (Risk-ON limits must not apply in Risk-OFF).
    Preserves the weight sum; respects ``max_weight_per_asset``; leaves an honest
    residual for the Pre-Trade Gate when redistribution capacity is insufficient.
    """
    adj = ConstraintAdjustment(risk_off=risk_off)

    if cfg is None or not getattr(cfg, "enabled", False):
        return weights, adj
    if risk_off:
        adj.notes.append("Risk-OFF — Risk-ON制約は適用しません。")
        return weights, adj
    if not weights:
        return weights, adj

    # score accessor works for pandas Series or plain dict
    def _score(t: str) -> float:
        try:
            v = scores.get(t, 0.0)
        except AttributeError:
            v = 0.0
        try:
            return float(v)
        except (TypeError, ValueError):
            return 0.0

    score_map = {t: _score(t) for t in weights}

    fi_cats = set(getattr(cfg, "fixed_income_categories", ["bond", "cash_like"]))
    def_cats = set(
        defensive_categories
        or getattr(cfg, "defensive_categories", None)
        or ["bond", "cash_like", "commodity", "fx"]
    )
    exclude = set(getattr(cfg.redistribution, "exclude_categories", []) or def_cats)

    max_fi = float(cfg.risk_on.max_fixed_income_weight)
    max_def = float(cfg.risk_on.max_defensive_weight)

    fi_tickers = {t for t in weights if ticker_to_category.get(t) in fi_cats}
    def_tickers = {t for t in weights if ticker_to_category.get(t) in def_cats}
    allowed = {t for t in weights if ticker_to_category.get(t) not in exclude}

    adj.fixed_income_before = _group_total(weights, fi_tickers)
    adj.defensive_before = _group_total(weights, def_tickers)

    over_fi = adj.fixed_income_before > max_fi + 1e-9
    over_def = adj.defensive_before > max_def + 1e-9
    if not over_fi and not over_def:
        adj.fixed_income_after = adj.fixed_income_before
        adj.defensive_after = adj.defensive_before
        return weights, adj

    if not allowed:
        adj.applied = False
        adj.fully_satisfied = False
        adj.fixed_income_after = adj.fixed_income_before
        adj.defensive_after = adj.defensive_before
        adj.notes.append("再配分先（許容カテゴリ）が無いため調整せず。Pre-Trade Gateで判定。")
        return weights, adj

    new = dict(weights)
    # Defensive is the broader group — enforce it first, then fixed_income.
    if over_def:
        new, _, _ = _enforce_cap(new, def_tickers, max_def, allowed, score_map, max_weight_per_asset)
    if _group_total(new, fi_tickers) > max_fi + 1e-9:
        new, _, _ = _enforce_cap(new, fi_tickers, max_fi, allowed, score_map, max_weight_per_asset)

    adj.applied = True
    adj.fixed_income_after = _group_total(new, fi_tickers)
    adj.defensive_after = _group_total(new, def_tickers)
    adj.fully_satisfied = (
        adj.fixed_income_after <= max_fi + 1e-6 and adj.defensive_after <= max_def + 1e-6
    )
    if not adj.fully_satisfied:
        adj.notes.append(
            "再配分容量が不足し、防御資産上限まで届きませんでした。Pre-Trade Gateで最終判定します。"
        )
    return new, adj
