"""Phase 3.3: Committee Review Comparison.

Deterministic (no-LLM) diff between the latest two Committee decision-log
entries: how verdicts, allocation, dissent, and review triggers changed since
last month. Display-only — never touches allocation (shadow-mode invariant).

Keep the comparison logic simple and deterministic: extract the diff, classify
severity by fixed rules. No post-hoc narrative from an LLM.
"""
from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from src.committee.decision_logger import (
    DEFAULT_COMMITTEE_LOG_PATH,
    read_committee_decision_log,
)
from src.committee.models import CommitteeVerdict, _SEVERITY
from src.logger import logger

_ALLOC_EPS = 1e-9
_MATERIAL_SINGLE_ASSET_MOVE = 0.10  # ±10 percentage points
# Members whose escalation to WATCH+ warrants CAUTION.
_KEY_CAUTION_MEMBERS = {"howard_marks", "rob_arnott", "core_ai_auditor"}
_HUMAN_MATERIAL = {"TRIM", "EXIT", "WAIT"}

_SEVERITY_RANK = {"NONE": 0, "INFO": 1, "CAUTION": 2, "MATERIAL": 3}


def _verdict_sev(v: str | None) -> int:
    if not v:
        return -1
    try:
        return _SEVERITY[CommitteeVerdict(v)]
    except (ValueError, KeyError):
        return -1


def _direction(prev: str | None, curr: str | None) -> str:
    if prev is None and curr is not None:
        return "NEW"
    if curr is None and prev is not None:
        return "REMOVED"
    if prev == curr:
        return "UNCHANGED"
    ps, cs = _verdict_sev(prev), _verdict_sev(curr)
    if cs > ps:
        return "WORSENED"
    if cs < ps:
        return "IMPROVED"
    return "CHANGED"


# ── models ──────────────────────────────────────────────────────────────────


class VerdictChange(BaseModel):
    previous: str | None = None
    current: str | None = None
    changed: bool = False
    direction: str = "UNCHANGED"


class MemberVerdictChange(BaseModel):
    member_id: str
    previous: str | None = None
    current: str | None = None
    changed: bool = False
    direction: str = "UNCHANGED"


class AllocationChange(BaseModel):
    ticker: str
    previous_weight: float = 0.0
    current_weight: float = 0.0
    diff: float = 0.0
    diff_pct_point: float = 0.0
    direction: str = "UNCHANGED"  # INCREASED / DECREASED / ADDED / REMOVED / UNCHANGED


class ReviewComparison(BaseModel):
    previous_run_id: str | None = None
    current_run_id: str | None = None
    previous_date: str | None = None
    current_date: str | None = None
    core_committee_verdict_change: VerdictChange
    satellite_committee_verdict_change: VerdictChange
    final_committee_verdict_change: VerdictChange
    recommended_action_change: dict
    member_verdict_changes: list[MemberVerdictChange] = Field(default_factory=list)
    allocation_changes: list[AllocationChange] = Field(default_factory=list)
    new_dissenting_views: dict[str, str] = Field(default_factory=dict)
    resolved_dissenting_views: dict[str, str] = Field(default_factory=dict)
    new_next_review_triggers: list[str] = Field(default_factory=list)
    resolved_next_review_triggers: list[str] = Field(default_factory=list)
    summary: str = ""
    severity: str = "NONE"

    def to_dict(self) -> dict:
        return self.model_dump(mode="json")


# ── diff helpers ────────────────────────────────────────────────────────────


def _verdict_change(prev: str | None, curr: str | None) -> VerdictChange:
    return VerdictChange(
        previous=prev, current=curr, changed=(prev != curr), direction=_direction(prev, curr)
    )


def _member_verdict_map(entry: dict) -> dict[str, str]:
    return {
        m.get("member_id"): m.get("verdict")
        for m in entry.get("member_outputs", [])
        if m.get("member_id")
    }


