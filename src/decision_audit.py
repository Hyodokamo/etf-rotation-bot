"""Phase 5: Integrated Decision Audit Summary.

Aggregates the monthly review, Investment Committee, Candidate Review, Candidate
Stability, Slack button presses, and human notes into one monthly investment-
decision audit (Markdown / CLI): who decided what, where AI and human diverged,
and what to check next.

Deterministic Python only (no LLM). Retrospective audit, NOT a trade
instruction: no 買え/売れ/注文 wording, no allocation change, no order quantity,
no auto-trading. Secrets in logs never surface (only whitelisted fields read).
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

from src.committee.candidate_decision_logger import (
    DEFAULT_CANDIDATE_LOG_PATH,
    read_candidate_decision_log,
)
from src.committee.candidate_stability import check_candidate_stability
from src.committee.decision_logger import (
    DEFAULT_COMMITTEE_LOG_PATH,
    read_committee_decision_log,
)
from src.decision_audit_models import (
    CandidateAuditEntry,
    DecisionAudit,
    HumanDecisionRecord,
    HumanNote,
    MonthlyAuditSummary,
)
from src.logger import logger
from src.slack_actions import DEFAULT_SLACK_LOG_PATH, read_slack_decision_log

# AI candidate verdict favorability (higher = more willing to buy)
_AI_RANK = {
    "REJECT_FOR_NOW": 0,
    "WAIT_FOR_BETTER_ENTRY": 1,
    "APPROVE_FOR_WATCHLIST": 2,
    "APPROVE_SMALL_TEST_BUY": 3,
}
_HUMAN_RANK = {
    "REJECT": 0, "SKIP": 0,
    "WAIT": 1, "RE_REVIEW": 1,
    "WATCHLIST": 2,
    "SMALL_TEST_BUY_CANDIDATE": 3,
}

def _month_of(entry: dict) -> str | None:
    for k in ("review_date", "date", "timestamp", "value_generated_at"):
        v = entry.get(k)
        if isinstance(v, str) and len(v) >= 7:
            return v[:7]
    return None


def _in_month(entries: list[dict], month: str) -> list[dict]:
    return [e for e in entries if _month_of(e) == month]


def classify_alignment(ai_verdict: str | None, human_decision: str | None) -> str:
    """Classify AI candidate verdict vs human decision."""
    if not human_decision:
        return "no_human_decision"
    ai_rank = _AI_RANK.get(ai_verdict or "")
    human_rank = _HUMAN_RANK.get(human_decision)
    if ai_rank is None or human_rank is None:
        return "unknown"
    if ai_rank == human_rank:
        return "aligned"
    # AI strongly cautioned (reject) but human is less cautious -> divergence
    if ai_verdict == "REJECT_FOR_NOW" and human_rank > 0:
        return "divergence"
    # AI willing to test-buy but human rejected -> divergence
    if ai_verdict == "APPROVE_SMALL_TEST_BUY" and human_rank == 0:
        return "divergence"
    if abs(ai_rank - human_rank) >= 2:
        return "divergence"
    return "mild_divergence"


def _latest(entries: list[dict]) -> dict | None:
    return entries[-1] if entries else None


def build_decision_audit(
    month: str,
    *,
    committee_log_path=DEFAULT_COMMITTEE_LOG_PATH,
    candidate_log_path=DEFAULT_CANDIDATE_LOG_PATH,
    slack_log_path=DEFAULT_SLACK_LOG_PATH,
) -> DecisionAudit:
    """Build the integrated monthly decision audit (deterministic)."""
    committee = _in_month(read_committee_decision_log(committee_log_path), month)
    candidate_all = read_candidate_decision_log(candidate_log_path)
    candidate = _in_month(candidate_all, month)
    slack = _in_month(read_slack_decision_log(slack_log_path), month)

    # ── stability map (computed over the full candidate log) ──
    stability_map: dict[str, str] = {}
    try:
        for s in check_candidate_stability(candidate_log_path):
            stability_map[s.candidate_symbol] = s.stability.value
    except Exception as e:  # never fail the audit on stability errors
        logger.warning(f"stability map skipped: {type(e).__name__}: {e}")

    # ── monthly summary ──
    monthly = MonthlyAuditSummary()
    committee_reviews = [e for e in committee if e.get("entry_type") != "note"]
    latest_committee = _latest(committee_reviews)
    if latest_committee:
        monthly.committee_verdict = latest_committee.get("final_committee_verdict")
        monthly.core_verdict = latest_committee.get("core_committee_verdict")
        monthly.satellite_verdict = latest_committee.get("satellite_committee_verdict")
        # human decision recorded at committee-run time
        if latest_committee.get("human_decision"):
            monthly.human_decisions.append(HumanDecisionRecord(
                decision=latest_committee.get("human_decision"),
                timestamp=latest_committee.get("timestamp"), source="committee_log",
            ))
    # monthly human decisions recorded via Slack buttons
    for e in slack:
        if e.get("source_type") == "monthly_review" and e.get("entry_type") != "note" and e.get("human_decision"):
            monthly.human_decisions.append(HumanDecisionRecord(
                decision=e.get("human_decision"), user_id=e.get("user_id"),
                timestamp=e.get("timestamp"), source="slack_log",
            ))

    # ── candidate entries (group by symbol) ──
    cand_reviews: dict[str, dict] = {}
    for e in candidate:
        if e.get("entry_type") == "note":
            continue
        sym = e.get("candidate_symbol")
        if sym:
            cand_reviews[sym] = e  # keep latest (entries are in append order)

    # human decisions per candidate: candidate-log field + slack candidate presses
    cand_human: dict[str, dict] = {}
    for e in candidate:
        if e.get("entry_type") != "note" and e.get("human_decision") and e.get("candidate_symbol"):
            cand_human[e["candidate_symbol"]] = {"decision": e["human_decision"],
                                                 "user_id": e.get("user_id"),
                                                 "timestamp": e.get("timestamp")}
    for e in slack:
        if (e.get("source_type") == "candidate_review" and e.get("entry_type") != "note"
                and e.get("human_decision") and e.get("candidate_symbol")):
            cand_human[e["candidate_symbol"]] = {"decision": e["human_decision"],
                                                 "user_id": e.get("user_id"),
                                                 "timestamp": e.get("timestamp")}

    candidates: list[CandidateAuditEntry] = []
    for sym in sorted(set(cand_reviews) | set(cand_human)):
        rev = cand_reviews.get(sym, {})
        hum = cand_human.get(sym, {})
        ai_v = rev.get("candidate_verdict")
        hum_d = hum.get("decision")
        entry = CandidateAuditEntry(
            symbol=sym, ai_verdict=ai_v, human_decision=hum_d,
            human_user_id=hum.get("user_id"),
            stability=stability_map.get(sym),
            alignment=classify_alignment(ai_v, hum_d),
            timestamp=hum.get("timestamp") or rev.get("timestamp"),
        )
        candidates.append(entry)

    divergences = [c for c in candidates if c.alignment in ("divergence", "mild_divergence")]
    unstable = [c for c in candidates if c.stability == "UNSTABLE"]

    # ── human notes (monthly + candidate, from all logs) ──
    notes: list[HumanNote] = []
    for e in committee:
        if e.get("human_note"):
            notes.append(HumanNote(scope="monthly_review", target="月次レビュー",
                                    note=e["human_note"], timestamp=e.get("timestamp")))
    for e in slack:
        if e.get("entry_type") == "note" and e.get("human_note"):
            notes.append(HumanNote(
                scope=e.get("source_type", ""), target=e.get("candidate_symbol") or "月次レビュー",
                note=e["human_note"], user_id=e.get("user_id"), timestamp=e.get("timestamp")))
    for e in candidate:
        if e.get("entry_type") == "note" and e.get("human_note"):
            notes.append(HumanNote(scope="candidate_review", target=e.get("candidate_symbol"),
                                   note=e["human_note"], user_id=e.get("user_id"),
                                   timestamp=e.get("timestamp")))

    # ── next review items (committee triggers + candidate checks/triggers) ──
    items: list[str] = []
    for e in committee_reviews:
        for t in e.get("next_review_triggers", []) or []:
            if t and t not in items:
                items.append(t)
    for e in cand_reviews.values():
        for t in (e.get("required_checks", []) or []):
            label = f"{e.get('candidate_symbol')}: {t}"
            if label not in items:
                items.append(label)
        for m in e.get("member_outputs", []) or []:
            for t in m.get("next_review_triggers", []) or []:
                if t and t not in items:
                    items.append(t)
    items = items[:10]

    # ── conclusion (1-2 lines) ──
    n_div = len([c for c in divergences if c.alignment == "divergence"])
    conclusion = (
        f"{month}: 月次委員会判定={monthly.committee_verdict or 'N/A'}"
        f"、人間判断={'/'.join(h.decision for h in monthly.human_decisions) or '記録なし'}。"
        f"Candidate {len(candidates)}件中、判断ズレ {len(divergences)}件（うち重大 {n_div}件）、"
        f"不安定 {len(unstable)}件。配分変更なし（監査のみ）。"
    )

    return DecisionAudit(
        month=month, conclusion=conclusion, monthly=monthly, candidates=candidates,
        divergences=divergences, unstable_candidates=unstable, human_notes=notes,
        next_review_items=items,
    )


# ── markdown ──────────────────────────────────────────────────────────────────

_ALIGN_ICON = {"aligned": "✅", "mild_divergence": "⚠️", "divergence": "🔴",
               "unknown": "❓", "no_human_decision": "—"}


def build_audit_markdown(audit: DecisionAudit) -> str:
    lines: list[str] = []
    lines.append(f"# Integrated Decision Audit — {audit.month}")
    lines.append("")
    lines.append("## 今月の結論")
    lines.append("")
    lines.append(audit.conclusion)
    lines.append("")

    lines.append("## 月次レビュー判断")
    lines.append("")
    m = audit.monthly
    lines.append(f"- Committee判定: Final={m.committee_verdict or 'N/A'} / "
                 f"Core={m.core_verdict or 'N/A'} / Satellite={m.satellite_verdict or 'N/A'}")
    if m.human_decisions:
        for h in m.human_decisions:
            who = f" by {h.user_id}" if h.user_id else ""
            lines.append(f"- 人間判断: {h.decision}{who}（{h.source}, {(h.timestamp or '')[:16]}）")
    else:
        lines.append("- 人間判断: 記録なし")
    lines.append("")

    lines.append("## Candidate Review判断")
    lines.append("")
    if audit.candidates:
        lines.append("| 候補 | AI判定 | 人間判断 | Stability | 整合 |")
        lines.append("|---|---|---|---|---|")
        for c in audit.candidates:
            lines.append(
                f"| {c.symbol} | {c.ai_verdict or '—'} | {c.human_decision or '—'} "
                f"| {c.stability or '—'} | {_ALIGN_ICON.get(c.alignment, '')} {c.alignment} |"
            )
    else:
        lines.append("（Candidate Reviewの記録はありません）")
    lines.append("")

    lines.append("## AI委員会 vs 人間判断")
    lines.append("")
    aligned = [c for c in audit.candidates if c.alignment == "aligned"]
    lines.append(f"- 整合: {len(aligned)}件 / 判断ズレ: {len(audit.divergences)}件 "
                 f"/ 人間判断なし: {len([c for c in audit.candidates if c.alignment == 'no_human_decision'])}件")
    lines.append("")

    lines.append("## 判断が割れた項目")
    lines.append("")
    if audit.divergences:
        for c in audit.divergences:
            lines.append(f"- {_ALIGN_ICON.get(c.alignment, '')} {c.symbol}: "
                         f"AI={c.ai_verdict} / Human={c.human_decision}（{c.alignment}）")
    else:
        lines.append("（判断ズレはありません）")
    lines.append("")

    lines.append("## Stabilityが不安定な候補")
    lines.append("")
    if audit.unstable_candidates:
        for c in audit.unstable_candidates:
            lines.append(f"- ⚠️ {c.symbol}: stability=UNSTABLE（AI={c.ai_verdict or '—'} / Human={c.human_decision or '—'}）")
    else:
        lines.append("（不安定な候補はありません）")
    lines.append("")

    lines.append("## 人間メモ一覧")
    lines.append("")
    if audit.human_notes:
        for n in audit.human_notes:
            who = f" by {n.user_id}" if n.user_id else ""
            lines.append(f"- [{n.target}] {n.note}{who}（{(n.timestamp or '')[:16]}）")
    else:
        lines.append("（メモはありません）")
    lines.append("")

    lines.append("## 次回確認事項")
    lines.append("")
    if audit.next_review_items:
        for t in audit.next_review_items:
            lines.append(f"- {t}")
    else:
        lines.append("（特になし）")
    lines.append("")

    lines.append("## 安全注記")
    lines.append("")
    lines.append("- 本サマリーは投資判断の振り返り（監査）であり、売買指示ではありません。")
    lines.append("- 配分変更・注文数量計算・自動売買・証券口座連携は行いません。")
    lines.append("- 最終配分は常に定量モデルの推奨値（quant推奨）を維持しています。")
    lines.append("")
    return "\n".join(lines)


def save_audit_report(
    markdown: str,
    month: str,
    out_dir: str | Path = "reports/audit",
    output_path: str | Path | None = None,
) -> str:
    if output_path:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
    else:
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        path = out / f"decision_audit_{month.replace('-', '')}.md"
    path.write_text(markdown, encoding="utf-8")
    logger.info(f"Decision audit report saved to {path}")
    return str(path)
