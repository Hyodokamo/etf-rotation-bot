"""Phase 7.2: Slack Signal Human Decision Actions.

Builds interactive Block Kit buttons for /etf review and routes signal
human decision button presses (USER_APPROVED / USER_WATCH / USER_HOLD_OFF /
USER_REJECTED / USER_REQUEST_RERUN / note) to record_human_signal_decision().

Safety invariants:
- No order quantity, no auto-trade, no brokerage, no NISA automation
- signal_history.csv never modified (human decisions → signal_human_decision_log.jsonl)
- ai_sleeve_state.csv / etf_master.csv never modified
- USER_APPROVED = 候補として確認済み (NOT a buy order, NOT 買付承認)
- Idempotency: same user+symbol+decision not double-recorded
  (tracked in logs/slack_signal_idempotency.jsonl separately from the official log)
- No shell/subprocess/eval/exec
- No secrets in button values or modal metadata
- core_manual/satellite_legacy sell proposals: never
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path

from src.logger import logger

SOURCE_SIGNAL = "signal_review"

_JST = timezone(timedelta(hours=9))

SIGNAL_NOTE_MODAL_CALLBACK_ID = "signal_note_modal"
SIGNAL_NOTE_BLOCK_ID = "signal_note_block"
SIGNAL_NOTE_INPUT_ACTION_ID = "signal_note_input"
MAX_SIGNAL_NOTE_LEN = 500

DEFAULT_SIGNAL_IDEMPOTENCY_LOG = "logs/slack_signal_idempotency.jsonl"

# Decision label mapping — advisory framing, no order/buy language
SIGNAL_DECISION_LABELS: dict[str, str] = {
    "USER_APPROVED":      "候補として確認済み",  # NOT 買付承認
    "USER_WATCH":         "Watch継続",
    "USER_HOLD_OFF":      "Hold Off",
    "USER_REJECTED":      "却下",
    "USER_REQUEST_RERUN": "再評価依頼",
    "USER_NOTE_ONLY":     "メモのみ",
}

# action_id → human decision (None = opens note modal)
SIGNAL_ACTIONS: dict[str, str | None] = {
    "signal_approved":      "USER_APPROVED",
    "signal_watch":         "USER_WATCH",
    "signal_hold_off":      "USER_HOLD_OFF",
    "signal_rejected":      "USER_REJECTED",
    "signal_request_rerun": "USER_REQUEST_RERUN",
    "signal_add_note":      None,
}

SIGNAL_NOTE_ACTION = "signal_add_note"

# (action_id, button label, style)
_SIGNAL_BUTTONS: list[tuple[str, str, str]] = [
    ("signal_approved",      "候補として確認済み", "primary"),
    ("signal_watch",         "Watch継続",         ""),
    ("signal_hold_off",      "Hold Off",          ""),
    ("signal_rejected",      "却下",               "danger"),
    ("signal_request_rerun", "再評価依頼",          ""),
    ("signal_add_note",      "メモ追加",           ""),
]

_SENSITIVE_SUBSTRINGS = (
    "api_key", "apikey", "secret", "password", "token",
    "prompt", "raw_response", "signing", "credential",
)

_FORBIDDEN_WORDS = [
    "買え", "売れ", "注文実行", "購入実行", "売却実行", "自動売買実行", "買付承認",
]

_SAFETY_NOTICE = (
    "自動売買なし / 注文数量計算なし / 証券口座連携なし。最終判断は人間が行います。"
)

_STATUS_DISPLAY: dict[str, str] = {
    "BUY_CANDIDATE":           "買い候補条件を満たした",
    "HIGH_PRIORITY_CANDIDATE": "優先確認候補",
    "USER_APPROVED":           "候補として確認済み",
    "USER_REJECTED":           "人間が却下済み",
    "WATCH":                   "WATCH監視中",
    "HOLD_OFF":                "様子見（HOLD_OFF）",
    "REJECT_FOR_NOW":          "除外（REJECT）",
    "NO_ACTION":               "シグナルなし",
}


# ── Result type ───────────────────────────────────────────────────────────────

@dataclass
class SignalActionResult:
    ok: bool
    message: str
    recorded: bool = False
    blocked: bool = False
    duplicate: bool = False
    symbol: str | None = None
    human_decision: str | None = None
    no_order_quantity: bool = True
    no_auto_trade: bool = True

    def to_dict(self) -> dict:
        return {
            "ok": self.ok, "message": self.message, "recorded": self.recorded,
            "blocked": self.blocked, "duplicate": self.duplicate,
            "symbol": self.symbol, "human_decision": self.human_decision,
            "no_order_quantity": self.no_order_quantity, "no_auto_trade": self.no_auto_trade,
        }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now_jst_iso() -> str:
    return datetime.now(_JST).isoformat()


def _has_sensitive_key(d: dict) -> bool:
    return any(any(s in str(k).lower() for s in _SENSITIVE_SUBSTRINGS) for k in d)


# ── Action value codec ────────────────────────────────────────────────────────

def build_signal_action_value(
    *,
    symbol: str,
    channel_id: str | None = None,
    message_ts: str | None = None,
) -> str:
    """Build a safe JSON value for a signal decision button (whitelisted keys; no secrets)."""
    payload = {
        "source_type": SOURCE_SIGNAL,
        "symbol": symbol.upper(),
        "generated_at": _now_jst_iso(),
        "channel_id": channel_id,
        "message_ts": message_ts,
    }
    return json.dumps(payload, ensure_ascii=False)


def parse_signal_action_value(value: str) -> dict:
    """Parse a signal button value. Raises ValueError on malformed JSON or sensitive keys."""
    try:
        data = json.loads(value)
    except (json.JSONDecodeError, TypeError) as e:
        raise ValueError(f"malformed signal action value: {e}") from e
    if not isinstance(data, dict):
        raise ValueError("signal action value must be a JSON object")
    if _has_sensitive_key(data):
        raise ValueError("signal action value contains a forbidden/sensitive key")
    return data


# ── Block Kit builder ─────────────────────────────────────────────────────────

def _build_symbol_section(row: dict) -> dict:
    ticker = row.get("ticker", "")
    status = row.get("status", "")
    conf = row.get("confidence", "")
    nrd = row.get("next_review_date", "")
    label = _STATUS_DISPLAY.get(status, status)
    try:
        conf_pct = f"{float(conf) * 100:.0f}%"
    except (ValueError, TypeError):
        conf_pct = "N/A"
    text = f"*{ticker}* — {label} | conf={conf_pct}"
    if nrd:
        text += f" | 次回={nrd}"
    return {"type": "section", "text": {"type": "mrkdwn", "text": text}}


def _build_action_buttons(symbol: str) -> dict:
    value = build_signal_action_value(symbol=symbol)
    elements: list[dict] = []
    for action_id, label, style in _SIGNAL_BUTTONS:
        btn: dict = {
            "type": "button",
            "text": {"type": "plain_text", "text": label, "emoji": False},
            "action_id": action_id,
            "value": value,
        }
        if style in ("primary", "danger"):
            btn["style"] = style
        elements.append(btn)
    return {"type": "actions", "elements": elements}


def build_signal_review_blocks(items: list[dict]) -> list[dict]:
    """Build Block Kit blocks for /etf review — interactive buttons per symbol.

    Each watchlist symbol gets:
      - A section block with status / confidence / next_review_date
      - An actions block with 6 decision buttons
      - A divider

    The last block is a context safety notice. No order execution language.
    No secrets in button values (whitelisted keys only).
    """
    blocks: list[dict] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "Watchlist Review", "emoji": False},
        }
    ]

    if not items:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": "（レビュー対象のアイテムはありません）"},
        })
    else:
        for row in items:
            ticker = row.get("ticker", "")
            if not ticker:
                continue
            blocks.append(_build_symbol_section(row))
            blocks.append(_build_action_buttons(ticker))
            blocks.append({"type": "divider"})

    blocks.append({
        "type": "context",
        "elements": [{"type": "mrkdwn", "text": _SAFETY_NOTICE}],
    })
    return blocks


# ── Idempotency (separate slim log; does not modify the official decision log) ─

def _idempotency_key(action_id: str, user_id: str, symbol: str) -> dict:
    return {"action_id": action_id, "user_id": user_id, "symbol": symbol.upper()}


def _already_recorded_signal(
    idempotency_log: str | Path,
    action_id: str,
    user_id: str,
    symbol: str,
) -> bool:
    p = Path(idempotency_log)
    if not p.exists():
        return False
    key = _idempotency_key(action_id, user_id, symbol)
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if (
            entry.get("action_id") == key["action_id"]
            and entry.get("user_id") == key["user_id"]
            and entry.get("symbol", "").upper() == key["symbol"]
        ):
            return True
    return False


def _append_idempotency_record(
    idempotency_log: str | Path,
    action_id: str,
    user_id: str,
    symbol: str,
    human_decision: str,
) -> None:
    p = Path(idempotency_log)
    p.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "timestamp": _now_jst_iso(),
        "action_id": action_id,
        "user_id": user_id,
        "symbol": symbol.upper(),
        "human_decision": human_decision,
        "no_auto_trade": True,
        "no_order_quantity": True,
    }
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# ── Signal action router ──────────────────────────────────────────────────────

def route_signal_action(
    action_id: str,
    value: str,
    user_id: str,
    *,
    allowed_users: list[str] | None = None,
    watchlist_path: str = "data/watchlist.csv",
    log_path: str = "logs/signal_human_decision_log.jsonl",
    idempotency_log: str = DEFAULT_SIGNAL_IDEMPOTENCY_LOG,
    dry_run: bool = False,
) -> SignalActionResult:
    """Route a signal decision button press to record_human_signal_decision().

    Safety:
    - allowed_users check before any processing
    - Only known signal_* action_ids dispatched (whitelist)
    - No shell/subprocess/eval/exec
    - no_order_quantity = True always (result invariant)
    - no_auto_trade = True always (result invariant)
    - Idempotency: same action_id + user_id + symbol not double-recorded
    """
    from src.signals.signal_review import record_human_signal_decision

    # 1. User allowlist
    if allowed_users is not None and user_id not in allowed_users:
        logger.warning(f"[signal_actions] blocked user: {user_id}")
        return SignalActionResult(ok=False, message="この操作は許可されていません。")

    # 2. Known signal action
    if action_id not in SIGNAL_ACTIONS:
        return SignalActionResult(ok=False, message=f"不明なアクション: {action_id!r}")

    human_decision = SIGNAL_ACTIONS[action_id]
    if human_decision is None:
        return SignalActionResult(ok=True, recorded=False,
                                  message="メモ追加モーダルを開いてください。")

    # 3. Parse button value (no secrets allowed)
    try:
        data = parse_signal_action_value(value)
    except ValueError:
        return SignalActionResult(ok=False, message="ボタンデータが不正です。")

    symbol = (data.get("symbol") or "").upper()
    if not symbol:
        return SignalActionResult(ok=False, message="シンボルが指定されていません。")

    # 4. Idempotency (per-user + per-symbol + per-action)
    if _already_recorded_signal(idempotency_log, action_id, user_id, symbol):
        label = SIGNAL_DECISION_LABELS.get(human_decision, human_decision)
        return SignalActionResult(
            ok=True, recorded=False, duplicate=True, symbol=symbol,
            human_decision=human_decision,
            no_order_quantity=True, no_auto_trade=True,
            message=f"既に記録済みです（{symbol} / {label}）。",
        )

    # 5. Record decision (read watchlist + save with backup + append official log)
    try:
        result = record_human_signal_decision(
            symbol, human_decision, note="",
            watchlist_path=watchlist_path,
            log_path=log_path,
            dry_run=dry_run,
        )
    except ValueError as exc:
        logger.warning(f"[signal_actions] record error: {exc}")
        return SignalActionResult(
            ok=False, message=f"記録できませんでした: {exc}",
            symbol=symbol, human_decision=human_decision,
        )
    except Exception as exc:
        logger.error(f"[signal_actions] unexpected error: {type(exc).__name__}: {exc}")
        return SignalActionResult(ok=False, message="記録中にエラーが発生しました。")

    # 6. Write idempotency record (best-effort; never breaks the flow)
    try:
        _append_idempotency_record(idempotency_log, action_id, user_id, symbol, human_decision)
    except Exception as exc:
        logger.warning(f"[signal_actions] idempotency log write failed: {exc}")

    label = SIGNAL_DECISION_LABELS.get(human_decision, human_decision)
    return SignalActionResult(
        ok=True, recorded=True, symbol=symbol, human_decision=human_decision,
        no_order_quantity=result.get("no_order_quantity", True),
        no_auto_trade=result.get("no_auto_trade", True),
        message=f"記録しました: {symbol} — {label}（自動売買なし、注文数量計算なし）",
    )


# ── Signal note modal ─────────────────────────────────────────────────────────

def build_signal_note_metadata(*, symbol: str) -> str:
    """Build safe JSON private_metadata for the signal note modal (whitelisted keys only)."""
    payload = {
        "source_type": SOURCE_SIGNAL,
        "symbol": symbol.upper(),
        "action_id": SIGNAL_NOTE_ACTION,
    }
    return json.dumps(payload, ensure_ascii=False)


def parse_signal_note_metadata(metadata: str) -> dict:
    """Parse signal note modal private_metadata. Raises ValueError on error or secrets."""
    try:
        data = json.loads(metadata)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError(f"malformed signal note metadata: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("metadata must be a JSON object")
    if _has_sensitive_key(data):
        raise ValueError("metadata contains a forbidden/sensitive key")
    return data


def build_signal_note_modal_view(*, symbol: str) -> dict:
    """Build the Slack modal view for adding a decision note to a signal item."""
    private_metadata = build_signal_note_metadata(symbol=symbol)
    return {
        "type": "modal",
        "callback_id": SIGNAL_NOTE_MODAL_CALLBACK_ID,
        "private_metadata": private_metadata,
        "title": {"type": "plain_text", "text": "判断メモ"},
        "submit": {"type": "plain_text", "text": "記録"},
        "close": {"type": "plain_text", "text": "キャンセル"},
        "blocks": [
            {
                "type": "input",
                "block_id": SIGNAL_NOTE_BLOCK_ID,
                "label": {"type": "plain_text", "text": f"{symbol.upper()} — 判断メモ"},
                "element": {
                    "type": "plain_text_input",
                    "action_id": SIGNAL_NOTE_INPUT_ACTION_ID,
                    "multiline": True,
                    "max_length": MAX_SIGNAL_NOTE_LEN,
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
                    "text": (
                        "メモは*判断の補足記録*です（売買承認ではありません）。"
                        "自動売買は行いません。"
                    ),
                }],
            },
        ],
    }


def build_signal_note_modal_from_value(value: str, *, user_id: str | None = None) -> dict:
    """Build a signal note modal from a signal_add_note button value."""
    try:
        data = parse_signal_action_value(value)
    except ValueError:
        data = {}
    symbol = (data.get("symbol") or "UNKNOWN").upper()
    return build_signal_note_modal_view(symbol=symbol)


def extract_signal_note(view: dict) -> str:
    """Extract the note text from a signal note view_submission payload."""
    values = (view.get("state") or {}).get("values") or {}
    block = values.get(SIGNAL_NOTE_BLOCK_ID) or {}
    field = block.get(SIGNAL_NOTE_INPUT_ACTION_ID) or {}
    return (field.get("value") or "").strip()


def route_signal_note_submission(
    view: dict,
    user_id: str,
    *,
    allowed_users: list[str] | None = None,
    watchlist_path: str = "data/watchlist.csv",
    log_path: str = "logs/signal_human_decision_log.jsonl",
) -> SignalActionResult:
    """Record a signal note modal submission as USER_NOTE_ONLY (no status change)."""
    from src.signals.signal_review import record_human_signal_decision

    # 1. User allowlist
    if allowed_users is not None and user_id not in allowed_users:
        logger.warning(f"[signal_note] blocked user: {user_id}")
        return SignalActionResult(ok=False, message="この操作は許可されていません。")

    # 2. Parse and validate metadata (no secrets)
    try:
        meta = parse_signal_note_metadata(view.get("private_metadata", ""))
    except ValueError as exc:
        return SignalActionResult(ok=False, message=f"メモのメタデータが不正です: {exc}")

    if meta.get("source_type") != SOURCE_SIGNAL:
        return SignalActionResult(ok=False, message="source_type が不正です。")

    symbol = (meta.get("symbol") or "").upper()
    if not symbol:
        return SignalActionResult(ok=False, message="シンボルが指定されていません。")

    # 3. Extract note
    note = extract_signal_note(view)
    if not note:
        return SignalActionResult(ok=False, symbol=symbol,
                                  message="メモが空です。記録しません。")
    note = note[:MAX_SIGNAL_NOTE_LEN]

    # 4. Record (USER_NOTE_ONLY = no status change; note appended to log only)
    try:
        result = record_human_signal_decision(
            symbol, "USER_NOTE_ONLY", note=note,
            watchlist_path=watchlist_path, log_path=log_path,
        )
    except ValueError as exc:
        logger.warning(f"[signal_note] record error: {exc}")
        return SignalActionResult(ok=False, message=f"記録できませんでした: {exc}",
                                  symbol=symbol)
    except Exception as exc:
        logger.error(f"[signal_note] unexpected: {type(exc).__name__}: {exc}")
        return SignalActionResult(ok=False, message="記録中にエラーが発生しました。")

    return SignalActionResult(
        ok=True, recorded=True, symbol=symbol, human_decision="USER_NOTE_ONLY",
        no_order_quantity=result.get("no_order_quantity", True),
        no_auto_trade=result.get("no_auto_trade", True),
        message=f"メモを記録しました: {symbol}（自動売買なし、注文数量計算なし）",
    )
