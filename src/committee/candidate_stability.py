"""Phase 3.7: Candidate Review Stability Check.

Reads `logs/candidate_review_log.jsonl` and, per candidate, compares the latest
two reviews to surface LLM verdict drift (e.g. GRID swinging between
WAIT_FOR_BETTER_ENTRY and REJECT_FOR_NOW). Lets us flag unstable candidates as
"human review required" rather than approving them.

This is a *quality audit* of the review process, NOT an approval decision.
Deterministic Python only — no LLM. Never changes allocation, never sizes orders.
"""
from __future__ import annotations

from datetime import date
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, Field

from src.committee.candidate_decision_logger import (
    DEFAULT_CANDIDATE_LOG_PATH,
    read_candidate_decision_log,
)
from src.logger import logger

_CONF_CAUTION = 0.20   # >= 20 points
_CONF_MINOR = 0.10

# Favorability toward buying (higher = more willing to buy).
_VERDICT_RANK = {
    "REJECT_FOR_NOW": 0,
    "WAIT_FOR_BETTER_ENTRY": 1,
    "APPROVE_FOR_WATCHLIST": 2,
    "APPROVE_SMALL_TEST_BUY": 3,
}
_HUMAN_RANK = {
    "REJECT": 0, "SKIP": 0,
    "WAIT": 1, "RE_REVIEW": 1,
    "WATCHLIST": 2, "SMALL_TEST_BUY_CANDIDATE": 3,
}
_SEVERITY_RANK = {"NONE": 0, "INFO": 1, "CAUTION": 2, "MATERIAL": 3}


class Stability(str, Enum):
    STABLE = "STABLE"
    MINOR_CHANGE = "MINOR_CHANGE"
    UNSTABLE = "UNSTABLE"
    INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"


class StabilitySeverity(str, Enum):
    NONE = "NONE"
    INFO = "INFO"
    CAUTION = "CAUTION"
    MATERIAL = "MATERIAL"


class VerdictDirection(str, Enum):
    IMPROVED = "IMPROVED"
    WORSENED = "WORSENED"
    UNCHANGED = "UNCHANGED"
    MIXED = "MIXED"
    UNKNOWN = "UNKNOWN"


class RecommendedHandling(str, Enum):
    OK_FOR_WATCHLIST = "OK_FOR_WATCHLIST"
    REVIEW_BEFORE_ACTION = "REVIEW_BEFORE_ACTION"
    HUMAN_REVIEW_REQUIRED = "HUMAN_REVIEW_REQUIRED"
    DO_NOT_ACT_YET = "DO_NOT_ACT_YET"


class StabilityResult(BaseModel):
    candidate_symbol: str
    previous_review_id: str | None = None
    current_review_id: str | None = None
    previous_verdict: str | None = None
    current_verdict: str | None = None
    verdict_changed: bool = False
    verdict_direction: VerdictDirection = VerdictDirection.UNKNOWN
    previous_confidence: float | None = None
    current_confidence: float | None = None
    confidence_change: float | None = None
    buy_thesis_changed: bool = False
    rejection_thesis_changed: bool = False
    new_key_risks: list[str] = Field(default_factory=list)
    resolved_key_risks: list[str] = Field(default_factory=list)
    previous_human_decision: str | None = None
    current_human_decision: str | None = None
    human_decision_changed: bool = False
    stability: Stability
    severity: StabilitySeverity
    summary: str = ""
    recommended_handling: RecommendedHandling

    def to_dict(self) -> dict:
        return self.model_dump(mode="json")


# ── helpers ──────────────────────────────────────────────────────────────────


def _group_by_symbol(entries: list[dict]) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = {}
    for e in entries:
        sym = e.get("candidate_symbol")
        if not sym:
            continue
        groups.setdefault(sym, []).append(e)
    return groups


def _direction(pv: str | None, cv: str | None, new_risks: list, rej_changed: bool) -> VerdictDirection:
    if pv not in _VERDICT_RANK or cv not in _VERDICT_RANK:
        return VerdictDirection.UNKNOWN
    if pv == cv:
        return VerdictDirection.UNCHANGED
    if _VERDICT_RANK[cv] > _VERDICT_RANK[pv]:
        # verdict improved, but conflicting evidence => MIXED
        if new_risks or rej_changed:
            return VerdictDirection.MIXED
        return VerdictDirection.IMPROVED
    return VerdictDirection.WORSENED


def _human_mismatch(human_decision: str | None, verdict: str | None) -> bool:
    if human_decision in _HUMAN_RANK and verdict in _VERDICT_RANK:
        return abs(_HUMAN_RANK[human_decision] - _VERDICT_RANK[verdict]) >= 2
    return False


