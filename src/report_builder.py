from datetime import date
from pathlib import Path
from typing import Optional

import pandas as pd

from src.config_loader import AppConfig
from src.correlation import compute_correlation, highly_correlated_pairs
from src.risk_gate import RiskGateResult
from src.logger import logger


def build_report(
    cfg: AppConfig,
    weights: dict[str, float],
    scores: pd.Series,
    indicators: pd.DataFrame,
    prices: pd.DataFrame,
    risk_gate: RiskGateResult,
    prev_weights: dict[str, float] | None,
    turnover: float | None,
    run_date: date | None = None,
    audit_result=None,
    proposed_turnover: float | None = None,
    risk_mode_check=None,
    pre_trade_gate=None,
    strategy_variant: str | None = None,
) -> str:
    if run_date is None:
        run_date = date.today()

    asset_map = {a.ticker: a for a in cfg.universe.assets}
    lines: list[str] = []

    lines.append(f"# ETF Rotation Bot — Monthly Report")
    lines.append(f"**実行日:** {run_date.isoformat()}")
    variant_label = strategy_variant or cfg.strategy_variant.name
    lines.append(f"**戦略バリアント:** `{variant_label}`")
    lines.append("")

    if risk_gate.risk_off:
        lines.append(f"> ⚠️ **リスクオフモード**: {risk_gate.message}")
        lines.append("")
    else:
        lines.append(f"> ✅ **リスクオン**: {risk_gate.message}")
        lines.append("")

    # --- Recommended allocation ---
    lines.append("## 推奨配分")
    lines.append("")
    lines.append("| ランク | 資産名 | Ticker | カテゴリ | スコア | 配分 |")
    lines.append("|--------|--------|--------|----------|--------|------|")

    sorted_weights = sorted(weights.items(), key=lambda x: -x[1])
    for rank, (ticker, w) in enumerate(sorted_weights, 1):
        asset = asset_map.get(ticker)
        name = asset.display_name if asset else ticker
        cat = asset.category if asset else "—"
        sc = f"{scores.get(ticker, float('nan')):.3f}" if ticker in scores.index else "—"
        lines.append(f"| {rank} | {name} | {ticker} | {cat} | {sc} | {w:.1%} |")

    lines.append("")

    # --- Score details ---
    lines.append("## スコア詳細")
    lines.append("")
    cols_to_show = ["mom_1m", "mom_3m", "mom_6m", "mom_12m", "vol_20d"]
    available = [c for c in cols_to_show if c in indicators.columns]

    header_parts = ["| 資産名", "Ticker"]
    for c in available:
        header_parts.append(_col_label(c))
    header_parts.append("スコア |")
    lines.append(" | ".join(header_parts[:2]) + " | " + " | ".join(header_parts[2:]))
    lines.append("|" + "|".join(["---"] * (len(header_parts))) + "|")

    top_tickers = [t for t, _ in sorted(scores.items(), key=lambda x: -x[1])[: cfg.report.top_n_display]]
    for ticker in top_tickers:
        if ticker not in indicators.index:
            continue
        asset = asset_map.get(ticker)
        name = asset.display_name if asset else ticker
        row_vals = [f"| {name}", ticker]
        for c in available:
            val = indicators.loc[ticker, c]
            if pd.isna(val):
                row_vals.append("—")
            elif c.startswith("mom"):
                row_vals.append(f"{val:.1%}")
            elif c == "vol_20d":
                row_vals.append(f"{val:.1%}")
            else:
                row_vals.append(f"{val:.2f}")
        sc = scores.get(ticker, float("nan"))
        row_vals.append(f"{sc:.3f} |")
        lines.append(" | ".join(row_vals))

    lines.append("")

    # --- Turnover constraint summary (Phase 2.3) ---
    if prev_weights is not None and turnover is not None:
        to_cfg = cfg.turnover
        eff_limit = to_cfg.effective_limit
        mode_label = to_cfg.mode_label
        was_limited = proposed_turnover is not None and proposed_turnover > eff_limit + 1e-9

        if was_limited:
            to_status = "⚠️ PASS_WITH_CAUTION"
        else:
            to_status = "✅ PASS"

        lines.append("## ターンオーバー制約")
        lines.append("")

        if to_cfg.migration_mode:
            lines.append(f"- モード：**{mode_label}**（通常上限：{to_cfg.normal_limit:.1%}、移行上限：{to_cfg.migration_limit:.1%}）")
        else:
            lines.append(f"- モード：**{mode_label}**")
            lines.append(f"- 上限：**{eff_limit:.1%}**")

        if proposed_turnover is not None:
            lines.append(f"- 推定ターンオーバー（制限前）：**{proposed_turnover:.1%}**")
        lines.append(f"- 実績ターンオーバー（制限後）：**{turnover:.1%}**")
        lines.append(f"- 判定：{to_status}")

        if to_cfg.migration_mode:
            lines.append("")
            lines.append(
                "> 注意：移行運用モードは配分ルール変更直後の一時的な設定です。"
                f"通常運用では {to_cfg.normal_limit:.0%} 上限に戻すことを推奨します。"
            )
        lines.append("")

    # --- Turnover analysis ---
    if prev_weights is not None and turnover is not None:
        lines.append("## ターンオーバー分析")
        lines.append("")
        lines.append(f"- 前回比ターンオーバー(買い): **{turnover:.1%}**")

        all_tickers = set(weights) | set(prev_weights)
        changes = {
            t: weights.get(t, 0.0) - prev_weights.get(t, 0.0)
            for t in all_tickers
        }
        big_changes = sorted(changes.items(), key=lambda x: -abs(x[1]))[:8]
        lines.append("")
        lines.append("| Ticker | 変化 |")
        lines.append("|--------|------|")
        for t, ch in big_changes:
            if abs(ch) < 0.005:
                continue
            asset = asset_map.get(t)
            name = asset.display_name if asset else t
            lines.append(f"| {name} ({t}) | {ch:+.1%} |")
        lines.append("")

    # --- Risk summary ---
    lines.append("## リスクサマリー")
    lines.append("")
    if risk_gate.sp500_return is not None:
        lines.append(f"- S&P500 ({cfg.risk.risk_off_window}日リターン): **{risk_gate.sp500_return:.2%}**")

    from src.risk_gate import EQUITY_CATEGORIES
    cat_map = {a.ticker: a.category for a in cfg.universe.assets}
    eq_weight = sum(w for t, w in weights.items() if cat_map.get(t, "") in EQUITY_CATEGORIES)
    bond_weight = sum(w for t, w in weights.items() if cat_map.get(t, "") == "bond")
    cash_weight = sum(w for t, w in weights.items() if cat_map.get(t, "") == "cash_like")

    lines.append(f"- エクイティ合計: **{eq_weight:.1%}**")
    lines.append(f"- 債券合計: **{bond_weight:.1%}**")
    lines.append(f"- キャッシュ系合計: **{cash_weight:.1%}**")
    lines.append("")

    # --- Risk-ON defensive weight check (Phase 2.4) ---
    if risk_mode_check is not None and risk_mode_check.enabled:
        status_icon = {
            "PASS": "✅", "PASS_WITH_CAUTION": "⚠️",
            "REVIEW_REQUIRED": "🔶", "N/A": "—",
        }.get(risk_mode_check.status, risk_mode_check.status)

        lines.append("## Risk-ON 防御資産比率チェック")
        lines.append("")
        lines.append(f"- Riskモード：**{risk_mode_check.risk_mode}**")
        lines.append(f"- 防御資産比率：**{risk_mode_check.defensive_weight:.1%}**")
        lines.append(f"- 判定：{status_icon} {risk_mode_check.status}")
        lines.append("")

        if risk_mode_check.status != "PASS":
            lines.append(f"> {risk_mode_check.message}")
            if risk_mode_check.status == "REVIEW_REQUIRED":
                lines.append("> score / volatility 配分により低ボラ資産へ偏っている可能性があります。手動確認してください。")
            lines.append("")

    # --- Correlation matrix ---
    if cfg.report.include_correlation_matrix:
        selected = list(weights.keys())
        if len(selected) >= 2 and all(t in prices.columns for t in selected):
            corr = compute_correlation(prices[selected], window=252)
            lines.append("## 相関マトリックス（推奨配分資産）")
            lines.append("")
            lines.append("| | " + " | ".join(selected) + " |")
            lines.append("|" + "|".join(["---"] * (len(selected) + 1)) + "|")
            for t in selected:
                row = [f"| **{t}**"]
                for u in selected:
                    v = corr.loc[t, u] if t in corr.index and u in corr.columns else float("nan")
                    row.append(f"{v:.2f}" if not pd.isna(v) else "—")
                lines.append(" | ".join(row) + " |")
            lines.append("")

            pairs = highly_correlated_pairs(corr, threshold=0.85)
            if pairs:
                lines.append("**高相関ペア (>0.85):**")
                for a, b, c in pairs[:5]:
                    lines.append(f"- {a} / {b}: {c:.2f}")
                lines.append("")

    # --- Pre-Trade Gate section (Phase 2.6) ---
    if pre_trade_gate is not None and pre_trade_gate.enabled:
        status_icon = {
            "PASS": "✅", "FAIL": "❌", "PASS_WITH_CAUTION": "⚠️", "REVIEW_REQUIRED": "🔶",
        }.get(pre_trade_gate.overall_status, pre_trade_gate.overall_status)

        lines.append("## Pre-Trade Gate（定量制約チェック）")
        lines.append("")
        lines.append(f"- 総合判定：{status_icon} **{pre_trade_gate.overall_status}**")
        if pre_trade_gate.overall_status not in ("PASS", "N/A"):
            lines.append("")
            lines.append("> ⚠️ 手動確認必須。自動売買は行いません。")
        lines.append("")

        display_checks = [c for c in pre_trade_gate.checks if c.severity != "INFO"]
        if display_checks:
            lines.append("| Check | Status | Severity | 内容 | 値 | 上限 |")
            lines.append("|---|---|---|---|---:|---:|")
            for chk in display_checks:
                chk_icon = {
                    "PASS": "✅", "FAIL": "❌", "PASS_WITH_CAUTION": "⚠️",
                    "REVIEW_REQUIRED": "🔶", "N/A": "—",
                }.get(chk.status, chk.status)
                val_str = f"{chk.value:.1%}" if chk.value is not None else "—"
                limit_str = f"{chk.limit:.1%}" if chk.limit is not None else "—"
                lines.append(
                    f"| {chk.check_id} | {chk_icon} {chk.status} | {chk.severity}"
                    f" | {chk.message} | {val_str} | {limit_str} |"
                )
            lines.append("")

    # --- AI audit section ---
    if audit_result is not None:
        lines.append("## AI監査結果（参考）")
        lines.append("")
        status_label = {
            "PASS": "✅ PASS",
            "PASS_WITH_CAUTION": "⚠️ PASS_WITH_CAUTION",
            "REVIEW_REQUIRED": "🔶 REVIEW_REQUIRED",
            "REJECT": "❌ REJECT",
        }.get(audit_result.status, str(audit_result.status))
        lines.append(f"**ステータス:** {status_label}")
        lines.append("")
        lines.append(f"**サマリー:** {audit_result.summary}")
        lines.append("")
        lines.append("> ⚠️ AI監査は参考情報です。最終配分は定量モデルの推奨値を使用しています。自動売買は行いません。")
        lines.append("")

        if audit_result.adjustments:
            lines.append("### AI参考調整案")
            lines.append("")
            if audit_result.adjustments_invalidated:
                lines.append(
                    "> ⚠️ ±5%制約を超えるAI参考調整案は無効化しました。実配分には反映しません。"
                )
                lines.append("")
            lines.append("| ETF | 現在配分 | AI参考配分 | 差分 | 判定 | 理由 |")
            lines.append("|-----|--------:|----------:|-----:|------|------|")
            for adj in audit_result.adjustments:
                delta = abs(adj.suggested_weight - adj.current_weight)
                validity = "有効" if adj.valid else "❌ 無効"
                reason_col = adj.reason if adj.valid else f"{adj.reason}（±5%制約超過: {delta:.1%}）"
                lines.append(
                    f"| {adj.ticker} | {adj.current_weight:.1%} | {adj.suggested_weight:.1%}"
                    f" | {delta:.1%} | {validity} | {reason_col} |"
                )
            lines.append("")
            lines.append("> AI参考調整案は実配分に反映しません。")
            lines.append("")

        if audit_result.pre_trade_checks:
            lines.append("### AI補足チェック")
            lines.append("")
            lines.append("| チェックID | 結果 | 詳細 | 値 |")
            lines.append("|-----------|------|------|-----|")
            for chk in audit_result.pre_trade_checks:
                result_icon = {"PASS": "✅", "WARN": "⚠️", "FAIL": "❌"}.get(chk.result, chk.result)
                val_str = f"{chk.value:.4f}" if chk.value is not None else "—"
                lines.append(f"| {chk.check_id} | {result_icon} {chk.result} | {chk.description} | {val_str} |")
            lines.append("")

        if audit_result.nisa_checks:
            lines.append("### NISA適格性（参考）")
            lines.append("")
            lines.append("| Ticker | NISA適格 | 理由 |")
            lines.append("|--------|---------|------|")
            for nc in audit_result.nisa_checks:
                flag = "✅" if nc.is_nisa_suitable else "❌"
                lines.append(f"| {nc.ticker} | {flag} | {nc.reason} |")
            lines.append("")

    # --- Warnings ---
    lines.append("## 注意事項")
    lines.append("")
    lines.append("- 本レポートは投資判断の参考情報であり、投資勧誘ではありません。")
    lines.append("- 日本円建てETF（1306.T等）は円建てリターン、米国ETFはドル建てリターンを使用しています。通貨リスクは考慮していません。")
    lines.append("- 自動売買機能はありません。実際の売買は手動で行ってください。")
    lines.append("")

    return "\n".join(lines)


def _col_label(col: str) -> str:
    labels = {
        "mom_1m": "1Mリターン",
        "mom_3m": "3Mリターン",
        "mom_6m": "6Mリターン",
        "mom_12m": "12Mリターン",
        "vol_20d": "ボラティリティ",
    }
    return labels.get(col, col)


def save_report(content: str, output_dir: str, run_date: date | None = None) -> Path:
    if run_date is None:
        run_date = date.today()

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"report_{run_date.isoformat()}.md"
    path.write_text(content, encoding="utf-8")
    logger.info(f"Report saved to {path}")
    return path
