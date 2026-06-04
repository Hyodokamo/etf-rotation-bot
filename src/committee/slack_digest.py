"""Phase 3.6.1: Slack Executive Digest.

Turns the monthly run artifacts (allocation, AI audit, Investment Committee,
Advisory, Comparison) into a concise, phone-readable committee summary — not a
"go read the file" ping. Detail stays in the Markdown report; Slack stays short.

Deterministic only: built from existing Committee outputs, never re-calls an LLM,
never changes allocation or sizes orders. Selectors accept either a CommitteeResult
(monthly) or a list of member dicts (candidate review), so the same digest helpers
work for both.
"""
from __future__ import annotations

import re

# Concrete-term signals used to prioritize specific over generic commentary.
_TICKERS = [
    "QQQM", "BND", "VTV", "VOO", "SGOV", "UUP", "SMH", "GLDM", "IEF", "TLT",
    "DBC", "VWO", "VXUS", "INDA", "MTUM", "IWM", "XLV", "XLE", "XLI", "XLF", "VNQ",
    "S&P500", "NASDAQ",
]
_KEYWORDS = [
    "高バリュエーション", "割高", "防御", "集中", "過熱", "データ品質", "過剰最適化",
    "平均回帰", "200日", "モメンタム", "相対強度", "ルール", "損切り", "サイクル",
]

_VERDICT_RANK = {
    "PASS": 0, "PASS_WITH_CAUTION": 1, "WATCH": 2, "INSUFFICIENT_DATA": 3, "REJECT": 4,
}

_LABELS = {
    "aqr_meb": "AQR/Meb",
    "howard_marks": "Howard Marks",
    "rob_arnott": "Rob Arnott",
    "buffett": "Buffett",
    "paul_tudor_jones": "Paul Tudor Jones",
    "druckenmiller": "Druckenmiller",
    "core_ai_auditor": "AI Auditor",
}
# Fixed display order for the per-agent one-liner section.
_AGENT_ORDER = [
    "aqr_meb", "howard_marks", "rob_arnott", "buffett",
    "paul_tudor_jones", "druckenmiller", "core_ai_auditor",
]
# Members used for the "Committee 論点" (debate highlights) section.
_DEBATE_IDS = ["aqr_meb", "howard_marks", "rob_arnott", "core_ai_auditor"]

_ONE_LINER_MAX = 80
_AUDITOR_QUALITY_TERMS = ("品質", "データ", "ルール", "過剰最適化", "根拠")


# ── normalization ────────────────────────────────────────────────────────────


def _normalize_members(source) -> list[dict]:
    """Accept a CommitteeResult or a list of MemberOutput/dict; return dicts."""
    members = getattr(source, "members", source) or []
    out: list[dict] = []
    for m in members:
        d = m.model_dump(mode="json") if hasattr(m, "model_dump") else dict(m)
        out.append(d)
    return out


def _rank(verdict: str | None) -> int:
    return _VERDICT_RANK.get(verdict or "", 0)


def _is_warning(verdict: str | None) -> bool:
    return _rank(verdict) >= 1


def _concrete_score(text: str | None) -> int:
    if not text:
        return 0
    score = 0
    for t in _TICKERS:
        if t in text:
            score += 2
    for k in _KEYWORDS:
        if k in text:
            score += 1
    score += len(re.findall(r"\d+(?:\.\d+)?%?", text))
    return score


def _trunc(text: str, limit: int = _ONE_LINER_MAX) -> str:
    text = " ".join((text or "").split())  # collapse whitespace/newlines
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _first_concrete(candidates: list) -> str:
    """Pick the most concrete non-empty string; fall back to first non-empty."""
    best = ""
    best_score = -1
    for c in candidates:
        if isinstance(c, list):
            c = c[0] if c else ""
        if not c:
            continue
        s = _concrete_score(c)
        if s > best_score:
            best_score = s
            best = c
    return best


# ── agent one-liners ─────────────────────────────────────────────────────────


def _agent_one_liner_text(m: dict) -> str:
    mid = m.get("member_id")
    verdict = m.get("verdict")

    if mid == "core_ai_auditor":
        base = _first_concrete([
            m.get("rationale"), m.get("dissenting_view"),
            m.get("key_risks"), m.get("strongest_objection"),
        ])
        if not base:
            base = f"{verdict}: ルール違反は検出せず、根拠の強さを監査"
        if not any(k in base for k in _AUDITOR_QUALITY_TERMS):
            base = "データ品質観点: " + base
        return _trunc(base)

    if _is_warning(verdict):
        base = _first_concrete([
            m.get("dissenting_view"), m.get("strongest_objection"),
            m.get("key_risks"), m.get("rationale"),
        ])
        if not base:
            base = f"{verdict}：警戒（詳細はレポート参照）"
    else:
        base = _first_concrete([m.get("strongest_support"), m.get("rationale")])
        if not base:
            base = "ルール上は許容"
    return _trunc(base)


