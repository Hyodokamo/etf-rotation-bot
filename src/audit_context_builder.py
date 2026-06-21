from __future__ import annotations

from datetime import date

import pandas as pd

from src.config_loader import AppConfig
from src.risk_gate import EQUITY_CATEGORIES, RiskGateResult


def build_audit_context(
    cfg: AppConfig,
    weights: dict[str, float],
    scores: pd.Series,
    indicators: pd.DataFrame,
    risk_gate: RiskGateResult,
    prev_weights: dict[str, float] | None,
    turnover: float | None,
    run_date: date,
    risk_mode_check=None,
    pre_trade_gate=None,
) -> dict:
    asset_map = {a.ticker: a for a in cfg.universe.assets}
    cat_map = {a.ticker: a.category for a in cfg.universe.assets}

    allocation = []
    for ticker, weight in sorted(weights.items(), key=lambda x: -x[1]):
        asset = asset_map.get(ticker)
        entry: dict = {
            "ticker": ticker,
            "display_name": asset.display_name if asset else ticker,
            "category": asset.category if asset else "unknown",
            "weight": round(weight, 4),
            "score": round(float(scores.get(ticker, 0.0)), 4),
        }
        if ticker in indicators.index:
            ind = indicators.loc[ticker]
            entry["indicators"] = {
                k: round(float(ind[k]), 4) if not pd.isna(ind[k]) else None
                for k in ["mom_1m", "mom_3m", "mom_6m", "mom_12m", "vol_20d"]
                if k in indicators.columns
            }
        allocation.append(entry)

    eq_weight = sum(w for t, w in weights.items() if cat_map.get(t) in EQUITY_CATEGORIES)
    bond_weight = sum(w for t, w in weights.items() if cat_map.get(t) == "bond")
    cash_weight = sum(w for t, w in weights.items() if cat_map.get(t) == "cash_like")

    context: dict = {
        "run_date": run_date.isoformat(),
        "risk_mode": "risk_off" if risk_gate.risk_off else "risk_on",
        "sp500_return_60d": round(risk_gate.sp500_return, 4) if risk_gate.sp500_return is not None else None,
        "category_summary": {
            "equity": round(eq_weight, 4),
            "bond": round(bond_weight, 4),
            "cash_like": round(cash_weight, 4),
        },
        "turnover": round(turnover, 4) if turnover is not None else None,
        "allocation": allocation,
        "prev_allocation": {t: round(w, 4) for t, w in prev_weights.items()} if prev_weights else None,
        "constraints": {
            "max_weight_per_asset": cfg.risk.max_weight_per_asset,
            "max_category_weights": cfg.risk.max_category_weights,
            "effective_turnover_limit": cfg.turnover.effective_limit,
            "turnover_mode": cfg.turnover.mode_label,
        },
    }

    if risk_mode_check is not None and risk_mode_check.enabled:
        context["risk_mode_consistency"] = {
            "risk_mode": risk_mode_check.risk_mode,
            "defensive_weight": round(risk_mode_check.defensive_weight, 4),
            "status": risk_mode_check.status,
            "message": risk_mode_check.message,
        }

    if pre_trade_gate is not None and pre_trade_gate.enabled:
        context["pre_trade_gate"] = pre_trade_gate.to_dict()

    return context