def _member_changes(prev: dict, curr: dict) -> list[MemberVerdictChange]:
    pm, cm = _member_verdict_map(prev), _member_verdict_map(curr)
    out: list[MemberVerdictChange] = []
    for mid in sorted(set(pm) | set(cm)):
        p, c = pm.get(mid), cm.get(mid)
        out.append(MemberVerdictChange(
            member_id=mid, previous=p, current=c, changed=(p != c), direction=_direction(p, c)
        ))
    return out


def _allocation_changes(prev: dict, curr: dict) -> list[AllocationChange]:
    pa = prev.get("final_allocation") or {}
    ca = curr.get("final_allocation") or {}
    out: list[AllocationChange] = []
    for ticker in sorted(set(pa) | set(ca)):
        p = float(pa.get(ticker, 0.0))
        c = float(ca.get(ticker, 0.0))
        diff = round(c - p, 6)
        if ticker not in pa and c > _ALLOC_EPS:
            direction = "ADDED"
        elif ticker not in ca and p > _ALLOC_EPS:
            direction = "REMOVED"
        elif diff > _ALLOC_EPS:
            direction = "INCREASED"
        elif diff < -_ALLOC_EPS:
            direction = "DECREASED"
        else:
            direction = "UNCHANGED"
        out.append(AllocationChange(
            ticker=ticker, previous_weight=round(p, 4), current_weight=round(c, 4),
            diff=diff, diff_pct_point=round(diff * 100, 2), direction=direction,
        ))
    return out


def _dissent_diff(prev: dict, curr: dict) -> tuple[dict, dict]:
    pd = {k: v for k, v in (prev.get("dissenting_views") or {}).items() if v}
    cd = {k: v for k, v in (curr.get("dissenting_views") or {}).items() if v}
    # new: present now and (absent before OR text changed)
    new = {k: v for k, v in cd.items() if pd.get(k) != v}
    # resolved: present before and (absent now OR text changed)
    resolved = {k: v for k, v in pd.items() if cd.get(k) != v}
    return new, resolved


def _trigger_diff(prev: dict, curr: dict) -> tuple[list[str], list[str]]:
    pt = prev.get("next_review_triggers") or []
    ct = curr.get("next_review_triggers") or []
    pset, cset = set(pt), set(ct)
    new = [t for t in ct if t not in pset]
    resolved = [t for t in pt if t not in cset]
    return new, resolved


# ── severity ─────────────────────────────────────────────────────────────────


def _compute_severity(
    curr: dict,
    final_change: VerdictChange,
    member_changes: list[MemberVerdictChange],
    allocation_changes: list[AllocationChange],
    new_dissent: dict,
    new_triggers: list[str],
    recommended_changed: bool,
) -> str:
    levels = {"NONE"}

    # ── MATERIAL ──
    if final_change.direction == "WORSENED" and final_change.current in ("WATCH", "REJECT"):
        levels.add("MATERIAL")
    if any(abs(a.diff) >= _MATERIAL_SINGLE_ASSET_MOVE - _ALLOC_EPS for a in allocation_changes):
        levels.add("MATERIAL")
    if curr.get("allocation_override") is True:  # should never happen in shadow mode
        levels.add("MATERIAL")
    if curr.get("human_decision") in _HUMAN_MATERIAL:
        levels.add("MATERIAL")

    # ── CAUTION ──
    worsened = [m for m in member_changes if m.direction == "WORSENED"]
    if len(worsened) >= 2:
        levels.add("CAUTION")
    cm = _member_verdict_map(curr)
    if any(_verdict_sev(cm.get(mid)) >= _SEVERITY[CommitteeVerdict.WATCH] for mid in _KEY_CAUTION_MEMBERS):
        levels.add("CAUTION")
    if len(new_dissent) >= 2:
        levels.add("CAUTION")
    if len(new_triggers) >= 3:
        levels.add("CAUTION")

    # ── INFO ──
    if final_change.changed:
        levels.add("INFO")
    if any(a.direction != "UNCHANGED" for a in allocation_changes):
        levels.add("INFO")
    if len(new_triggers) >= 1:
        levels.add("INFO")
    if recommended_changed:
        levels.add("INFO")

    return max(levels, key=lambda s: _SEVERITY_RANK[s])


