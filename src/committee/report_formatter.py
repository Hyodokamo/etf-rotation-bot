"""Phase 3.1: Investment Committee OS — Markdown / Slack formatting (display-only)."""
from __future__ import annotations

from src.committee.models import CommitteeResult, CommitteeTier, CommitteeVerdict

_VERDICT_ICON = {
    CommitteeVerdict.PASS: "✅",
    CommitteeVerdict.PASS_WITH_CAUTION: "⚠️",
    CommitteeVerdict.WATCH: "👀",
    CommitteeVerdict.REJECT: "❌",
    CommitteeVerdict.INSUFFICIENT_DATA: "❓",
}


def _icon(verdict: CommitteeVerdict) -> str:
    return _VERDICT_ICON.get(verdict, "")


def build_committee_markdown(result: CommitteeResult) -> str:
    """Full report section for the Markdown report."""
    lines: list[str] = []
    lines.append("## Investment Committee（shadow mode・参考）")
    lines.append("")
    lines.append(
        "> 🛡️ shadow mode：本会議体の意見は**最終配分に一切影響しません**。"
        "自動売買は行いません。"
    )
    lines.append("")
    lines.append(
        f"- **最終判定:** {_icon(result.final_committee_verdict)} "
        f"`{result.final_committee_verdict.value}`"
    )
    lines.append(
        f"- Core Committee: {_icon(result.core_committee_verdict)} "
        f"`{result.core_committee_verdict.value}`"
    )
    lines.append(
        f"- Satellite Committee: {_icon(result.satellite_committee_verdict)} "
        f"`{result.satellite_committee_verdict.value}`"
    )
    lines.append(f"- 推奨アクション（助言）: {result.recommended_action}")
    lines.append(f"- allocation_override: `{str(result.allocation_override).lower()}`（shadow mode 固定）")
    if not result.satellite_activated:
        lines.append(f"- Satellite Committee: 未起動（{result.satellite_activation_reason}）")
    lines.append("")

    for tier, label in ((CommitteeTier.CORE, "Core Committee"), (CommitteeTier.SATELLITE, "Satellite Committee")):
        tier_members = [m for m in result.members if m.tier == tier]
        if not tier_members:
            if tier == CommitteeTier.SATELLITE and not result.satellite_activated:
                lines.append(f"### {label}")
                lines.append("")
                lines.append(f"> 未起動（{result.satellite_activation_reason}）。")
                lines.append("")
            continue
        lines.append(f"### {label}")
        lines.append("")
        lines.append("| メンバー | 判定 | 確信度 | 最も支持する理由 | 最も反対する理由 | 最も強く反対する点 | 主なリスク |")
        lines.append("|---|---|---:|---|---|---|---|")
        for m in tier_members:
            risks = "、".join(m.key_risks) if m.key_risks else "—"
            lines.append(
                f"| {m.display_name or m.member_id} | {_icon(m.verdict)} {m.verdict.value} "
                f"| {m.confidence:.0%} | {m.strongest_support or '—'} "
                f"| {m.strongest_objection or '—'} | {m.dissenting_view or '—'} | {risks} |"
            )
        lines.append("")

    if result.next_review_triggers:
        lines.append("### 次回レビュー・トリガー")
        lines.append("")
        for t in result.next_review_triggers:
            lines.append(f"- {t}")
        lines.append("")

    lines.append("> Committee の意見は参考情報です。最終配分は定量モデルの推奨値を使用しています。")
    lines.append("")
    return "\n".join(lines)


def build_committee_slack_summary(result: CommitteeResult) -> str:
    """Concise Slack summary (display-only)."""
    lines = [
        "*Investment Committee（shadow・参考）*",
        f"最終: {_icon(result.final_committee_verdict)} {result.final_committee_verdict.value}"
        f"（Core: {result.core_committee_verdict.value} / Satellite: {result.satellite_committee_verdict.value}）",
        f"助言: {result.recommended_action}",
        "🛡️ shadow mode：最終配分への影響なし／自動売買は行いません",
    ]
    return "\n".join(lines)
