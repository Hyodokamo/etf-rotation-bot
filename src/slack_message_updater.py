"""Phase 4.4: Original Slack message update (audit-trail surfacing).

After a button press / note submission is recorded, optionally update the
original Slack message with a "記録済み" status block via chat.update, so that
later readers can see which human decision was recorded.

Display-only and secondary: the append-only log is authoritative. A chat.update
failure never rolls back the log. Nothing here changes allocation, sizes orders,
or trades. No 買う/売る/注文 wording.
"""
from __future__ import annotations

import os

from src.logger import logger
from src.slack_actions import (
    CANDIDATE_DECISION_LABELS,
    MONTHLY_DECISION_LABELS,
    SOURCE_MONTHLY,
)

STATUS_BLOCK_ID = "committee_record_status"


def _ts_display(timestamp: str) -> str:
    # "2026-06-05T09:12:00+09:00" -> "2026-06-05 09:12"
    return (timestamp or "").replace("T", " ")[:16]


def _decision_label(source_type: str, human_decision: str | None) -> str:
    if human_decision == "ADD_NOTE":
        return "メモ追加"
    if source_type == SOURCE_MONTHLY:
        return MONTHLY_DECISION_LABELS.get(human_decision, human_decision or "")
    return CANDIDATE_DECISION_LABELS.get(human_decision, human_decision or "")


def build_status_block(
    *,
    source_type: str,
    human_decision: str | None,
    user_id: str,
    timestamp: str,
    candidate_symbol: str | None = None,
    note_present: bool = False,
) -> dict:
    """Build the '記録済み' status section block (stable block_id for replace)."""
    target = "月次レビュー" if source_type == SOURCE_MONTHLY else (candidate_symbol or "候補")
    label = _decision_label(source_type, human_decision)
    icon = "📝" if human_decision == "ADD_NOTE" else "✅"
    text = f"{icon} 記録済み: {target} = {label} by {user_id} at {_ts_display(timestamp)}"
    if note_present and human_decision != "ADD_NOTE":
        text += "  📝 メモあり"
    return {
        "type": "section",
        "block_id": STATUS_BLOCK_ID,
        "text": {"type": "mrkdwn", "text": text},
    }


def apply_status_block(existing_blocks: list[dict] | None, status_block: dict) -> list[dict]:
    """Append the status block, or replace an existing one (by block_id).

    Preserves all other (digest/candidate) blocks unchanged.
    """
    blocks = list(existing_blocks or [])
    for i, b in enumerate(blocks):
        if b.get("block_id") == STATUS_BLOCK_ID:
            blocks[i] = status_block
            return blocks
    blocks.append(status_block)
    return blocks


def build_chat_update_payload(channel_id: str, message_ts: str, blocks: list[dict]) -> dict:
    return {"channel": channel_id, "ts": message_ts, "blocks": blocks}


def _default_updater(payload: dict) -> None:  # pragma: no cover - network
    from slack_sdk import WebClient

    client = WebClient(token=os.environ["SLACK_BOT_TOKEN"])
    client.chat_update(**payload)


def update_original_message(
    *,
    channel_id: str | None,
    message_ts: str | None,
    status_block: dict,
    existing_blocks: list[dict] | None = None,
    updater=None,
) -> dict:
    """Update the original message with the status block. Never raises.

    Noop (no clobber) when channel/ts are missing or the original blocks are not
    available to preserve. Returns a small result dict.
    """
    if not channel_id or not message_ts:
        return {"updated": False, "reason": "missing channel or ts"}
    if existing_blocks is None:
        return {"updated": False, "reason": "no existing blocks to preserve"}

    blocks = apply_status_block(existing_blocks, status_block)
    payload = build_chat_update_payload(channel_id, message_ts, blocks)
    try:
        (updater or _default_updater)(payload)
        return {"updated": True, "blocks": blocks}
    except Exception as e:  # log update is best-effort; never fail the record
        logger.warning(f"chat.update failed (record kept): {type(e).__name__}: {e}")
        return {"updated": False, "reason": f"{type(e).__name__}: {e}"}