def _build_summary(cmp: "ReviewComparison") -> str:
    fc = cmp.final_committee_verdict_change
    parts = [f"[{cmp.severity}]"]
    if fc.changed:
        parts.append(f"最終判定 {fc.previous}→{fc.current}（{fc.direction}）")
    else:
        parts.append(f"最終判定 {fc.current}（変化なし）")
    moves = [a for a in cmp.allocation_changes if a.direction in ("ADDED", "REMOVED")
             or abs(a.diff_pct_point) >= 1.0]
    if moves:
        top = max(moves, key=lambda a: abs(a.diff_pct_point))
        parts.append(f"配分変化最大: {top.ticker} {top.diff_pct_point:+.1f}pt（{top.direction}）")
    if cmp.new_dissenting_views:
        parts.append(f"新規反対意見 {len(cmp.new_dissenting_views)}件")
    if cmp.new_next_review_triggers:
        parts.append(f"新規トリガー {len(cmp.new_next_review_triggers)}件")
    return " / ".join(parts)


# ── public API ───────────────────────────────────────────────────────────────


def build_comparison(prev: dict, curr: dict) -> ReviewComparison:
    """Build a deterministic structured diff between two committee log entries."""
    final_change = _verdict_change(prev.get("final_committee_verdict"), curr.get("final_committee_verdict"))
    core_change = _verdict_change(prev.get("core_committee_verdict"), curr.get("core_committee_verdict"))
    sat_change = _verdict_change(prev.get("satellite_committee_verdict"), curr.get("satellite_committee_verdict"))

    prev_action = prev.get("recommended_action")
    curr_action = curr.get("recommended_action")
    recommended_action_change = {
        "previous": prev_action, "current": curr_action, "changed": prev_action != curr_action,
    }

    member_changes = _member_changes(prev, curr)
    allocation_changes = _allocation_changes(prev, curr)
    new_dissent, resolved_dissent = _dissent_diff(prev, curr)
    new_triggers, resolved_triggers = _trigger_diff(prev, curr)

    cmp = ReviewComparison(
        previous_run_id=prev.get("run_id"),
        current_run_id=curr.get("run_id"),
        previous_date=prev.get("date"),
        current_date=curr.get("date"),
        core_committee_verdict_change=core_change,
        satellite_committee_verdict_change=sat_change,
        final_committee_verdict_change=final_change,
        recommended_action_change=recommended_action_change,
        member_verdict_changes=member_changes,
        allocation_changes=allocation_changes,
        new_dissenting_views=new_dissent,
        resolved_dissenting_views=resolved_dissent,
        new_next_review_triggers=new_triggers,
        resolved_next_review_triggers=resolved_triggers,
    )
    cmp.severity = _compute_severity(
        curr, final_change, member_changes, allocation_changes,
        new_dissent, new_triggers, recommended_action_change["changed"],
    )
    cmp.summary = _build_summary(cmp)
    return cmp


def compare_latest_committee_runs(
    log_path: str | Path = DEFAULT_COMMITTEE_LOG_PATH,
) -> ReviewComparison | None:
    """Compare the latest two valid committee log entries.

    Returns None if fewer than two valid entries exist (comparison not possible).
    Corrupt log lines are skipped by read_committee_decision_log.
    """
    entries = read_committee_decision_log(log_path)
    if len(entries) < 2:
        logger.info("Committee Review Comparison: fewer than 2 log entries — skipped.")
        return None
    prev, curr = entries[-2], entries[-1]
    return build_comparison(prev, curr)


# ── formatters (display-only) ────────────────────────────────────────────────

_SEVERITY_ICON = {"NONE": "•", "INFO": "ℹ️", "CAUTION": "⚠️", "MATERIAL": "🔴"}


