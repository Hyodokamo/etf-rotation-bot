"""Phase 5.2: Signal Review / Human Decision Flow.

Allows humans to record decisions on AI-generated watchlist signals.

Human decisions:
- USER_APPROVED   : Confirmed as watchlist candidate (NOT a buy order)
- USER_REJECTED   : Excluded from watchlist (AI cannot overwrite)
- USER_WATCH      : Human sets status to WATCH (AI can still update)
- USER_HOLD_OFF   : Human sets status to HOLD_OFF (AI can still update)
- USER_REQUEST_RERUN: Request re-evaluation; status reset to WATCH
- USER_NOTE_ONLY  : Add note only, no status change

Safety invariants (always enforced):
- No order quantity calculation
- No auto-trade
- No brokerage integration
- NISA usage decision never automated
- ai_sleeve_state.csv / etf_master.csv / total_portfolio_snapshot.csv never modified
- signal_history.csv never modified (human decisions go to signal_human_decision_log.jsonl)
- USER_APPROVED / USER_REJECTED can only be set here (human-only, AI cannot set them)
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path

from src.logger import logger
from src.signals.watchlist_store import (
    DEFAULT_ARCHIVE_DIR,
    DEFAULT_WATCHLIST_PATH,
    WATCHLIST_COLUMNS,
    load_watchlist,
    save_watchlist,
)

DEFAULT_HUMAN_DECISION_LOG_PATH = "logs/signal_human_decision_log.jsonl"

HUMAN_DECISIONS = frozenset({
    "USER_APPROVED",
    "USER_REJECTED",
    "USER_WATCH",
    "USER_HOLD_OFF",
    "USER_REQUEST_RERUN",
    "USER_NOTE_ONLY",
})

HUMAN_LOCKED_STATUSES = frozenset({"USER_APPROVED", "USER_REJECTED"})

# Maps human decision to new watchlist status (None = no status change)
_DECISION_TO_STATUS: dict[str, str | None] = {
    "USER_APPROVED": "USER_APPROVED",
    "USER_REJECTED": "USER_REJECTED",
    "USER_WATCH": "WATCH",
    "USER_HOLD_OFF": "HOLD_OFF",
    "USER_REQUEST_RERUN": "WATCH",
    "USER_NOTE_ONLY": None,
}

# Days until next_review_date for each decision type
_REVIEW_HORIZON: dict[str, int] = {
    "USER_APPROVED": 7,
    "USER_WATCH": 7,
    "USER_HOLD_OFF": 14,
    "USER_REQUEST_RERUN": 0,   # immediate re-evaluation
    "USER_REJECTED": 30,
    "USER_NOTE_ONLY": 14,
}

FORBIDDEN_WORDS = ["買え", "売れ", "注文実行", "購入実行", "売却実行", "自動売買実行"]

# Status display order (highest priority first)
_STATUS_PRIORITY: dict[str, int] = {
    "HIGH_PRIORITY_CANDIDATE": 0,
    "BUY_CANDIDATE": 1,
    "WATCH": 2,
    "HOLD_OFF": 3,
    "REJECT_FOR_NOW": 4,
    "USER_APPROVED": 5,
    "USER_REJECTED": 6,
}


def load_watchlist_review_items(
    path: str | Path = DEFAULT_WATCHLIST_PATH,
) -> list[dict]:
    """Load all watchlist items sorted by action priority.

    USER_APPROVED / USER_REJECTED are included for display but marked as human-locked
    (AI cannot overwrite them; use this function for human review display only).
    """
    rows = load_watchlist(path)
    return sorted(rows, key=lambda r: _STATUS_PRIORITY.get(r.get("status", ""), 99))


def build_signal_review_summary(
    items: list[dict],
    as_of_date: str | None = None,
) -> str:
    """Build a Markdown review summary for CLI display.

    Uses Watchlist candidacy framing. No order execution language.
    """
    today = as_of_date or date.today().isoformat()
    lines: list[str] = []
    lines.append(f"# Signal Review — {today}")
    lines.append("")
    lines.append(
        "> 助言専用：実注文・注文数量計算・自動売買・証券口座連携は行いません。"
        "最終判断は人間が行います。"
    )
    lines.append("")

    if not items:
        lines.append("（レビュー対象のシグナルはありません）")
        lines.append("")
        _append_review_safety(lines)
        return "\n".join(lines)

    lines.append(f"## Watchlist Review Items （{len(items)}件）")
    lines.append("")
    lines.append("| symbol | status | confidence | next_review_date | reason_summary |")
    lines.append("|---|---|---|---|---|")
    for r in items:
        ticker = r.get("ticker", "")
        status = r.get("status", "")
        conf = r.get("confidence", "")
        nrd = r.get("next_review_date", "")
        reason = (r.get("reason_summary", "") or "")[:48]
        locked = " [locked]" if status in HUMAN_LOCKED_STATUSES else ""
        lines.append(f"| {ticker} | {status}{locked} | {conf} | {nrd} | {reason} |")

    lines.append("")
    lines.append("## 人間判断の種類")
    lines.append("")
    lines.append("- `USER_APPROVED`: Watchlist候補として確認済み（買付指示ではありません）")
    lines.append("- `USER_REJECTED`: 候補から除外（次回AIシグナルで再出力されません）")
    lines.append("- `USER_WATCH`: Watch継続（AI評価は引き続き行われます）")
    lines.append("- `USER_HOLD_OFF`: Hold Off — 様子見（AI評価は引き続き行われます）")
    lines.append("- `USER_REQUEST_RERUN`: 再評価要求（次回シグナルチェック対象に戻す）")
    lines.append("- `USER_NOTE_ONLY`: メモのみ記録（status変更なし）")
    lines.append("")
    lines.append("## コマンド例")
    lines.append("")
    lines.append("```")
    lines.append(
        'python main.py --signal-review --review-symbol ITA '
        '--human-signal-decision USER_APPROVED '
        '--human-signal-note "候補として確認済み"'
    )
    lines.append(
        'python main.py --signal-review --review-symbol XLU '
        '--human-signal-decision USER_WATCH '
        '--human-signal-note "金利動向をもう少し確認"'
    )
    lines.append("```")
    lines.append("")
    _append_review_safety(lines)
    return "\n".join(lines)


def _append_review_safety(lines: list[str]) -> None:
    lines.append("## 安全注記")
    lines.append("")
    lines.append(
        "- `USER_APPROVED` は「Watchlist候補として人間が確認した」記録であり、"
        "売買・発注の指示ではありません。"
    )
    lines.append("- 注文数量は計算しません。自動売買は行いません。証券口座との連携はありません。")
    lines.append("- NISA・特定口座の使用判断は自動化しません。")
    lines.append("- core_manual / satellite_legacy / 既存BOTZ/GRID の売却提案は行いません。")
    lines.append("- ai_sleeve_state.csv / etf_master.csv は変更しません。")
    lines.append("- signal_history.csv は変更しません（人間判断は signal_human_decision_log.jsonl に記録）。")
    lines.append("")


def record_human_signal_decision(
    symbol: str,
    decision: str,
    note: str = "",
    *,
    watchlist_path: str | Path = DEFAULT_WATCHLIST_PATH,
    log_path: str | Path = DEFAULT_HUMAN_DECISION_LOG_PATH,
    archive_dir: str | Path = DEFAULT_ARCHIVE_DIR,
    dry_run: bool = False,
    create_if_missing: bool = False,
    protect_human_locked: bool = False,
) -> dict:
    """Record a human decision for a watchlist symbol.

    Returns a result dict with: symbol, decision, prev_status, new_status, note,
    final_decision_by_human=True, no_order_quantity=True, no_auto_trade=True,
    sell_signal_reserved=True, created_new_entry, human_locked.

    Raises ValueError if the decision string is invalid, or if the symbol is not
    in the watchlist AND ``create_if_missing`` is False (default — preserves CLI
    behavior).

    Mobile MVP options (used by the Slack button path):
    - ``create_if_missing=True``: if the symbol is not in the watchlist, create a
      new row from the button's symbol instead of raising (a phone user can act on
      a freshly-pushed candidate even before the watchlist persists it).
    - ``protect_human_locked=True``: never overwrite an existing USER_APPROVED /
      USER_REJECTED row — the prior human decision wins (status change is skipped
      and the attempt is logged).

    Safety guarantees:
    - USER_APPROVED / USER_REJECTED can only be set here (human-only)
    - AI cannot call this function from signal engine / committee
    - Button press is a *decision record*, never a buy order / quantity / auto-trade
    - signal_history.csv is never modified
    - ai_sleeve_state.csv / etf_master.csv are never modified
    """
    symbol = symbol.upper()
    decision = decision.upper()

    if decision not in HUMAN_DECISIONS:
        raise ValueError(
            f"Invalid decision '{decision}'. "
            f"Must be one of: {sorted(HUMAN_DECISIONS)}"
        )

    rows = load_watchlist(watchlist_path)
    now_ts = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    today = date.today().isoformat()

    existing_row = next((r for r in rows if r.get("ticker", "").upper() == symbol), None)
    symbol_missing = existing_row is None
    if symbol_missing and not create_if_missing:
        raise ValueError(
            f"Symbol '{symbol}' not found in watchlist '{watchlist_path}'. "
            "Run --crash-signal-check first to add it to the watchlist."
        )

    prev_status = existing_row.get("status", "") if existing_row else ""
    new_status = _DECISION_TO_STATUS[decision]  # None for USER_NOTE_ONLY

    # Human-lock: an existing human decision is never overwritten by a different one.
    human_locked = (
        protect_human_locked
        and prev_status in HUMAN_LOCKED_STATUSES
        and new_status is not None
        and new_status != prev_status
    )

    result = {
        "as_of_date": today,
        "timestamp": now_ts,
        "symbol": symbol,
        "decision": decision,
        "prev_status": prev_status,
        "new_status": prev_status if (human_locked or new_status is None) else new_status,
        "note": note,
        "updated_by": f"human:{decision}",
        "created_new_entry": symbol_missing and not human_locked,
        "human_locked": human_locked,
        "final_decision_by_human": True,
        "no_order_quantity": True,
        "no_auto_trade": True,
        "sell_signal_reserved": True,
    }

    if human_locked:
        logger.info(
            f"Signal review: {symbol} is human-locked ('{prev_status}'); "
            f"change to {new_status} skipped (prior human decision kept)"
        )
        _append_human_decision_log(result, log_path)
        return result

    if dry_run:
        logger.info(
            f"Signal review (dry_run): {symbol} {decision} "
            f"→ {new_status or '(no status change)'}"
            + (" [new entry]" if symbol_missing else "")
        )
        _append_human_decision_log(result, log_path)
        return result

    # Apply status change to watchlist
    if new_status is not None:
        horizon = _REVIEW_HORIZON.get(decision, 14)
        next_review = (date.today() + timedelta(days=horizon)).isoformat()

        if symbol_missing:
            # Create a new row from the button's symbol (USER decision only).
            new_reason = (f"[{decision}] {note[:60]}" if note else f"[{decision}]")[:120]
            new_row = {col: "" for col in WATCHLIST_COLUMNS}
            new_row.update({
                "ticker": symbol,
                "status": new_status,
                "signal_side": "BUY",
                "last_reviewed_at": now_ts,
                "next_review_date": next_review,
                "updated_by": f"human:{decision}",
                "reason_summary": new_reason,
            })
            updated_rows = list(rows) + [new_row]
            logger.info(f"Signal review: created new watchlist entry {symbol} → {new_status}")
        else:
            updated_rows = []
            for row in rows:
                if row.get("ticker", "").upper() == symbol:
                    updated = dict(row)
                    updated_reason = row.get("reason_summary", "") or ""
                    if note:
                        updated_reason = (
                            f"[{decision}] {note[:60]} | {updated_reason}"
                        )[:120]
                    updated.update({
                        "status": new_status,
                        "last_reviewed_at": now_ts,
                        "next_review_date": next_review,
                        "updated_by": f"human:{decision}",
                        "reason_summary": updated_reason,
                    })
                    updated_rows.append(updated)
                else:
                    updated_rows.append(row)

        save_watchlist(updated_rows, watchlist_path, dry_run=False, archive_dir=archive_dir)
        logger.info(
            f"Signal review recorded: {symbol} {decision} "
            f"(prev={prev_status!r} → new={new_status!r})"
        )
    else:
        # USER_NOTE_ONLY: no watchlist write, log only (even if symbol missing)
        logger.info(
            f"Signal review: {symbol} USER_NOTE_ONLY — status='{prev_status}' unchanged, note logged"
        )

    _append_human_decision_log(result, log_path)
    return result


def _append_human_decision_log(
    entry: dict,
    path: str | Path = DEFAULT_HUMAN_DECISION_LOG_PATH,
) -> None:
    """Append one human decision to the append-only JSONL log."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    logger.info(f"Human decision log appended: {p}")
