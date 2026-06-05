"""Phase 4.1: Slack Interactivity — action ids, value codec, decision log.

Button presses are *decision records*, never trade approvals. Nothing here
changes allocation, computes quantities, or trades. `candidate_small_test_candidate`
means "record as a small-amount *consideration* candidate" — no order, no shares.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

from src.logger import logger

SOURCE_MONTHLY = "monthly_review"
SOURCE_CANDIDATE = "candidate_review"

DEFAULT_SLACK_LOG_PATH = "logs/slack_decision_log.jsonl"
SLACK_DECISION_SCHEMA_VERSION = "1.0"

_JST = timezone(timedelta(hours=9))

# action_id -> human_decision (None => note action, no decision recorded)
MONTHLY_ACTIONS: dict[str, str | None] = {
    "monthly_review_confirmed": "REVIEW_CONFIRMED",
    "monthly_skip_this_month": "SKIP_THIS_MONTH",
    "monthly_request_rerun": "REQUEST_RERUN",
    "monthly_add_note": None,
}
CANDIDATE_ACTIONS: dict[str, str | None] = {
    "candidate_watchlist": "WATCHLIST",
    "candidate_small_test_candidate": "SMALL_TEST_BUY_CANDIDATE",
    "candidate_wait": "WAIT",
    "candidate_reject": "REJECT",
    "candidate_re_review": "RE_REVIEW",
    "candidate_add_note": None,
}

# Button labels (decision-record wording only — never 買う/売る/注文/購入).
MONTHLY_BUTTONS = [
    ("monthly_review_confirmed", "確認済み"),
    ("monthly_skip_this_month", "今月は見送り"),
    ("monthly_request_rerun", "再レビュー"),
    ("monthly_add_note", "メモ追加"),
]
CANDIDATE_BUTTONS = [
    ("candidate_watchlist", "Watchlist入り"),
    ("candidate_small_test_candidate", "小額検討候補"),
    ("candidate_wait", "様子見"),
    ("candidate_reject", "見送り"),
    ("candidate_re_review", "再レビュー"),
    ("candidate_add_note", "メモ追加"),
]

# small_test_candidate is blocked for unstable / not-yet candidates.
BLOCK_SMALL_TEST_STABILITY = {"UNSTABLE"}
BLOCK_SMALL_TEST_HANDLING = {"HUMAN_REVIEW_REQUIRED", "DO_NOT_ACT_YET"}

_SAFE_VALUE_KEYS = {
    "source_type", "run_id", "review_id", "candidate_symbol",
    "candidate_stability", "recommended_handling", "generated_at",
}
_SENSITIVE_KEY_SUBSTRINGS = (
    "api_key", "apikey", "secret", "password", "token",
    "prompt", "raw_response", "signing", "credential",
)


def _now_jst_iso() -> str:
    return datetime.now(_JST).isoformat()


def is_known_action(action_id: str) -> bool:
    return action_id in MONTHLY_ACTIONS or action_id in CANDIDATE_ACTIONS


def source_for_action(action_id: str) -> str | None:
    if action_id in MONTHLY_ACTIONS:
        return SOURCE_MONTHLY
    if action_id in CANDIDATE_ACTIONS:
        return SOURCE_CANDIDATE
    return None


def human_decision_for(action_id: str) -> str | None:
    if action_id in MONTHLY_ACTIONS:
        return MONTHLY_ACTIONS[action_id]
    return CANDIDATE_ACTIONS.get(action_id)


def is_note_action(action_id: str) -> bool:
    return action_id in ("monthly_add_note", "candidate_add_note")


def _has_sensitive_key(d: dict) -> bool:
    return any(any(s in str(k).lower() for s in _SENSITIVE_KEY_SUBSTRINGS) for k in d)


# ── value codec ──────────────────────────────────────────────────────────────


def build_action_value(
    *,
    source_type: str,
    run_id: str | None = None,
    review_id: str | None = None,
    candidate_symbol: str | None = None,
    candidate_stability: str | None = None,
    recommended_handling: str | None = None,
    generated_at: str | None = None,
) -> str:
    """Build the safe JSON value carried by a Slack button (no secrets)."""
    payload = {
        "source_type": source_type,
        "run_id": run_id,
        "review_id": review_id,
        "candidate_symbol": candidate_symbol,
        "candidate_stability": candidate_stability,
        "recommended_handling": recommended_handling,
        "generated_at": generated_at or _now_jst_iso(),
    }
    # only whitelisted keys -> inherently free of secrets/prompts/raw responses
    return json.dumps(payload, ensure_ascii=False)


def parse_action_value(value: str) -> dict:
    """Parse a button value. Raises ValueError on malformed JSON / non-dict / secrets."""
    try:
        data = json.loads(value)
    except (json.JSONDecodeError, TypeError) as e:
        raise ValueError(f"malformed action value: {e}") from e
    if not isinstance(data, dict):
        raise ValueError("action value must be a JSON object")
    if _has_sensitive_key(data):
        raise ValueError("action value contains a forbidden/sensitive key")
    return data


def extract_action(payload: dict) -> tuple[str, str, str]:
    """Extract (action_id, value, user_id) from a Slack interaction payload."""
    actions = payload.get("actions") or []
    if not actions:
        raise ValueError("payload has no actions")
    action = actions[0]
    action_id = action.get("action_id", "")
    value = action.get("value", "")
    user_id = (payload.get("user") or {}).get("id", "")
    return action_id, value, user_id


# ── append-only slack decision log ───────────────────────────────────────────


def append_slack_decision_log(entry: dict, log_path: str | Path = DEFAULT_SLACK_LOG_PATH) -> str:
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # defense in depth: drop any sensitive keys
    safe = {k: v for k, v in entry.items() if not any(s in str(k).lower() for s in _SENSITIVE_KEY_SUBSTRINGS)}
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(safe, ensure_ascii=False) + "\n")
    logger.info(f"Slack decision log appended to {path} (action={safe.get('action_id')}, user={safe.get('user_id')})")
    return str(path)


def read_slack_decision_log(log_path: str | Path = DEFAULT_SLACK_LOG_PATH) -> list[dict]:
    path = Path(log_path)
    if not path.exists():
        return []
    entries: list[dict] = []
    for i, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        raw = raw.strip()
        if not raw:
            continue
        try:
            entries.append(json.loads(raw))
        except json.JSONDecodeError:
            logger.warning(f"Skipping corrupt slack decision log line {i} in {path}")
            continue
    return entries
