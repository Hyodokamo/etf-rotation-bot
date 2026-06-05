"""Phase 4.1: Slack Interactivity — action router.

Routes a button press (action_id + value + user_id) to an append-only human
decision record, with: user allowlist, unknown/malformed rejection, idempotency
(no double-record of the same action+user+target), and a stability block on
`candidate_small_test_candidate` for UNSTABLE / HUMAN_REVIEW_REQUIRED /
DO_NOT_ACT_YET candidates.

Records decisions only. Never changes allocation, computes quantities, or trades.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta

from src.logger import logger
from src.slack_actions import (
    BLOCK_SMALL_TEST_HANDLING,
    BLOCK_SMALL_TEST_STABILITY,
    DEFAULT_SLACK_LOG_PATH,
    SLACK_DECISION_SCHEMA_VERSION,
    SOURCE_CANDIDATE,
    SOURCE_MONTHLY,
    append_slack_decision_log,
    human_decision_for,
    is_known_action,
    is_note_action,
    parse_action_value,
    read_slack_decision_log,
    source_for_action,
)
from src.committee.candidate_decision_logger import (
    DEFAULT_CANDIDATE_LOG_PATH,
    append_candidate_decision_log,
    read_candidate_decision_log,
)

_JST = timezone(timedelta(hours=9))


@dataclass
class ActionResult:
    ok: bool
    message: str
    recorded: bool = False
    blocked: bool = False
    duplicate: bool = False
    source_type: str | None = None
    human_decision: str | None = None
    candidate_symbol: str | None = None
    log_path: str | None = None

    def to_dict(self) -> dict:
        return {
            "ok": self.ok, "message": self.message, "recorded": self.recorded,
            "blocked": self.blocked, "duplicate": self.duplicate,
            "source_type": self.source_type, "human_decision": self.human_decision,
            "candidate_symbol": self.candidate_symbol,
        }


def _identifier(source_type: str, data: dict) -> str | None:
    if source_type == SOURCE_MONTHLY:
        return data.get("run_id")
    return data.get("review_id") or data.get("candidate_symbol")


def _already_recorded(log_path, action_id, user_id, identifier) -> bool:
    for e in read_slack_decision_log(log_path):
        if (e.get("action_id") == action_id and e.get("user_id") == user_id
                and e.get("target_id") == identifier):
            return True
    return False


def route_action(
    action_id: str,
    value: str,
    user_id: str,
    *,
    allowed_users: list[str] | None = None,
    log_path=DEFAULT_SLACK_LOG_PATH,
    now: str | None = None,
) -> ActionResult:
    """Validate, authorize, and record a Slack button press (append-only)."""
    now = now or datetime.now(_JST).isoformat()

    # 1) user allowlist (None => no restriction configured)
    if allowed_users is not None and user_id not in allowed_users:
        logger.warning(f"Slack action from unallowed user: {user_id}")
        return ActionResult(ok=False, message="この操作は許可されていません。")

    # 2) known action
    if not is_known_action(action_id):
        return ActionResult(ok=False, message="不明なアクションです。")

    # 3) parse value
    try:
        data = parse_action_value(value)
    except ValueError:
        return ActionResult(ok=False, message="ボタンデータが不正です。")

    source_type = source_for_action(action_id)
    symbol = data.get("candidate_symbol")

    # 4) note action: acknowledge (modal text capture is a later phase)
    if is_note_action(action_id):
        return ActionResult(
            ok=True, recorded=False, source_type=source_type, candidate_symbol=symbol,
            message="メモ追加はモーダル入力（次フェーズ）で対応します。",
        )

    human_decision = human_decision_for(action_id)

    # 5) stability block for small-amount consideration candidate
    if action_id == "candidate_small_test_candidate":
        stability = data.get("candidate_stability")
        handling = data.get("recommended_handling")
        if stability in BLOCK_SMALL_TEST_STABILITY or handling in BLOCK_SMALL_TEST_HANDLING:
            reason = stability if stability in BLOCK_SMALL_TEST_STABILITY else handling
            return ActionResult(
                ok=True, blocked=True, recorded=False, source_type=source_type,
                candidate_symbol=symbol, human_decision=None,
                message=(
                    f"この候補は{reason}のため、小額検討候補にはできません。"
                    "再レビューまたは様子見を選択してください。"
                ),
            )

    identifier = _identifier(source_type, data)

    # 6) idempotency
    if _already_recorded(log_path, action_id, user_id, identifier):
        return ActionResult(
            ok=True, recorded=False, duplicate=True, source_type=source_type,
            candidate_symbol=symbol, human_decision=human_decision,
            message="既に記録済みです（重複押下）。",
        )

    # 7) append-only record (decision only; no allocation / quantity / trade)
    entry = {
        "schema_version": SLACK_DECISION_SCHEMA_VERSION,
        "timestamp": now,
        "source_type": source_type,
        "action_id": action_id,
        "human_decision": human_decision,
        "user_id": user_id,
        "target_id": identifier,
        "run_id": data.get("run_id"),
        "review_id": data.get("review_id"),
        "candidate_symbol": symbol,
        "candidate_stability": data.get("candidate_stability"),
        "recommended_handling": data.get("recommended_handling"),
        "value_generated_at": data.get("generated_at"),
        "allocation_override": False,
        "auto_trade": False,
        "order_generated": False,
    }
    saved = append_slack_decision_log(entry, log_path)

    if source_type == SOURCE_MONTHLY:
        msg_map = {
            "REVIEW_CONFIRMED": "記録しました: 月次レビューを確認済みにしました。",
            "SKIP_THIS_MONTH": "記録しました: 今月は見送りとして保存しました。",
            "REQUEST_RERUN": "記録しました: 再レビュー要望を保存しました。",
        }
        message = msg_map.get(human_decision, f"記録しました: {human_decision}")
    else:
        from src.slack_actions import CANDIDATE_DECISION_LABELS
        label = CANDIDATE_DECISION_LABELS.get(human_decision, human_decision)
        message = f"記録しました: {symbol} を{label}として保存しました。"

    return ActionResult(
        ok=True, recorded=True, source_type=source_type,
        human_decision=human_decision, candidate_symbol=symbol, log_path=saved,
        message=message,
    )


def _note_already_recorded(entries, action_id, user_id, target_id, note) -> bool:
    for e in entries:
        if (e.get("entry_type") == "note" and e.get("action_id") == action_id
                and e.get("user_id") == user_id and e.get("target_id") == target_id
                and e.get("human_note") == note):
            return True
    return False


def route_note_submission(
    private_metadata: str,
    human_note: str,
    user_id: str,
    *,
    allowed_users: list[str] | None = None,
    monthly_log_path=DEFAULT_SLACK_LOG_PATH,
    candidate_log_path=DEFAULT_CANDIDATE_LOG_PATH,
    now: str | None = None,
) -> ActionResult:
    """Record a note-modal submission append-only (decision context, not approval)."""
    from src.slack_modals import parse_note_private_metadata, validate_note

    now = now or datetime.now(_JST).isoformat()

    # 1) user allowlist
    if allowed_users is not None and user_id not in allowed_users:
        logger.warning(f"Slack note submission from unallowed user: {user_id}")
        return ActionResult(ok=False, message="この操作は許可されていません。")

    # 2) parse private_metadata (rejects malformed / secrets)
    try:
        meta = parse_note_private_metadata(private_metadata)
    except ValueError:
        return ActionResult(ok=False, message="メモのメタデータが不正です。")

    source_type = meta.get("source_type")
    if source_type not in (SOURCE_MONTHLY, SOURCE_CANDIDATE):
        return ActionResult(ok=False, message="source_type が不正です。")

    # 3) required identifiers
    run_id = meta.get("run_id")
    review_id = meta.get("review_id")
    symbol = meta.get("candidate_symbol")
    if source_type == SOURCE_MONTHLY and not run_id:
        return ActionResult(ok=False, message="run_id が必要です。")
    if source_type == SOURCE_CANDIDATE and not (review_id or symbol):
        return ActionResult(ok=False, message="review_id または candidate_symbol が必要です。")

    # 4) validate note (strip / non-empty / truncate to 500)
    try:
        note = validate_note(human_note)
    except ValueError:
        return ActionResult(ok=False, source_type=source_type, message="メモが空です。記録しません。")

    action_id = meta.get("action_id") or (
        "monthly_add_note" if source_type == SOURCE_MONTHLY else "candidate_add_note"
    )
    target_id = run_id if source_type == SOURCE_MONTHLY else (review_id or symbol)

    entry = {
        "schema_version": SLACK_DECISION_SCHEMA_VERSION,
        "timestamp": now,
        "entry_type": "note",
        "source_type": source_type,
        "action_id": action_id,
        "human_decision": "ADD_NOTE",
        "human_note": note,
        "user_id": user_id,
        "target_id": target_id,
        "run_id": run_id,
        "review_id": review_id,
        "candidate_symbol": symbol,
        "allocation_override": False,
        "auto_trade": False,
        "order_generated": False,
    }

    if source_type == SOURCE_MONTHLY:
        existing = read_slack_decision_log(monthly_log_path)
        if _note_already_recorded(existing, action_id, user_id, target_id, note):
            return ActionResult(ok=True, recorded=False, duplicate=True, source_type=source_type,
                                message="既に同じメモが記録済みです。")
        saved = append_slack_decision_log(entry, monthly_log_path)
        message = "メモを記録しました: 月次レビュー"
    else:
        existing = read_candidate_decision_log(candidate_log_path)
        if _note_already_recorded(existing, action_id, user_id, target_id, note):
            return ActionResult(ok=True, recorded=False, duplicate=True, source_type=source_type,
                                candidate_symbol=symbol, message="既に同じメモが記録済みです。")
        saved = append_candidate_decision_log(entry, candidate_log_path)
        message = f"メモを記録しました: {symbol or review_id}"

    return ActionResult(ok=True, recorded=True, source_type=source_type,
                        human_decision="ADD_NOTE", candidate_symbol=symbol,
                        log_path=saved, message=message)
