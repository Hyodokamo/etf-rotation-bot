import os

import requests

from src.logger import logger


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
) -> str:
    top5 = sorted(weights.items(), key=lambda x: -x[1])[:5]
    top5_str = "\n".join(f"  {t}: {w:.1%}" for t, w in top5)
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

    lines = [
        "*ETF Rotation Bot — 月次レポート*",
        f"モード: {mode}",
        f"ターンオーバー: {to_str}",
        f"Top5配分:\n{top5_str}",
    ]

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

    lines.append(f"レポート: {report_path}")
    return "\n".join(lines)
