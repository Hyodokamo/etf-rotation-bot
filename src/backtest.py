"""Phase 2.8: Simplified monthly backtest for strategy variant comparison.

Simulates monthly portfolio rebalancing without turnover limits.
Each month: compute indicators/scores → allocate → track next-month return.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from src.allocation import compute_allocation, resolve_max_assets, trim_to_max_assets
from src.asset_role import filter_ranking_scores
from src.config_loader import AppConfig
from src.indicators import compute_indicators
from src.logger import logger
from src.pre_trade_gate import run_pre_trade_gate
from src.risk_gate import apply_risk_gate, evaluate_risk_gate
from src.risk_mode_check import check_risk_mode_consistency
from src.scoring import compute_scores

# Minimum trading-day history required before computing the first allocation.
_MIN_HISTORY_DAYS = 270  # ~252 + buffer


@dataclass
class BacktestResult:
    variant_name: str
    n_months: int
    cumulative_return: float
    annual_return: float
    max_drawdown: float
    sharpe_ratio: float
    win_rate: float
    avg_defensive_weight: float
    sgov_adoption_rate: float
    pre_trade_gate_fail_count: int
    monthly_returns: list[float] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "variant_name": self.variant_name,
            "n_months": self.n_months,
            "cumulative_return": round(self.cumulative_return, 4),
            "annual_return": round(self.annual_return, 4),
            "max_drawdown": round(self.max_drawdown, 4),
            "sharpe_ratio": round(self.sharpe_ratio, 4),
            "win_rate": round(self.win_rate, 4),
            "avg_defensive_weight": round(self.avg_defensive_weight, 4),
            "sgov_adoption_rate": round(self.sgov_adoption_rate, 4),
            "pre_trade_gate_fail_count": self.pre_trade_gate_fail_count,
        }


def run_backtest(
    prices: pd.DataFrame,
    cfg: AppConfig,
    ticker_to_category: dict[str, str],
    variant_name: str = "baseline_current",
    n_months: int = 12,
) -> BacktestResult:
    """Simulate monthly rebalancing and compute performance metrics.

    Args:
        prices: Full price history DataFrame (date index, ticker columns).
        cfg: App configuration.
        ticker_to_category: Ticker → category mapping.
        variant_name: Strategy variant to simulate.
        n_months: Number of months to simulate (uses last n_months+1 month-ends).

    Returns:
        BacktestResult with aggregated metrics.
    """
    prices = prices.sort_index()

    # Month-end dates available in prices
    month_ends = prices.resample("ME").last().dropna(how="all").index
    if len(month_ends) < 2:
        logger.warning("Insufficient monthly data for backtest.")
        return _empty_result(variant_name)

    # Use last (n_months + 1) month-ends: n_months allocation points + 1 endpoint
    month_ends = month_ends[-(n_months + 1):]

    monthly_returns: list[float] = []
    monthly_weights: list[dict[str, float]] = []
    sgov_adoptions = 0
    gate_fails = 0
    def_cats = set(cfg.risk_mode_checks.defensive_categories)

    for i in range(len(month_ends) - 1):
        alloc_date = month_ends[i]
        eval_date = month_ends[i + 1]

        prices_slice = prices.loc[:alloc_date]
        if len(prices_slice) < _MIN_HISTORY_DAYS:
            logger.debug(f"Skipping {alloc_date}: insufficient history ({len(prices_slice)} days)")
            continue

        indicators = compute_indicators(prices_slice, cfg.scoring)
        if indicators.empty:
            continue

        scores = compute_scores(indicators, cfg.scoring)
        risk_gate = evaluate_risk_gate(prices_slice, cfg.risk)

        filtered_scores, _ = filter_ranking_scores(scores, cfg, variant_name)
        if filtered_scores.empty:
            continue

        raw_weights = compute_allocation(
            scores=filtered_scores,
            indicators=indicators.loc[indicators.index.intersection(filtered_scores.index)],
            ticker_to_category=ticker_to_category,
            alloc_cfg=cfg.allocation,
            risk_cfg=cfg.risk,
        )
        weights = apply_risk_gate(raw_weights, ticker_to_category, risk_gate, cfg.risk.risk_off_equity_cap)

        n_eligible = int((filtered_scores > 0).sum())
        max_assets = resolve_max_assets(n_eligible, cfg.global_settings.max_portfolio_assets)
        weights = trim_to_max_assets(weights, max_assets)

        if not weights:
            continue

        # Compute next-month return (no turnover limit in backtest)
        period_return = 0.0
        for ticker, w in weights.items():
            if ticker not in prices.columns:
                continue
            p_start_series = prices.loc[:alloc_date, ticker].dropna()
            p_end_series = prices.loc[:eval_date, ticker].dropna()
            if p_start_series.empty or p_end_series.empty:
                continue
            ret = p_end_series.iloc[-1] / p_start_series.iloc[-1] - 1.0
            period_return += w * float(ret)

        monthly_returns.append(period_return)
        monthly_weights.append(weights)

        if "SGOV" in weights:
            sgov_adoptions += 1

        # Pre-trade gate check
        rmc = check_risk_mode_consistency(
            weights=weights,
            ticker_to_category=ticker_to_category,
            risk_off=risk_gate.risk_off,
            cfg=cfg.risk_mode_checks,
        )
        gate = run_pre_trade_gate(
            weights=weights,
            ticker_to_category=ticker_to_category,
            cfg=cfg,
            turnover=None,
            risk_mode_check=rmc,
        )
        if gate.overall_status in ("FAIL", "REVIEW_REQUIRED"):
            gate_fails += 1

    if not monthly_returns:
        return _empty_result(variant_name)

    returns = pd.Series(monthly_returns)
    n = len(returns)
    cumulative = float((1 + returns).prod() - 1)
    annual_return = float((1 + cumulative) ** (12 / n) - 1) if n > 0 else 0.0

    cum_prod = (1 + returns).cumprod()
    running_max = cum_prod.cummax()
    max_dd = float(((cum_prod - running_max) / running_max).min())

    mean_r = float(returns.mean())
    std_r = float(returns.std())
    sharpe = float(mean_r / std_r * np.sqrt(12)) if std_r > 0 else 0.0

    win_rate = float((returns > 0).mean())

    def_weights = [
        sum(w for t, w in mw.items() if ticker_to_category.get(t, "") in def_cats)
        for mw in monthly_weights
    ]
    avg_def_weight = float(pd.Series(def_weights).mean()) if def_weights else 0.0

    logger.info(
        f"Backtest [{variant_name}] n={n} cumRet={cumulative:.1%} "
        f"DD={max_dd:.1%} Sharpe={sharpe:.2f} gate_fails={gate_fails}"
    )
    return BacktestResult(
        variant_name=variant_name,
        n_months=n,
        cumulative_return=cumulative,
        annual_return=annual_return,
        max_drawdown=max_dd,
        sharpe_ratio=sharpe,
        win_rate=win_rate,
        avg_defensive_weight=avg_def_weight,
        sgov_adoption_rate=sgov_adoptions / n if n > 0 else 0.0,
        pre_trade_gate_fail_count=gate_fails,
        monthly_returns=monthly_returns,
    )


def compare_backtest(
    prices: pd.DataFrame,
    cfg: AppConfig,
    ticker_to_category: dict[str, str],
    variants: list[str] | None = None,
    n_months: int = 12,
) -> list[BacktestResult]:
    """Run backtest for each variant and return results list."""
    from src.strategy_runner import COMPARISON_VARIANTS
    names = variants or COMPARISON_VARIANTS
    return [
        run_backtest(prices, cfg, ticker_to_category, v, n_months)
        for v in names
    ]


def build_backtest_markdown(results: list[BacktestResult]) -> str:
    if not results:
        return "# バックテスト結果\n\nデータ不足のためスキップされました。\n"

    lines: list[str] = []
    lines.append("## バックテスト比較（月次、ターンオーバー制限なし）")
    lines.append("")
    n = results[0].n_months
    lines.append(f"> シミュレーション期間: 直近 {n} ヶ月（注意: ターンオーバー制限なし、スリッページなし）")
    lines.append("")

    header = "| 指標 |" + "".join(f" {r.variant_name} |" for r in results)
    lines.append(header)
    lines.append("|---|" + "---|" * len(results))

    def row(label: str, values: list[str]) -> str:
        return f"| {label} |" + "".join(f" {v} |" for v in values)

    lines.append(row("累積リターン", [f"{r.cumulative_return:.1%}" for r in results]))
    lines.append(row("年率リターン", [f"{r.annual_return:.1%}" for r in results]))
    lines.append(row("最大ドローダウン", [f"{r.max_drawdown:.1%}" for r in results]))
    lines.append(row("シャープレシオ", [f"{r.sharpe_ratio:.2f}" for r in results]))
    lines.append(row("月次勝率", [f"{r.win_rate:.1%}" for r in results]))
    lines.append(row("防御資産比率（平均）", [f"{r.avg_defensive_weight:.1%}" for r in results]))
    lines.append(row("SGOV採用頻度", [f"{r.sgov_adoption_rate:.1%}" for r in results]))
    lines.append(row("Pre-Trade Gate FAIL月数", [str(r.pre_trade_gate_fail_count) for r in results]))
    lines.append("")

    return "\n".join(lines)


def save_backtest_report(
    results: list[BacktestResult],
    output_dir: str,
) -> tuple[str, str]:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    json_path = out / "backtest_comparison.json"
    json_path.write_text(
        json.dumps([r.to_dict() for r in results], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info(f"Backtest JSON saved: {json_path}")
    return str(json_path), str(json_path)


def _empty_result(variant_name: str) -> BacktestResult:
    return BacktestResult(
        variant_name=variant_name,
        n_months=0,
        cumulative_return=0.0,
        annual_return=0.0,
        max_drawdown=0.0,
        sharpe_ratio=0.0,
        win_rate=0.0,
        avg_defensive_weight=0.0,
        sgov_adoption_rate=0.0,
        pre_trade_gate_fail_count=0,
    )