def build_agent_one_liners(source) -> list[dict]:
    """One concise line per committee member (fixed order, <=80 chars each)."""
    by_id = {m.get("member_id"): m for m in _normalize_members(source)}
    out: list[dict] = []
    for mid in _AGENT_ORDER:
        if mid not in by_id:
            continue
        text = _agent_one_liner_text(by_id[mid])
        label = _LABELS.get(mid, mid)
        out.append({"member_id": mid, "label": label, "text": text, "line": f"{label}: {text}"})
    return out


# ── debate highlights ────────────────────────────────────────────────────────


def _debate_comment(m: dict) -> str:
    if _is_warning(m.get("verdict")):
        base = _first_concrete([
            m.get("dissenting_view"), m.get("strongest_objection"),
            m.get("key_risks"), m.get("rationale"),
        ])
    else:
        base = _first_concrete([m.get("strongest_support"), m.get("rationale")])
    return _trunc(base or f"{m.get('verdict')}", 90)


def build_committee_debate_highlights(source, max_points: int = 4) -> list[str]:
    """Up to `max_points` '対立軸' lines, warning members first, auditor included."""
    by_id = {m.get("member_id"): m for m in _normalize_members(source)}
    targets = [by_id[i] for i in _DEBATE_IDS if i in by_id]
    # warning severity desc, then concrete desc
    targets.sort(
        key=lambda m: (
            _rank(m.get("verdict")),
            _concrete_score(m.get("dissenting_view") or m.get("strongest_objection") or m.get("rationale")),
        ),
        reverse=True,
    )
    selected = targets[:max_points]
    # ensure core_ai_auditor included if present
    if "core_ai_auditor" in by_id and not any(m.get("member_id") == "core_ai_auditor" for m in selected):
        if selected:
            selected[-1] = by_id["core_ai_auditor"]
        else:
            selected = [by_id["core_ai_auditor"]]
    lines = []
    for m in selected:
        label = _LABELS.get(m.get("member_id"), m.get("member_id"))
        lines.append(f"{label}（{m.get('verdict')}）: {_debate_comment(m)}")
    return lines


# ── dissent / triggers / advisory selectors ──────────────────────────────────


def select_top_dissenting_views(source, top: int = 3) -> list[str]:
    members = [m for m in _normalize_members(source) if (m.get("dissenting_view") or "").strip()]
    members.sort(
        key=lambda m: (_rank(m.get("verdict")), _concrete_score(m.get("dissenting_view"))),
        reverse=True,
    )
    out = []
    for m in members[:top]:
        label = _LABELS.get(m.get("member_id"), m.get("member_id"))
        out.append(f"{label}: {_trunc(m.get('dissenting_view'), 90)}")
    return out


def select_top_review_triggers(source, top: int = 3) -> list[str]:
    triggers = list(getattr(source, "next_review_triggers", []) or [])
    if not triggers:  # candidate path: gather from member dicts
        for m in _normalize_members(source):
            triggers.extend(m.get("next_review_triggers", []) or [])
    seen: list[str] = []
    for t in triggers:
        if t and t not in seen:
            seen.append(t)
    seen.sort(key=_concrete_score, reverse=True)
    return [_trunc(t, 90) for t in seen[:top]]


def build_advisory_top(advisory, top: int = 3) -> list[str]:
    if advisory is None:
        return []
    items = list(getattr(advisory, "action_items", []) or [])
    items.sort(key=lambda it: {"HIGH": 0, "MEDIUM": 1, "LOW": 2}.get(getattr(it.priority, "value", str(it.priority)), 3))
    out = []
    for it in items[:top]:
        pr = getattr(it.priority, "value", str(it.priority))
        out.append(f"[{pr}] {_trunc(it.message, 90)}")
    return out


# ── executive digest ─────────────────────────────────────────────────────────


