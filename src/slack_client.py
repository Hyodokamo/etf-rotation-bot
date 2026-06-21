from __future__ import annotations

import os
import re

import requests

from src.logger import logger
from src.slack_blocks import build_mobile_summary_header, build_review_decision_section


def post_to_slack(message: str) -> bool:
    """Post message to Slack via Incoming Webhook.

    Returns True on success, False if webhook URL is missing or request fails.
    """
    webhook_url = os.environ.get("SLACK_WEBHOOK_URL", "").strip()
    if not webhook_url:
        logger.warning("SLACK_WEBHOOK_URL is not set — Slack notification skipped")
        return False

    try:
        resp = requests.post(
            webhook_url,
            json={"text": message},
            timeout=10,
        )
        resp.raise_for_status()
        logger.info("Slack notification sent successfully")
        return True
    except requests.RequestException as e:
        logger.error(f"Slack notification failed: {e}")
        return False


_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


def _extract_run_date(report_path: str) -> str | None:
    """Best-effort YYYY-MM-DD from a report path (e.g. outputs/report_2026-06-20.md)."""
    m = _DATE_RE.search(report_path or "")
    return m.group(1) if m else None


def build_slack_summary(
    weights: dict[str, float],
    risk_off: bool,
    turnover: float | None,
    report_path: str,
    audit_result=None,
    proposed_turnover: float | None = None,
    turnover_cfg=None,
    risk_mode_check=None,
    pre_trade_gate=None,
    strategy_variant: str | None = None,
    slack_review_cfg=None,
    committee_result=None,
    committee_comparison=None,
    committee_advisory=None,
) -> str:
    top5 = sorted(weights.items(), key=lambda x: -x[1])[:5]
    # Mobile-friendly: single-line Top配分 (避けるスマホ折返し)。
    top5_str = " · ".join(f"{t} {w:.1%}" for t, w in top5)
    mode = "⚠️ リスクオフ" if risk_off else "✅ リスクオン"

    # Turnover summary with mode info (Phase 2.3)
    if turnover is not None:
        if turnover_cfg is not None and turnover_cfg.migration_mode:
            to_str = f"{turnover:.1%}（移行運用モード、通常上限{turnover_cfg.normal_limit:.0%}）"
        elif turnover_cfg is not None:
            to_str = f"{turnover:.1%}（通常運用、上限{turnover_cfg.effective_limit:.0%}）"
        else:
            to_str = f"{turnover:.1%}"
    else:
        to_str = "N/A（初回）"

    # Mobile-first header (MVP-1): 1行結論・推奨アクション・急ぎ度・自動売買しない注記。
    gate_status = pre_trade_gate.overall_status if pre_trade_gate is not None else None
    run_date = _extract_run_date(report_path)

    lines = [
        build_mobile_summary_header(gate_status, run_date),
        f"戦略: `{strategy_variant}`" if strategy_variant else None,
        f"モード: {mode}",
        f"ターンオーバー: {to_str}",
        f"Top配分: {top5_str}",
    ]
    lines = [l for l in lines if l is not None]

    if audit_result is not None:
        status_val = audit_result.status.value
        status_icon = {"PASS": "✅", "PASS_WITH_CAUTION": "⚠️", "REVIEW_REQUIRED": "🔶", "REJECT": "❌"}.get(
            status_val, ""
        )
        lines.append(f"AI監査: {status_icon} {status_val} — {audit_result.summary[:80]}")
        if audit_result.adjustments_invalidated:
            lines.append("AI参考調整案：制約違反のため未採用（±5%超）")
            lines.append("実配分への反映：なし")

    # Pre-Trade Gate summary (Phase 2.6)
    if pre_trade_gate is not None and pre_trade_gate.enabled:
        gate_icon = {
            "PASS": "✅", "FAIL": "❌", "PASS_WITH_CAUTION": "⚠️", "REVIEW_REQUIRED": "🔶",
        }.get(pre_trade_gate.overall_status, "")
        lines.append(f"Pre-Trade Gate: {gate_icon} {pre_trade_gate.overall_status}")
        problem_checks = [
            c for c in pre_trade_gate.checks
            if c.status in ("FAIL", "REVIEW_REQUIRED", "PASS_WITH_CAUTION")
            and c.severity != "INFO"
        ]
        for chk in problem_checks[:3]:
            lines.append(f"- {chk.message}")

    # Risk-ON defensive check (Phase 2.4) — shown only when no pre_trade_gate
    elif risk_mode_check is not None and risk_mode_check.enabled and risk_mode_check.status != "PASS":
        dw = risk_mode_check.defensive_weight
        if risk_mode_check.status == "REVIEW_REQUIRED":
            lines.append(f"防御資産比率：{dw:.1%}（Risk-ONだが高め、要確認）")
        else:
            lines.append(f"防御資産比率：{dw:.1%}（Risk-ONだがやや高め）")

    lines.append(
        f"📄 詳細レポート: {report_path}"
        "（スマホで開けない場合あり／本要約で判断可）"
    )

    # Phase 3.1: Investment Committee summary (shadow mode, display-only)
    if committee_result is not None:
        from src.committee.report_formatter import build_committee_slack_summary
        lines.append(build_committee_slack_summary(committee_result))

    # Phase 3.3: Committee change summary vs previous run (only when available)
    if committee_comparison is not None:
        from src.committee.review_comparison import build_comparison_slack_summary
        lines.append(build_comparison_slack_summary(committee_comparison))

    # Phase 3.4: Committee advisory (only when available)
    if committee_advisory is not None:
        from src.committee.advisory import build_advisory_slack_summary
        lines.append(build_advisory_slack_summary(committee_advisory))

    # Phase 3: Review decision section
    review_enabled = slack_review_cfg.enabled if slack_review_cfg is not None else True
    if review_enabled:
        gate_status = pre_trade_gate.overall_status if pre_trade_gate is not None else None
        gate_failures = (
            [c.check_id for c in pre_trade_gate.checks if c.status in ("FAIL", "REVIEW_REQUIRED") and c.severity != "INFO"]
            if pre_trade_gate is not None else []
        )
        lines.append(build_review_decision_section(gate_status, gate_failures))

    return "\n".join(lines)