def _insufficient_result(symbol: str, latest: dict | None) -> StabilityResult:
    return StabilityResult(
        candidate_symbol=symbol,
        current_review_id=(latest or {}).get("review_id"),
        current_verdict=(latest or {}).get("candidate_verdict"),
        current_confidence=(latest or {}).get("confidence"),
        current_human_decision=(latest or {}).get("human_decision"),
        verdict_direction=VerdictDirection.UNKNOWN,
        stability=Stability.INSUFFICIENT_HISTORY,
        severity=StabilitySeverity.INFO,
        summary=f"{symbol}: 比較に必要な履歴が不足（有効レビュー1件以下）。",
        recommended_handling=RecommendedHandling.REVIEW_BEFORE_ACTION,
    )


def build_stability(prev: dict, curr: dict) -> StabilityResult:
    """Deterministic stability diff between two candidate-review log entries."""
    symbol = curr.get("candidate_symbol") or prev.get("candidate_symbol") or ""
    pv = prev.get("candidate_verdict")
    cv = curr.get("candidate_verdict")
    verdict_changed = pv != cv

    pc = prev.get("confidence")
    cc = curr.get("confidence")
    conf_change = round((cc or 0.0) - (pc or 0.0), 4) if (pc is not None or cc is not None) else None

    buy_changed = (prev.get("strongest_buy_thesis", "") != curr.get("strongest_buy_thesis", ""))
    rej_changed = (prev.get("strongest_rejection_thesis", "") != curr.get("strongest_rejection_thesis", ""))

    prev_risks = prev.get("key_risks", []) or []
    curr_risks = curr.get("key_risks", []) or []
    pset, cset = set(prev_risks), set(curr_risks)
    new_risks = [r for r in curr_risks if r not in pset]
    resolved_risks = [r for r in prev_risks if r not in cset]

    phd = prev.get("human_decision")
    chd = curr.get("human_decision")
    human_changed = phd != chd

    direction = _direction(pv, cv, new_risks, rej_changed)
    big_conf = conf_change is not None and abs(conf_change) >= _CONF_CAUTION
    minor_conf = conf_change is not None and abs(conf_change) >= _CONF_MINOR
    swung_wait_reject = {pv, cv} == {"WAIT_FOR_BETTER_ENTRY", "REJECT_FOR_NOW"}
    worsened_to_reject = (
        cv == "REJECT_FOR_NOW" and pv in _VERDICT_RANK and _VERDICT_RANK["REJECT_FOR_NOW"] < _VERDICT_RANK.get(pv, 0)
    )
    human_mismatch = _human_mismatch(chd, cv)

    # ── stability ──
    if swung_wait_reject or worsened_to_reject or (verdict_changed and big_conf):
        stability = Stability.UNSTABLE
    elif verdict_changed or buy_changed or rej_changed or new_risks or resolved_risks or minor_conf:
        stability = Stability.MINOR_CHANGE
    else:
        stability = Stability.STABLE

    # ── severity ──
    sev = "NONE"
    if verdict_changed or buy_changed or new_risks or minor_conf:
        sev = "INFO"
    if swung_wait_reject or big_conf or rej_changed or len(new_risks) >= 2:
        sev = max(sev, "CAUTION", key=lambda s: _SEVERITY_RANK[s])
    if worsened_to_reject or human_mismatch:
        sev = "MATERIAL"

    # ── recommended handling ──
    if human_mismatch:
        handling = RecommendedHandling.HUMAN_REVIEW_REQUIRED
    elif cv == "REJECT_FOR_NOW":
        handling = RecommendedHandling.DO_NOT_ACT_YET
    elif stability == Stability.UNSTABLE or _SEVERITY_RANK[sev] >= _SEVERITY_RANK["CAUTION"]:
        handling = RecommendedHandling.REVIEW_BEFORE_ACTION
    elif pv == cv == "APPROVE_SMALL_TEST_BUY" and not new_risks:
        handling = RecommendedHandling.OK_FOR_WATCHLIST
    else:
        handling = RecommendedHandling.REVIEW_BEFORE_ACTION

    conf_txt = f"{conf_change:+.0%}" if conf_change is not None else "—"
    summary = (
        f"[{stability.value}/{sev}] {symbol} {pv}→{cv}（{direction.value}）"
        f" / confΔ {conf_txt}"
        + (f" / 新規リスク{len(new_risks)}件" if new_risks else "")
        + (" / 人間判断と乖離" if human_mismatch else "")
    )

    return StabilityResult(
        candidate_symbol=symbol,
        previous_review_id=prev.get("review_id"),
        current_review_id=curr.get("review_id"),
        previous_verdict=pv,
        current_verdict=cv,
        verdict_changed=verdict_changed,
        verdict_direction=direction,
        previous_confidence=pc,
        current_confidence=cc,
        confidence_change=conf_change,
        buy_thesis_changed=buy_changed,
        rejection_thesis_changed=rej_changed,
        new_key_risks=new_risks,
        resolved_key_risks=resolved_risks,
        previous_human_decision=phd,
        current_human_decision=chd,
        human_decision_changed=human_changed,
        stability=stability,
        severity=StabilitySeverity(sev),
        summary=summary,
        recommended_handling=handling,
    )


