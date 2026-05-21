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
) -> str:
    top5 = sorted(weights.items(), key=lambda x: -x[1])[:5]
    top5_str = "\n".join(f"  {t}: {w:.1%}" for t, w in top5)
    mode = "⚠️ リスクオフ" if risk_off else "✅ リスクオン"
    to_str = f"{turnover:.1%}" if turnover is not None else "N/A（初回）"

    return (
        f"*ETF Rotation Bot — 月次レポート*\n"
        f"モード: {mode}\n"
        f"ターンオーバー: {to_str}\n"
        f"Top5配分:\n{top5_str}\n"
        f"レポート: {report_path}"
    )