def build_comparison_markdown(cmp: ReviewComparison) -> str:
    icon = _SEVERITY_ICON.get(cmp.severity, "")
    lines: list[str] = []
    lines.append("## Committee Review Comparison（前回比・参考）")
    lines.append("")
    lines.append(f"- 重要度: {icon} **{cmp.severity}**")
    lines.append(f"- 対象: `{cmp.previous_date}` ({cmp.previous_run_id}) → `{cmp.current_date}` ({cmp.current_run_id})")
    lines.append(f"- {cmp.summary}")
    lines.append("")
    lines.append("> 🛡️ shadow mode：本比較は参考情報であり、最終配分には影響しません。")
    lines.append("")

    lines.append("### 判定の変化")
    lines.append("")
    lines.append("| 区分 | 前回 | 今回 | 変化 |")
    lines.append("|---|---|---|---|")
    for label, ch in (
        ("Core", cmp.core_committee_verdict_change),
        ("Satellite", cmp.satellite_committee_verdict_change),
        ("Final", cmp.final_committee_verdict_change),
    ):
        lines.append(f"| {label} | {ch.previous} | {ch.current} | {ch.direction} |")
    lines.append("")

    member_changed = [m for m in cmp.member_verdict_changes if m.changed]
    if member_changed:
        lines.append("### メンバー判定の変化")
        lines.append("")
        lines.append("| メンバー | 前回 | 今回 | 変化 |")
        lines.append("|---|---|---|---|")
        for m in member_changed:
            lines.append(f"| {m.member_id} | {m.previous} | {m.current} | {m.direction} |")
        lines.append("")

    alloc_changed = [a for a in cmp.allocation_changes if a.direction != "UNCHANGED"]
    if alloc_changed:
        lines.append("### 配分の変化")
        lines.append("")
        lines.append("| ETF | 前回 | 今回 | 差分(pt) | 区分 |")
        lines.append("|---|---:|---:|---:|---|")
        for a in sorted(alloc_changed, key=lambda x: -abs(x.diff_pct_point)):
            lines.append(
                f"| {a.ticker} | {a.previous_weight:.1%} | {a.current_weight:.1%} "
                f"| {a.diff_pct_point:+.1f} | {a.direction} |"
            )
        lines.append("")

    if cmp.new_dissenting_views or cmp.resolved_dissenting_views:
        lines.append("### 反対意見の変化")
        lines.append("")
        for mid, txt in cmp.new_dissenting_views.items():
            lines.append(f"- 🆕 {mid}: {txt}")
        for mid, txt in cmp.resolved_dissenting_views.items():
            lines.append(f"- ✅ 解消 {mid}: {txt}")
        lines.append("")

    if cmp.new_next_review_triggers or cmp.resolved_next_review_triggers:
        lines.append("### レビュー・トリガーの変化")
        lines.append("")
        for t in cmp.new_next_review_triggers:
            lines.append(f"- 🆕 {t}")
        for t in cmp.resolved_next_review_triggers:
            lines.append(f"- ✅ 解消 {t}")
        lines.append("")

    return "\n".join(lines)


def build_comparison_slack_summary(cmp: ReviewComparison) -> str:
    """Concise (3–5 line) Slack change summary."""
    icon = _SEVERITY_ICON.get(cmp.severity, "")
    fc = cmp.final_committee_verdict_change
    lines = [
        f"*Committee 変化サマリー* {icon} {cmp.severity}",
        (f"最終判定: {fc.previous}→{fc.current}（{fc.direction}）" if fc.changed
         else f"最終判定: {fc.current}（変化なし）"),
    ]
    alloc_moves = [a for a in cmp.allocation_changes
                   if a.direction in ("ADDED", "REMOVED") or abs(a.diff_pct_point) >= 1.0]
    if alloc_moves:
        top = sorted(alloc_moves, key=lambda a: -abs(a.diff_pct_point))[:3]
        lines.append("配分: " + " / ".join(f"{a.ticker} {a.diff_pct_point:+.1f}pt" for a in top))
    if cmp.new_dissenting_views or cmp.new_next_review_triggers:
        lines.append(
            f"新規 反対意見{len(cmp.new_dissenting_views)}件・トリガー{len(cmp.new_next_review_triggers)}件"
        )
    lines.append("🛡️ shadow: 配分への影響なし")
    return "\n".join(lines)