def check_candidate_stability(
    log_path: str | Path = DEFAULT_CANDIDATE_LOG_PATH,
    symbol: str | None = None,
) -> list[StabilityResult]:
    """Per-candidate stability from the latest two valid log entries.

    Corrupt lines are skipped by read_candidate_decision_log. Candidates with
    fewer than two entries get an INSUFFICIENT_HISTORY result.
    """
    entries = read_candidate_decision_log(log_path)
    groups = _group_by_symbol(entries)
    if symbol:
        sym = symbol.strip().upper()
        groups = {s: v for s, v in groups.items() if s.upper() == sym}

    results: list[StabilityResult] = []
    for sym, items in groups.items():
        if len(items) < 2:
            results.append(_insufficient_result(sym, items[-1] if items else None))
        else:
            results.append(build_stability(items[-2], items[-1]))
    return results


# ── formatters ────────────────────────────────────────────────────────────────

_STABILITY_ICON = {
    Stability.STABLE: "✅",
    Stability.MINOR_CHANGE: "ℹ️",
    Stability.UNSTABLE: "⚠️",
    Stability.INSUFFICIENT_HISTORY: "❓",
}


def build_stability_markdown(results: list[StabilityResult], review_date: str | None = None) -> str:
    review_date = review_date or date.today().isoformat()
    lines: list[str] = []
    lines.append("# Candidate Stability Check（判定安定性の監査）")
    lines.append(f"**チェック日:** {review_date}")
    lines.append("")
    lines.append(
        "> 🛡️ これは判定品質の監査であり、承認判断ではありません。"
        "配分変更・注文数量計算・自動売買・証券口座連携は行いません。"
    )
    lines.append("")
    for r in results:
        icon = _STABILITY_ICON.get(r.stability, "")
        lines.append(f"## {r.candidate_symbol}")
        lines.append("")
        lines.append(f"- 安定性: {icon} **{r.stability.value}** / 重要度: **{r.severity.value}**")
        lines.append(f"- verdict: {r.previous_verdict} → {r.current_verdict}（{r.verdict_direction.value}）")
        if r.confidence_change is not None:
            lines.append(f"- confidence変化: {r.confidence_change:+.0%}（{r.previous_confidence} → {r.current_confidence}）")
        if r.human_decision_changed or r.current_human_decision:
            lines.append(f"- 人間判断: {r.previous_human_decision} → {r.current_human_decision}")
        lines.append(f"- 推奨対応: **{r.recommended_handling.value}**")
        if r.new_key_risks:
            lines.append(f"- 新規リスク: {', '.join(r.new_key_risks)}")
        if r.resolved_key_risks:
            lines.append(f"- 解消リスク: {', '.join(r.resolved_key_risks)}")
        lines.append(f"- 要約: {r.summary}")
        lines.append("")
    return "\n".join(lines)


def save_stability_report(
    markdown: str,
    review_date: str | None = None,
    out_dir: str | Path = "reports/candidates",
) -> str:
    review_date = review_date or date.today().isoformat()
    stamp = review_date.replace("-", "")
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"candidate_stability_{stamp}.md"
    path.write_text(markdown, encoding="utf-8")
    logger.info(f"Candidate stability report saved to {path}")
    return str(path)


def build_stability_slack_summary(result: StabilityResult) -> str:
    icon = _STABILITY_ICON.get(result.stability, "")
    return "\n".join([
        f"*Candidate Stability: {result.candidate_symbol}* {icon} {result.stability.value} / {result.severity.value}",
        f"verdict: {result.previous_verdict} → {result.current_verdict}",
        f"推奨対応: {result.recommended_handling.value}",
        f"{result.summary}",
        "🛡️ 監査のみ・配分変更/数量計算なし",
    ])