def build_executive_digest(
    weights: dict[str, float],
    risk_off: bool,
    turnover: float | None,
    report_path: str,
    audit_result=None,
    turnover_cfg=None,
    risk_mode_check=None,
    pre_trade_gate=None,
    strategy_variant: str | None = None,
    committee_result=None,
    committee_advisory=None,
    committee_comparison=None,
    slack_review_cfg=None,
) -> str:
    """Compose the concise Slack Investment Committee digest (display-only)."""
    risk = "⚠️ リスクオフ" if risk_off else "✅ リスクオン"
    top5 = sorted(weights.items(), key=lambda x: -x[1])[:5]
    top5_str = " · ".join(f"{t} {w:.1%}" for t, w in top5)

    if turnover is not None:
        to_str = f"{turnover:.1%}"
        if turnover_cfg is not None:
            to_str += f"（上限{turnover_cfg.effective_limit:.0%}）"
    else:
        to_str = "N/A（初回）"

    final_verdict = committee_result.final_committee_verdict.value if committee_result else None
    stance = committee_advisory.overall_stance.value if committee_advisory else None

    # 今月の結論（1文）
    if final_verdict:
        conclusion = (
            f"{'リスクオフ' if risk_off else 'リスクオン'}。"
            f"委員会最終判定は {final_verdict}"
            + (f"、助言スタンスは {stance}" if stance else "")
            + "。配分変更なし（shadow）。"
        )
    else:
        conclusion = f"{'リスクオフ' if risk_off else 'リスクオン'}。定量モデル配分を維持（shadow、配分変更なし）。"

    lines: list[str] = []
    lines.append("*📋 ETF Rotation Bot — Investment Committee Digest*")
    lines.append(f"*今月の結論:* {conclusion}")
    lines.append(f"戦略 `{strategy_variant or '-'}` / モード {risk} / ターンオーバー {to_str}")
    lines.append(f"Top配分: {top5_str}")

    if audit_result is not None:
        status_val = audit_result.status.value
        icon = {"PASS": "✅", "PASS_WITH_CAUTION": "⚠️", "REVIEW_REQUIRED": "🔶", "REJECT": "❌"}.get(status_val, "")
        lines.append(f"AI監査: {icon} {status_val} — {_trunc(audit_result.summary, 90)}")

    if pre_trade_gate is not None and getattr(pre_trade_gate, "enabled", False):
        g_icon = {"PASS": "✅", "FAIL": "❌", "PASS_WITH_CAUTION": "⚠️", "REVIEW_REQUIRED": "🔶"}.get(
            pre_trade_gate.overall_status, "")
        lines.append(f"Pre-Trade Gate: {g_icon} {pre_trade_gate.overall_status}")

    if committee_result is not None:
        lines.append(
            f"*🧑‍⚖️ Committee最終判定:* {final_verdict}"
            f"（Core {committee_result.core_committee_verdict.value} / "
            f"Satellite {committee_result.satellite_committee_verdict.value}）"
        )

        debate = build_committee_debate_highlights(committee_result, max_points=4)
        if debate:
            lines.append("*Committee 論点:*")
            lines.extend(f"• {d}" for d in debate)

        one_liners = build_agent_one_liners(committee_result)
        if one_liners:
            lines.append("*🗣 各エージェントの一言:*")
            lines.extend(f"• {o['line']}" for o in one_liners)

        dissent = select_top_dissenting_views(committee_result, top=3)
        if dissent:
            lines.append("*主な反対意見 Top3:*")
            lines.extend(f"• {d}" for d in dissent)

        triggers = select_top_review_triggers(committee_result, top=3)
        if triggers:
            lines.append("*次回までの監視条件 Top3:*")
            lines.extend(f"• {t}" for t in triggers)

    advisory_top = build_advisory_top(committee_advisory, top=3)
    if advisory_top:
        lines.append("*Committee Advisory:*")
        lines.extend(f"• {a}" for a in advisory_top)

    if committee_comparison is not None:
        lines.append(
            f"前回比: {committee_comparison.severity} "
            f"({committee_comparison.final_committee_verdict_change.previous}→"
            f"{committee_comparison.final_committee_verdict_change.current})"
        )

    lines.append("🛡️ shadow mode：配分変更なし／売買数量計算なし／自動売買なし")

    # Preserve Phase 3 monthly review-decision section.
    review_enabled = slack_review_cfg.enabled if slack_review_cfg is not None else False
    if review_enabled and pre_trade_gate is not None:
        from src.slack_blocks import build_review_decision_section
        gate_failures = [
            c.check_id for c in pre_trade_gate.checks
            if c.status in ("FAIL", "REVIEW_REQUIRED") and c.severity != "INFO"
        ]
        lines.append(build_review_decision_section(pre_trade_gate.overall_status, gate_failures))

    lines.append(f"詳細レポート: {report_path}")
    return "\n".join(lines)
