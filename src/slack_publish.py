"""Phase 4.3: Slack action-block production wiring + delivery.

Builds the Block Kit payloads that attach interactive buttons to the monthly
Digest and Candidate Review posts, and delivers them via Bot Token
(chat.postMessage). When no Bot Token is configured, it falls back to the
existing Incoming Webhook (text only, no buttons) with a warning.

Display + decision-record only: no allocation change, no order quantity, no
auto-trading, no brokerage. Stability gating from Phase 4.1.1 is preserved.
"""
from __future__ import annotations

import os

from src.logger import logger
from src.slack_blocks import build_candidate_action_blocks, build_monthly_action_blocks

_SECTION_LIMIT = 2900  # Slack section text hard limit is 3000


def bot_token_available() -> bool:
    return bool(os.environ.get("SLACK_BOT_TOKEN", "").strip())


def _text_sections(text: str) -> list[dict]:
    """Split long text into Slack section blocks under the 3000-char limit."""
    blocks: list[dict] = []
    remaining = text
    while remaining:
        chunk = remaining[:_SECTION_LIMIT]
        remaining = remaining[_SECTION_LIMIT:]
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": chunk}})
    return blocks


def build_monthly_digest_blocks(digest_text: str, value: str, *, interactive: bool = True) -> list[dict]:
    """Digest text as section blocks, plus monthly action buttons when interactive."""
    blocks = _text_sections(digest_text)
    if interactive:
        blocks.extend(build_monthly_action_blocks(value))
    return blocks


def build_candidate_review_blocks(
    summary_text: str,
    value: str,
    candidate_stability: str | None = None,
    recommended_handling: str | None = None,
    *,
    interactive: bool = True,
) -> list[dict]:
    """Candidate summary section, plus stability-gated candidate action buttons."""
    blocks = _text_sections(summary_text)
    if interactive:
        blocks.extend(build_candidate_action_blocks(value, candidate_stability, recommended_handling))
    return blocks


def build_ephemeral_response(message: str) -> dict:
    """Safe ephemeral confirmation payload (no secrets, decision-record wording)."""
    return {
        "response_type": "ephemeral",
        "replace_original": False,
        "text": message,
    }


def _post_via_bot(text: str, blocks: list[dict], channel: str) -> bool:  # pragma: no cover - network
    try:
        from slack_sdk import WebClient
    except ImportError:
        logger.warning("slack_sdk not installed — cannot post with buttons. Install slack_sdk.")
        return False
    try:
        client = WebClient(token=os.environ["SLACK_BOT_TOKEN"])
        client.chat_postMessage(channel=channel, text=text, blocks=blocks)
        logger.info("Slack message with action buttons sent via Bot Token.")
        return True
    except Exception as e:
        logger.error(f"Slack bot post failed: {type(e).__name__}: {e}")
        return False


def post_committee_message(text: str, blocks: list[dict] | None = None, channel: str | None = None) -> dict:
    """Deliver a committee message. Bot Token (with buttons) if available+channel,
    else Incoming Webhook (text only) with a warning when buttons were requested.

    Returns {"delivery", "buttons", "ok"} — no Slack call is mocked away here;
    the webhook path reuses the existing post_to_slack().
    """
    if blocks and bot_token_available() and channel:
        ok = _post_via_bot(text, blocks, channel)
        if ok:
            return {"delivery": "bot", "buttons": True, "ok": True}
        # bot attempt failed -> fall through to webhook (no buttons)
        logger.warning("Bot post failed — falling back to Incoming Webhook without buttons.")

    if blocks and not bot_token_available():
        logger.warning("SLACK_BOT_TOKEN 未設定のため、ボタンなしDigestをWebhookで送信します。")

    from src.slack_client import post_to_slack
    ok = post_to_slack(text)
    return {"delivery": "webhook", "buttons": False, "ok": ok}
