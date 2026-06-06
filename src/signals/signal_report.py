"""Phase 5.1: signal report builder.

Builds daily_signal_report.md and optional Slack message.
No forbidden wording: 買え / 売れ / 購入実行 / 売却実行.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

from src.logger import logger
from src.signals.signal_models import FinalSignal, SignalResult

DEFAULT_REPORT_DIR = "reports"

_SIGNAL_ICON: dict[str, str] = {
    "HIGH_PRIORITY_CANDIDATE": "[HIGH]",
    "BUY_CANDIDATE": "[BUY_CANDIDATE]",
    "WATCH": "[WATCH]",
    "HOLD_OFF": "[HOLD_OFF]",
    "REJECT_FOR_NOW": "[REJECT]",
    "NO_ACTION": "[-]",
}

FORBIDDEN_WORDS = ["買え", "売れ", "購入実行", "売却実行"]


def build_signal_markdown(
    signals: list[SignalResult],
    market_regime: str = "neutral",
    as_of_date: str | None = None,
) -> str:
    today = as_of_date or date.today().isoformat()
    lines: list[str] = []
    lines.append(f"# Daily Signal Report — {today}")
    lines.append("")
    lines.append(
        "> 助言専用：実注文・注文数量計算・自動売買・証券口座連携は行いません。"
        "最終判断は人間が行います。"
    )
    lines.append(f"> 市場レジーム: **{market_regime}**")
    lines.append("")

    if not signals:
        lines.append("（本日のシグナルはありません）")
        lines.append("")
        _append_safety(lines)
        return "\n".join(lines)

    for r in signals:
        icon = _SIGNAL_ICON.get(r.final_signal.value, "[-]")
        lines.append(f"## {icon} {r.symbol} — {r.final_signal.value}")
        lines.append("")
        lines.append(f"- **signal_side:** {r.signal_side.value}")
        lines.append(f"- **final_signal:** {r.final_signal.value}")
        lines.append(f"- **confidence:** {r.confidence:.0%}")
        lines.append(
            f"- **total_score:** {r.total_score:+d} / "
            f"positive_members: {r.positive_member_count} / "
            f"veto: {r.veto_count}"
        )

        if r.trigger_labels:
            lines.append(f"- **トリガー:** {', '.join(r.trigger_labels)}")
        if r.risk_flags:
            lines.append(f"- **リスクフラグ:** {', '.join(r.risk_flags[:5])}")
        if r.veto_flags:
            lines.append(f"- **Veto:** {', '.join(r.veto_flags)}")

        lines.append(f"- **推奨テキスト:** {r.recommended_action_text}")
        lines.append(f"- **no_order_quantity:** {r.no_order_quantity}")
        lines.append(f"- **no_auto_trade:** {r.no_auto_trade}")
        lines.append(f"- **sell_signal_reserved:** {r.sell_signal_reserved}")

        if r.positive_reasons:
            lines.append("")
            lines.append("**買い候補となる根拠（Committee）:**")
            for reason in r.positive_reasons[:3]:
                lines.append(f"- {reason}")

        if r.dissenting_views:
            lines.append("")
            lines.append("**反対意見:**")
            for dv in r.dissenting_views[:3]:
                lines.append(f"- {dv}")

        if r.member_outputs:
            lines.append("")
            lines.append("| メンバー | stance | score | confidence |")
            lines.append("|---|---|---|---|")
            for m in r.member_outputs:
                lines.append(
                    f"| {m.member_id} | {m.stance.value} | {m.score:+d} | {m.confidence:.0%} |"
                )

        lines.append("")
        lines.append("---")
        lines.append("")

    _append_safety(lines)
    return "\n".join(lines)


def _append_safety(lines: list[str]) -> None:
    lines.append("## 安全注記")
    lines.append("")
    lines.append("- 本レポートはWatchlist候補条件を満たしたことを通知するものであり、売買指示ではありません。")
    lines.append("- 実注文・注文数量計算・自動売買・証券口座連携は行いません。")
    lines.append("- 最終判断は人間が行います。")
    lines.append("- core_manual（コア資産）・satellite_legacy・既存BOTZ/GRID は売却提案の対象外です。")
    lines.append("- SELL シグナルは今回のMVPでは実装されていません（reserved）。")
    lines.append("- AI検証枠のみが対象であり、全資産の売却・是正提案ではありません。")
    lines.append("")


def build_signal_slack_message(
    signals: list[SignalResult],
    market_regime: str = "neutral",
) -> str:
    """Build concise Slack message (optional in MVP)."""
    if not signals:
        return f"*Daily Signal* | 市場レジーム: {market_regime}\n本日のシグナルなし。"
    lines = [f"*Daily Signal* | 市場レジーム: {market_regime}"]
    for r in signals:
        icon = _SIGNAL_ICON.get(r.final_signal.value, "[-]")
        lines.append(
            f"{icon} *{r.symbol}* → {r.final_signal.value}"
            f" (score={r.total_score:+d}, veto={r.veto_count})"
        )
    lines.append("_注文数量計算・自動売買は行いません。最終判断は人間が行います。_")
    return "\n".join(lines)


def save_signal_report(
    markdown: str,
    out_dir: str | Path = DEFAULT_REPORT_DIR,
    output_path: str | Path | None = None,
) -> str:
    if output_path:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
    else:
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        path = out / "daily_signal_report.md"
    path.write_text(markdown, encoding="utf-8")
    logger.info(f"Signal report saved: {path}")
    return str(path)
