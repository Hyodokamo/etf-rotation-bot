"""Phase 3.4: Committee Advisory Mode.

Turns the structured Committee judgment (CommitteeResult + Review Comparison)
into concrete, practical *advice* for the month — never a trade instruction.

Shadow-mode invariant preserved: `allocation_override` is always False, the final
allocation is never modified, and no order quantities are produced. Advice never
says "売る"/"買う" assertively — it says "追加購入を控える", "維持を推奨",
"再レビュー", "候補レビューへ回す".

The generation is deterministic Python. (An optional LLM may later polish the
natural-language wording, but must never generate the buy/sell decision.)
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field, field_validator

from src.committee.models import CommitteeResult, CommitteeVerdict, _SEVERITY

ADVISORY_MODE = "shadow_advisory"

# Member verdicts that count as a "concern" for advisory rules (excludes
# INSUFFICIENT_DATA, which means "no judgment" rather than a negative view).
_CONCERN = {CommitteeVerdict.WATCH, CommitteeVerdict.REJECT}

_PRIORITY_RANK = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
_MAX_ACTION_ITEMS = 5

_DO_NOT_ACTIONS = [
    "Committee助言を理由に final_allocation を変更しない",
    "売買数量を自動計算しない",
    "「売る」「買う」を自動執行しない",
]


class OverallStance(str, Enum):
    ACCEPT = "ACCEPT"
    HOLD_WITH_CAUTION = "HOLD_WITH_CAUTION"
    WAIT_FOR_REVIEW = "WAIT_FOR_REVIEW"
    REDUCE_RISK_REVIEW = "REDUCE_RISK_REVIEW"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class AdvisoryCategory(str, Enum):
    BUY_DISCIPLINE = "BUY_DISCIPLINE"
    HOLD_DISCIPLINE = "HOLD_DISCIPLINE"
    RISK_CONTROL = "RISK_CONTROL"
    REVIEW_TRIGGER = "REVIEW_TRIGGER"
    DATA_QUALITY = "DATA_QUALITY"
    CANDIDATE_REVIEW = "CANDIDATE_REVIEW"
    HUMAN_DECISION_REQUIRED = "HUMAN_DECISION_REQUIRED"


class AdvisoryPriority(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class ActionItem(BaseModel):
    category: AdvisoryCategory
    priority: AdvisoryPriority
    message: str
    reason: str = ""
    review_trigger: str = ""


class CommitteeAdvisory(BaseModel):
    advisory_mode: str = ADVISORY_MODE
    overall_stance: OverallStance
    action_items: list[ActionItem] = Field(default_factory=list)
    do_not_actions: list[str] = Field(default_factory=lambda: list(_DO_NOT_ACTIONS))
    next_review_focus: list[str] = Field(default_factory=list)
    generated_from: dict = Field(default_factory=dict)
    allocation_override: bool = False

    @field_validator("allocation_override")
    @classmethod
    def _force_no_override(cls, v: bool) -> bool:
        # Advisory mode is still shadow mode: never override allocation.
        return False

    def to_dict(self) -> dict:
        return self.model_dump(mode="json")


# ── helpers ──────────────────────────────────────────────────────────────────


def _member_verdicts(committee_result: CommitteeResult) -> dict[str, CommitteeVerdict]:
    return {m.member_id: m.verdict for m in committee_result.members}


def _is_concern(v: CommitteeVerdict | None) -> bool:
    return v in _CONCERN


def _pct(weights: dict | None, ticker: str) -> str:
    if weights and ticker in weights and weights[ticker]:
        return f"{weights[ticker]:.0%}"
    return ""


def _high_if_caution(severity: str) -> AdvisoryPriority:
    return AdvisoryPriority.HIGH if severity in ("CAUTION", "MATERIAL") else AdvisoryPriority.MEDIUM


# ── core generation ──────────────────────────────────────────────────────────


def _determine_stance(
    final_verdict: CommitteeVerdict,
    data_quality_issue: bool,
) -> OverallStance:
    if final_verdict == CommitteeVerdict.INSUFFICIENT_DATA:
        return OverallStance.INSUFFICIENT_DATA
    if final_verdict == CommitteeVerdict.REJECT:
        return OverallStance.REDUCE_RISK_REVIEW
    if final_verdict == CommitteeVerdict.WATCH:
        return OverallStance.WAIT_FOR_REVIEW
    if final_verdict == CommitteeVerdict.PASS_WITH_CAUTION:
        stance = OverallStance.HOLD_WITH_CAUTION
    else:  # PASS
        stance = OverallStance.ACCEPT
    # Data-quality problems escalate an otherwise-calm stance to a review.
    if data_quality_issue and stance in (OverallStance.ACCEPT, OverallStance.HOLD_WITH_CAUTION):
        return OverallStance.WAIT_FOR_REVIEW
    return stance


def build_advisory(
    committee_result: CommitteeResult,
    comparison=None,
    final_allocation: dict[str, float] | None = None,
    risk_mode: str | None = None,
    ai_audit_status: str | None = None,
) -> CommitteeAdvisory:
    """Deterministically build the monthly Committee advisory (display-only)."""
    verdicts = _member_verdicts(committee_result)
    final_verdict = committee_result.final_committee_verdict
    severity = getattr(comparison, "severity", "NONE") if comparison is not None else "NONE"

    auditor_concern = _is_concern(verdicts.get("core_ai_auditor"))
    ai_reject = (ai_audit_status == "REJECT")
    data_quality_issue = auditor_concern or ai_reject

    stance = _determine_stance(final_verdict, data_quality_issue)
    items: list[ActionItem] = []

    # 1) Data quality / human decision take precedence when relevant.
    if data_quality_issue:
        items.append(ActionItem(
            category=AdvisoryCategory.DATA_QUALITY,
            priority=AdvisoryPriority.HIGH,
            message="配分判断の前に、データ品質・計算ロジック・過剰最適化の有無を再確認する",
            reason=(
                f"AI監査ステータス={ai_audit_status} / core_ai_auditor="
                f"{verdicts.get('core_ai_auditor').value if verdicts.get('core_ai_auditor') else 'N/A'}。"
                "投資助言より先にプロセスの信頼性を担保する。"
            ),
            review_trigger="AI監査がPASS圏に戻る、または core_ai_auditor の懸念が解消するか",
        ))
    if ai_reject:
        items.append(ActionItem(
            category=AdvisoryCategory.HUMAN_DECISION_REQUIRED,
            priority=AdvisoryPriority.HIGH,
            message="今月は人間による再レビューを必須とし、自動的な配分維持・変更を行わない",
            reason="AI監査が REJECT。データ・ロジックの信頼性が確認できるまで投資助言を保留する。",
            review_trigger="再実行でAI監査が PASS / PASS_WITH_CAUTION になるか",
        ))

    # 2) Member-driven advice (each only on WATCH/REJECT).
    if _is_concern(verdicts.get("rob_arnott")):
        qqqm = _pct(final_allocation, "QQQM")
        items.append(ActionItem(
            category=AdvisoryCategory.BUY_DISCIPLINE,
            priority=_high_if_caution(severity),
            message=f"成長株・高バリュエーション資産（QQQM{(' ' + qqqm) if qqqm else ''}）の追加購入は今月控える",
            reason="Rob Arnott型が割高化・平均回帰リスクにWATCH。上昇後の追随買いは将来リターンを圧縮しうる。",
            review_trigger="QQQMの3か月モメンタムが0割れ、またはS&P500 60日リターンの失速",
        ))
    if _is_concern(verdicts.get("howard_marks")):
        bnd = _pct(final_allocation, "BND")
        items.append(ActionItem(
            category=AdvisoryCategory.RISK_CONTROL,
            priority=_high_if_caution(severity),
            message=f"防御資産比率を維持する（BND{(' ' + bnd) if bnd else ''}、目安30%以上）",
            reason=(
                "Howard Marks型がリスクオン継続への過信を警告。"
                + (f"現在は{risk_mode}。" if risk_mode else "")
            ),
            review_trigger="S&P500が直近高値から5%超下落、またはVOOが200日線を割る",
        ))
    if _is_concern(verdicts.get("paul_tudor_jones")):
        items.append(ActionItem(
            category=AdvisoryCategory.REVIEW_TRIGGER,
            priority=AdvisoryPriority.MEDIUM,
            message="トレンド崩れ時の撤退・再レビュー条件をあらかじめ明示しておく",
            reason="Paul Tudor Jones型が下方リスク管理・損切り規律にWATCH。",
            review_trigger="QQQMまたはVOOが200日移動平均線を割る",
        ))
    if _is_concern(verdicts.get("druckenmiller")):
        items.append(ActionItem(
            category=AdvisoryCategory.CANDIDATE_REVIEW,
            priority=AdvisoryPriority.MEDIUM,
            message="新規テーマ・候補ETFの追加は別の候補レビューへ回す（今月の配分には足さない）",
            reason="Druckenmiller型が大局テーマの確信度低下を指摘。確信度の高い一本筋でないため。",
            review_trigger="主要マクロテーマの再確認、流動性環境の変化",
        ))
    if _is_concern(verdicts.get("aqr_meb")):
        items.append(ActionItem(
            category=AdvisoryCategory.HOLD_DISCIPLINE,
            priority=AdvisoryPriority.MEDIUM,
            message="ルールに沿って現行配分を維持し、裁量での上乗せをしない",
            reason="AQR/Meb型が定量シグナルの整合性低下にWATCH。",
            review_trigger="複数のトレンド指標が再び整合するか",
        ))

    # 3) Reflect comparison: new dissenting views and new review triggers.
    if comparison is not None:
        new_dissent = getattr(comparison, "new_dissenting_views", {}) or {}
        if new_dissent:
            mid, txt = next(iter(new_dissent.items()))
            items.append(ActionItem(
                category=AdvisoryCategory.RISK_CONTROL,
                priority=AdvisoryPriority.MEDIUM,
                message="新たに出た反対意見に対応した監視を行う",
                reason=f"前回比で新規の反対意見（{mid}）: {txt[:80]}",
                review_trigger="当該懸念が次回も継続するか",
            ))
        new_triggers = getattr(comparison, "new_next_review_triggers", []) or []
        if new_triggers:
            items.append(ActionItem(
                category=AdvisoryCategory.REVIEW_TRIGGER,
                priority=AdvisoryPriority.MEDIUM,
                message="新たに追加されたレビュー条件を重点監視する",
                reason=f"前回比で新規トリガー {len(new_triggers)}件。",
                review_trigger="; ".join(new_triggers[:3]),
            ))

    # 4) Calm fallback (specific, not a generic platitude).
    if not items and stance == OverallStance.ACCEPT:
        items.append(ActionItem(
            category=AdvisoryCategory.HOLD_DISCIPLINE,
            priority=AdvisoryPriority.LOW,
            message="現行の定量配分を維持する。新規の上乗せ・テーマ追加は行わない",
            reason="全メンバーがPASS圏で、前回比でも重要な変化は検出されていない。",
            review_trigger="次回レビューで判定・配分・トリガーに変化が出るか",
        ))

    # 5) Severity guarantee: CAUTION+ must surface at least one HIGH item.
    if severity in ("CAUTION", "MATERIAL") and not any(i.priority == AdvisoryPriority.HIGH for i in items):
        if items:
            items[0].priority = AdvisoryPriority.HIGH
        else:
            items.append(ActionItem(
                category=AdvisoryCategory.REVIEW_TRIGGER,
                priority=AdvisoryPriority.HIGH,
                message="前回比で重要な変化を検出。今月は配分を据え置き、変化要因を重点確認する",
                reason=f"Committee Review Comparison severity={severity}。",
                review_trigger=getattr(comparison, "summary", "") if comparison else "",
            ))

    # Sort HIGH-first (stable) and cap to 5.
    items.sort(key=lambda i: _PRIORITY_RANK[i.priority.value])
    items = items[:_MAX_ACTION_ITEMS]

    # next_review_focus: concrete, from committee triggers + new comparison triggers.
    focus: list[str] = []
    for t in list(committee_result.next_review_triggers) + (
        getattr(comparison, "new_next_review_triggers", []) or [] if comparison is not None else []
    ):
        if t and t not in focus:
            focus.append(t)
    focus = focus[:5]

    generated_from = {
        "final_committee_verdict": final_verdict.value,
        "core_committee_verdict": committee_result.core_committee_verdict.value,
        "satellite_committee_verdict": committee_result.satellite_committee_verdict.value,
        "ai_audit_status": ai_audit_status,
        "risk_mode": risk_mode,
        "comparison_severity": severity if comparison is not None else None,
        "satellite_activated": committee_result.satellite_activated,
    }

    return CommitteeAdvisory(
        overall_stance=stance,
        action_items=items,
        next_review_focus=focus,
        generated_from=generated_from,
        allocation_override=False,
    )


# ── formatters (display-only) ────────────────────────────────────────────────

_STANCE_ICON = {
    OverallStance.ACCEPT: "✅",
    OverallStance.HOLD_WITH_CAUTION: "⚠️",
    OverallStance.WAIT_FOR_REVIEW: "👀",
    OverallStance.REDUCE_RISK_REVIEW: "🔴",
    OverallStance.INSUFFICIENT_DATA: "❓",
}
_PRIORITY_ICON = {"HIGH": "🔴", "MEDIUM": "🟡", "LOW": "⚪"}


def build_advisory_markdown(advisory: CommitteeAdvisory) -> str:
    icon = _STANCE_ICON.get(advisory.overall_stance, "")
    lines: list[str] = []
    lines.append("## Committee Advisory（助言・参考）")
    lines.append("")
    lines.append(f"- 総合スタンス: {icon} **{advisory.overall_stance.value}**")
    lines.append(f"- mode: `{advisory.advisory_mode}` / allocation_override: `{str(advisory.allocation_override).lower()}`")
    lines.append("")
    lines.append("> 🛡️ shadow mode：助言のみ。最終配分は変更しません／売買数量は出しません／自動売買は行いません。")
    lines.append("")

    if advisory.action_items:
        lines.append("### Action Items（優先度順・最大5件）")
        lines.append("")
        for it in advisory.action_items:
            p_icon = _PRIORITY_ICON.get(it.priority.value, "")
            lines.append(f"- {p_icon} **{it.priority.value}** [{it.category.value}] {it.message}")
            if it.reason:
                lines.append(f"  - 理由: {it.reason}")
            if it.review_trigger:
                lines.append(f"  - 見直し条件: {it.review_trigger}")
        lines.append("")

    if advisory.next_review_focus:
        lines.append("### 次回レビューの focus")
        lines.append("")
        for f in advisory.next_review_focus:
            lines.append(f"- {f}")
        lines.append("")

    lines.append("### Do NOT")
    lines.append("")
    for d in advisory.do_not_actions:
        lines.append(f"- {d}")
    lines.append("")
    return "\n".join(lines)


def build_advisory_slack_summary(advisory: CommitteeAdvisory) -> str:
    """Concise Slack advisory: stance + top action items (HIGH first, max 3)."""
    icon = _STANCE_ICON.get(advisory.overall_stance, "")
    lines = [f"*Committee Advisory（助言）* {icon} {advisory.overall_stance.value}"]
    for it in advisory.action_items[:3]:
        p_icon = _PRIORITY_ICON.get(it.priority.value, "")
        lines.append(f"{p_icon} {it.priority.value}: {it.message}")
    lines.append("🛡️ shadow: 助言のみ・配分変更なし・数量計算なし")
    return "\n".join(lines)
