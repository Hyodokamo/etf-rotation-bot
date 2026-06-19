"""Phase 4.2: Slack Note Modal.

Builds the "判断メモ" modal opened by the メモ追加 buttons and parses its
submission. A note is *decision context*, never a trade approval — the modal
contains no 買う/売る/注文 wording, and nothing here changes allocation, sizes
orders, or trades.
"""
from __future__ import annotations

import json

from src.slack_actions import SOURCE_CANDIDATE, SOURCE_MONTHLY

NOTE_MODAL_CALLBACK_ID = "committee_note_modal"
NOTE_BLOCK_ID = "note_block"
NOTE_ACTION_ID = "human_note_input"
MAX_NOTE_LEN = 500

_SAFE_METADATA_KEYS = {
    "source_type", "run_id", "review_id", "candidate_symbol", "action_id", "user_id",
    "channel_id", "message_ts",
}
_SENSITIVE_KEY_SUBSTRINGS = (
    "api_key", "apikey", "secret", "password", "token",
    "prompt", "raw_response", "signing", "credential",
)


def _has_sensitive_key(d: dict) -> bool:
    return any(any(s in str(k).lower() for s in _SENSITIVE_KEY_SUBSTRINGS) for k in d)


# ── private_metadata codec ───────────────────────────────────────────────────


def build_note_private_metadata(
    *,
    source_type: str,
    action_id: str | None = None,
    run_id: str | None = None,
    review_id: str | None = None,
    candidate_symbol: str | None = None,
    user_id: str | None = None,
    channel_id: str | None = None,
    message_ts: str | None = None,
) -> str:
    """Safe JSON private_metadata for the note modal (whitelisted keys only)."""
    payload = {
        "source_type": source_type,
        "action_id": action_id,
        "run_id": run_id,
        "review_id": review_id,
        "candidate_symbol": candidate_symbol,
        "user_id": user_id,
        "channel_id": channel_id,
        "message_ts": message_ts,
    }
    return json.dumps(payload, ensure_ascii=False)


def parse_note_private_metadata(metadata: str) -> dict:
    """Parse private_metadata. Raises ValueError on malformed JSON / non-dict / secrets."""
    try:
        data = json.loads(metadata)
    except (json.JSONDecodeError, TypeError) as e:
        raise ValueError(f"malformed private_metadata: {e}") from e
    if not isinstance(data, dict):
        raise ValueError("private_metadata must be a JSON object")
    if _has_sensitive_key(data):
        raise ValueError("private_metadata contains a forbidden/sensitive key")
    return data


# ── note validation / extraction ─────────────────────────────────────────────


def validate_note(note: str | None) -> str:
    """Strip, reject empty, and truncate to MAX_NOTE_LEN. Raises ValueError if empty."""
    text = (note or "").strip()
    if not text:
        raise ValueError("note is empty")
    return text[:MAX_NOTE_LEN]


def extract_human_note(view: dict) -> str:
    """Pull the raw note text from a view_submission payload (stripped)."""
    values = (view.get("state") or {}).get("values") or {}
    block = values.get(NOTE_BLOCK_ID) or {}
    field = block.get(NOTE_ACTION_ID) or {}
    return (field.get("value") or "").strip()


# ── modal view builder ───────────────────────────────────────────────────────


def build_note_modal_view(
    *,
    source_type: str,
    action_id: str | None = None,
    run_id: str | None = None,
    review_id: str | None = None,
    candidate_symbol: str | None = None,
    user_id: str | None = None,
    channel_id: str | None = None,
    message_ts: str | None = None,
) -> dict:
    """Build the Slack modal view payload for adding a decision note.

    Validates the required identifiers per source_type and raises ValueError on
    missing/invalid fields. Reused for monthly and candidate notes.
    """
    if source_type not in (SOURCE_MONTHLY, SOURCE_CANDIDATE):
        raise ValueError("source_type is required and must be monthly_review or candidate_review")
    if source_type == SOURCE_MONTHLY and not run_id:
        raise ValueError("run_id is required for monthly_review notes")
    if source_type == SOURCE_CANDIDATE and not (review_id or candidate_symbol):
        raise ValueError("review_id or candidate_symbol is required for candidate_review notes")

    private_metadata = build_note_private_metadata(
        source_type=source_type, action_id=action_id, run_id=run_id,
        review_id=review_id, candidate_symbol=candidate_symbol, user_id=user_id,
        channel_id=channel_id, message_ts=message_ts,
    )

    return {
        "type": "modal",
        "callback_id": NOTE_MODAL_CALLBACK_ID,
        "private_metadata": private_metadata,
        "title": {"type": "plain_text", "text": "判断メモ"},
        "submit": {"type": "plain_text", "text": "記録"},
        "close": {"type": "plain_text", "text": "キャンセル"},
        "blocks": [
            {
                "type": "input",
                "block_id": NOTE_BLOCK_ID,
                "label": {"type": "plain_text", "text": "判断メモ（判断理由・確認したい点）"},
                "element": {
                    "type": "plain_text_input",
                    "action_id": NOTE_ACTION_ID,
                    "multiline": True,
                    "max_length": MAX_NOTE_LEN,
                    "placeholder": {
                        "type": "plain_text",
                        "text": "判断理由や確認したい点を入力してください",
                    },
                },
            },
            {
                "type": "context",
                "elements": [{
                    "type": "mrkdwn",
                    "text": "メモは*判断の補足記録*です（売買承認ではありません）。自動売買は行いません。",
                }],
            },
        ],
    }


def build_note_modal_from_action_value(value: str, action_id: str, user_id: str | None = None) -> dict:
    """Build a note modal view from a メモ追加 button's safe JSON value."""
    from src.slack_actions import parse_action_value, source_for_action

    data = parse_action_value(value)
    source_type = data.get("source_type") or source_for_action(action_id)
    return build_note_modal_view(
        source_type=source_type,
        action_id=action_id,
        run_id=data.get("run_id"),
        review_id=data.get("review_id"),
        candidate_symbol=data.get("candidate_symbol"),
        user_id=user_id,
        channel_id=data.get("channel_id"),
        message_ts=data.get("message_ts"),
    )
