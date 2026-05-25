"""Phase 2.8: Strategy variant comparison runner.

Runs the allocation pipeline for multiple strategy variants using the same
pre-loaded market data, then generates a side-by-side comparison report.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import pandas as pd

from src.allocation import compute_allocation, resolve_max_assets, trim_to_max_assets
from src.asset_role import filter_ranking_scores, get_fallback_tickers
from src.config_loader import AppConfig
from src.indicators import compute_indicators
from src.logger import logger
from src.pre_trade_gate import PreTradeGateResult, run_pre_trade_gate
from src.risk_gate import RiskGateResult, apply_risk_gate, evaluate_risk_gate
from src.risk_mode_check import RiskModeCheckResult, check_risk_mode_consistency
from src.scoring import compute_scores
from src.turnover import apply_turnover_limit, compute_turnover


COMPARISON_VARIANTS = ["baseline_current", "cash_fallback_separated"]


@dataclass
class VariantResult:
    variant_name: str
    weights: dict[str, float]
    excluded_tickers: list[str]
    n_eligible: int
    turnover: float | None
    proposed_turnover: float | None
    risk_gate: RiskGateResult
    risk_mode_check: RiskModeCheckResult
    pre_trade_gate: PreTradeGateResult
    defensive_weight: float

    def to_dict(self) -> dict:
        def_cats = set()  # computed externally, stored in defensive_weight
        return {
            "variant_name": self.variant_name,
            "weights": {t: round(w, 4) for t, w in self.weights.items()},
            "excluded_tickers": self.excluded_tickers,
            "n_eligible": self.n_eligible,
            "turnover": round(self.turnover, 4) if self.turnover is not None else None,
            "proposed_turnover": round(self.proposed_turnover, 4) if self.proposed_turnover is not None else None,
            "risk_mode": "RISK_OFF" if self.risk_gate.risk_off else "RISK_ON",
            "risk_mode_check_status": self.risk_mode_check.status,
            "defensive_weight": round(self.defensive_weight, 4),
            "pre_trade_gate_status": self.pre_trade_gate.overall_status,
            "pre_trade_gate_failures": [
                c.check_id for c in self.pre_trade_gate.checks
                if c.status in ("FAIL", "REVIEW_REQUIRED") and c.severity != "INFO"
            ],
        }


@dataclass
class ComparisonResult:
    run_date: date
    variants: list[VariantResult]

    def to_dict(self) -> dict:
        return {
            "run_date": self.run_date.isoformat(),
            "variants": [v.to_dict() for v in self.variants],
        }


# ── single variant allocation ────────────────────────────────────────────────


def run_variant_allocation(
    scores: pd.Series,
    indicators: pd.DataFrame,
    prices: pd.DataFrame,
    cfg: AppConfig,
    ticker_to_category: dict[str, str],
    risk_gate: RiskGateResult,
    prev_weights: dict[str, float] | None,
    variant_name: str,
    apply_turnover: bool = True,
) -> VariantResult:
    """Run the full allocation pipeline for a single strategy variant.

    When ``apply_turnover=False`` (used in comparison mode), the turnover limit
    is skipped so each variant shows its pure model recommendation.  The
    ``proposed_turnover`` field still indicates how much churn would be needed
    from the current portfolio state.
    """
    filtered_scores, excluded = filter_ranking_scores(scores, cfg, variant_name)

    raw_weights = compute_allocation(
        scores=filtered_scores,
        indicators=indicators.loc[indicators.index.intersection(filtered_scores.index)],
        ticker_to_category=ticker_to_category,
        alloc_cfg=cfg.allocation,
        risk_cfg=cfg.risk,
    )
    weights = apply_risk_gate(raw_weights, ticker_to_category, risk_gate, cfg.risk.risk_off_equity_cap)

    proposed_turnover = None
    turnover = None
    if prev_weights:
        proposed_turnover = compute_turnover(weights, prev_weights)
        if apply_turnover:
            weights = apply_turnover_limit(weights, prev_weights, cfg.turnover.effective_limit)
            turnover = compute_turnover(weights, prev_weights)

    n_eligible = int((filtered_scores > 0).sum())
    max_assets = resolve_max_assets(n_eligible, cfg.global_settings.max_portfolio_assets)
    final_weights = trim_to_max_assets(weights, max_assets)

    risk_mode_check = check_risk_mode_consistency(
        weights=final_weights,
        ticker_to_category=ticker_to_category,
        risk_off=risk_gate.risk_off,
        cfg=cfg.risk_mode_checks,
    )

    pre_trade_gate = run_pre_trade_gate(
        weights=final_weights,
        ticker_to_category=ticker_to_category,
        cfg=cfg,
        turnover=turnover,
        risk_mode_check=risk_mode_check,
    )

    return VariantResult(
        variant_name=variant_name,
        weights=final_weights,
        excluded_tickers=excluded,
        n_eligible=n_eligible,
        turnover=turnover,
        proposed_turnover=proposed_turnover,
        risk_gate=risk_gate,
        risk_mode_check=risk_mode_check,
        pre_trade_gate=pre_trade_gate,
        defensive_weight=risk_mode_check.defensive_weight,
    )


# ── comparison ───────────────────────────────────────────────────────────────


def compare_variants(
    prices: pd.DataFrame,
    cfg: AppConfig,
    ticker_to_category: dict[str, str],
    prev_weights: dict[str, float] | None,
    run_date: date,
    variants: list[str] | None = None,
) -> ComparisonResult:
    """Run all variants and return comparison result."""
    variant_names = variants or COMPARISON_VARIANTS

    indicators = compute_indicators(prices, cfg.scoring)
    scores = compute_scores(indicators, cfg.scoring)
    risk_gate = evaluate_risk_gate(prices, cfg.risk)

    results: list[VariantResult] = []
    for name in variant_names:
        logger.info(f"Running variant: {name}")
        result = run_variant_allocation(
            scores=scores,
            indicators=indicators,
            prices=prices,
            cfg=cfg,
            ticker_to_category=ticker_to_category,
            risk_gate=risk_gate,
            prev_weights=prev_weights,
            variant_name=name,
            apply_turnover=False,
        )
        results.append(result)
        logger.info(
            f"  {name}: {list(result.weights.keys())} "
            f"defensive={result.defensive_weight:.1%} gate={result.pre_trade_gate.overall_status}"
        )

    return ComparisonResult(run_date=run_date, variants=results)


# ── report generation ────────────────────────────────────────────────────────


def build_comparison_markdown(result: ComparisonResult) -> str:
    lines: list[str] = []
    lines.append("# Strategy Variant 比較レポート")
    lines.append("")
    lines.append(f"**実行日:** {result.run_date.isoformat()}")
    lines.append("")
    lines.append("> ⚠️ このレポートは参考情報です。自動売買は行いません。最終配分の採用は人間の判断で行ってください。")
    lines.append("")

    # Summary table
    variant_names = [v.variant_name for v in result.variants]
    lines.append("## 総合比較")
    lines.append("")

    header = "| 項目 |" + "".join(f" {n} |" for n in variant_names)
    lines.append(header)
    sep = "|---|" + "---|" * len(variant_names)
    lines.append(sep)

    def row(label: str, values: list[str]) -> str:
        return f"| {label} |" + "".join(f" {v} |" for v in values)

    # Risk mode
    lines.append(row("リスクモード", [
        "⚠️ RISK_OFF" if v.risk_gate.risk_off else "✅ RISK_ON"
        for v in result.variants
    ]))

    # Defensive weight
    lines.append(row("防御資産比率", [f"{v.defensive_weight:.1%}" for v in result.variants]))

    # Risk mode check
    rmc_icons = {"PASS": "✅", "PASS_WITH_CAUTION": "⚠️", "REVIEW_REQUIRED": "🔶", "N/A": "—"}
    lines.append(row("Risk-ON整合性", [
        f"{rmc_icons.get(v.risk_mode_check.status, '')} {v.risk_mode_check.status}"
        for v in result.variants
    ]))

    # Pre-Trade Gate
    gate_icons = {"PASS": "✅", "FAIL": "❌", "PASS_WITH_CAUTION": "⚠️", "REVIEW_REQUIRED": "🔶"}
    lines.append(row("Pre-Trade Gate", [
        f"{gate_icons.get(v.pre_trade_gate.overall_status, '')} {v.pre_trade_gate.overall_status}"
        for v in result.variants
    ]))

    # Turnover
    lines.append(row("ターンオーバー", [
        f"{v.turnover:.1%}" if v.turnover is not None else "N/A（初回）"
        for v in result.variants
    ]))

    # SGOV adoption
    lines.append(row("SGOV採用", [
        "✅" if "SGOV" in v.weights else "❌"
        for v in result.variants
    ]))

    # UUP adoption
    lines.append(row("UUP採用", [
        "✅" if "UUP" in v.weights else "❌"
        for v in result.variants
    ]))

    # Excluded tickers
    lines.append(row("ランキング除外", [
        ", ".join(v.excluded_tickers) if v.excluded_tickers else "なし"
        for v in result.variants
    ]))

    lines.append("")

    # Allocation detail per variant
    for v in result.variants:
        lines.append(f"## 配分詳細: {v.variant_name}")
        lines.append("")
        lines.append("| ランク | Ticker | 配分 |")
        lines.append("|--------|--------|------|")
        for rank, (ticker, w) in enumerate(sorted(v.weights.items(), key=lambda x: -x[1]), 1):
            lines.append(f"| {rank} | {ticker} | {w:.1%} |")

        if v.excluded_tickers:
            lines.append("")
            lines.append(f"> ランキング除外 ETF: {', '.join(v.excluded_tickers)}")

        # Pre-Trade Gate failures
        failures = [c for c in v.pre_trade_gate.checks if c.status in ("FAIL", "REVIEW_REQUIRED") and c.severity != "INFO"]
        if failures:
            lines.append("")
            lines.append("**Pre-Trade Gate 指摘:**")
            for f in failures:
                lines.append(f"- [{f.status}] {f.message}")

        lines.append("")

    lines.append("## 注意事項")
    lines.append("")
    lines.append("- 本比較は参考情報であり、投資判断の根拠としないでください。")
    lines.append("- 自動売買機能はありません。")
    lines.append("- デフォルト戦略は `baseline_current` です。変更する場合は config.yaml の `strategy_variant.name` を更新してください。")
    lines.append("")

    return "\n".join(lines)


def save_comparison_report(
    result: ComparisonResult,
    output_dir: str,
    run_date: date,
) -> tuple[str, str]:
    """Save comparison markdown and JSON. Returns (md_path, json_path)."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    md_path = out / "strategy_variant_comparison.md"
    md_path.write_text(build_comparison_markdown(result), encoding="utf-8")

    json_path = out / "strategy_variant_comparison.json"
    json_path.write_text(
        json.dumps(result.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    logger.info(f"Comparison report saved: {md_path}")
    logger.info(f"Comparison JSON saved:   {json_path}")
    return str(md_path), str(json_path)
