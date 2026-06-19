"""Phase 5.5: Signal Review Due / Alert Threshold detection.

Classifies watchlist items by their next_review_date relative to a reference date:
- OVERDUE:      next_review_date < today
- DUE_TODAY:    next_review_date == today
- DUE_SOON:     today < next_review_date <= today + 3 days
- NO_DATE:      next_review_date is missing or empty
- LOCKED:       USER_APPROVED / USER_REJECTED with future date (human-set, AI cannot change)
- ON_SCHEDULE:  next_review_date > today + 3 days

Safety invariants (always enforced):
- watchlist.csv is read-only in this module; never modified
- signal_history.csv never modified
- ai_sleeve_state.csv never modified
- etf_master.csv never modified
- No order quantity calculation
- No auto-trade
- No brokerage integration
- USER_APPROVED / USER_REJECTED status is NEVER changed by AI
- Overdue detection is informational only; no automatic status updates
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from enum import Enum
from pathlib import Path

from src.logger import logger
from src.signals.watchlist_store import DEFAULT_WATCHLIST_PATH, load_watchlist

HUMAN_LOCKED_STATUSES = frozenset({"USER_APPROVED", "USER_REJECTED"})

DUE_SOON_DAYS = 3  # "due_soon" window: within N days of today

FORBIDDEN_WORDS = ["買え", "売れ", "注文実行", "購入実行", "売却実行", "自動売買実行"]


class DueCategory(str, Enum):
    OVERDUE = "overdue"
    DUE_TODAY = "due_today"
    DUE_SOON = "due_soon"
    NO_DATE = "no_date"
    LOCKED = "locked"
    ON_SCHEDULE = "on_schedule"


# Display order (lower number = shown first)
_DUE_PRIORITY: dict[DueCategory, int] = {
    DueCategory.OVERDUE: 0,
    DueCategory.DUE_TODAY: 1,
    DueCategory.DUE_SOON: 2,
    DueCategory.NO_DATE: 3,
    DueCategory.LOCKED: 4,
    DueCategory.ON_SCHEDULE: 5,
}

_SECTION_TITLES: dict[DueCategory, str] = {
    DueCategory.OVERDUE: "期限超過（overdue）",
    DueCategory.DUE_TODAY: "本日確認（due_today）",
    DueCategory.DUE_SOON: "近日確認（due_soon）",
    DueCategory.NO_DATE: "確認日未設定（no_date）",
    DueCategory.LOCKED: "人間判断済み locked",
    DueCategory.ON_SCHEDULE: "スケジュール通り（on_schedule）",
}

# Recommendation text per (status, due_category)
_RECOMMENDATIONS: dict[tuple[str, DueCategory], str] = {
    ("HIGH_PRIORITY_CANDIDATE", DueCategory.OVERDUE): "人間確認または再レビュー推奨",
    ("HIGH_PRIORITY_CANDIDATE", DueCategory.DUE_TODAY): "本日中に人間確認推奨",
    ("HIGH_PRIORITY_CANDIDATE", DueCategory.DUE_SOON): "近日中に確認を推奨",
    ("BUY_CANDIDATE", DueCategory.OVERDUE): "人間確認または再レビュー推奨",
    ("BUY_CANDIDATE", DueCategory.DUE_TODAY): "本日中に人間確認推奨",
    ("BUY_CANDIDATE", DueCategory.DUE_SOON): "近日中に確認を推奨",
    ("WATCH", DueCategory.OVERDUE): "継続監視または再評価",
    ("WATCH", DueCategory.DUE_TODAY): "本日確認を推奨",
    ("WATCH", DueCategory.DUE_SOON): "近日中に確認を推奨",
    ("HOLD_OFF", DueCategory.OVERDUE): "継続監視または再評価",
    ("HOLD_OFF", DueCategory.DUE_TODAY): "本日確認を推奨",
    ("HOLD_OFF", DueCategory.DUE_SOON): "近日中に確認を推奨",
    ("REJECT_FOR_NOW", DueCategory.OVERDUE): "必要なら再評価",
    ("REJECT_FOR_NOW", DueCategory.DUE_TODAY): "必要なら再評価",
    ("USER_APPROVED", DueCategory.OVERDUE): "人間設定済み（AI変更不可）次回確認日超過",
    ("USER_APPROVED", DueCategory.DUE_TODAY): "人間設定済み（AI変更不可）本日確認日",
    ("USER_APPROVED", DueCategory.DUE_SOON): "人間設定済み（AI変更不可）近日確認予定",
    ("USER_APPROVED", DueCategory.LOCKED): "候補として確認済み（AI変更不可）",
    ("USER_REJECTED", DueCategory.OVERDUE): "人間が却下済み（AI変更不可）",
    ("USER_REJECTED", DueCategory.DUE_TODAY): "人間が却下済み（AI変更不可）",
    ("USER_REJECTED", DueCategory.LOCKED): "人間が却下済み（AI変更不可）",
}

_DEFAULT_RECOMMENDATION = "確認を推奨"


@dataclass
class ReviewDueItem:
    """One watchlist item classified by its review due status.

    Invariants:
    - no_order_quantity is always True
    - no_auto_trade is always True
    - is_locked True means USER_APPROVED or USER_REJECTED (AI cannot change)
    - This is informational only; no status updates are made
    """
    ticker: str
    status: str
    due_category: DueCategory
    next_review_date: str
    confidence: str
    reason_summary: str
    recommendation: str
    is_locked: bool
    no_order_quantity: bool = True
    no_auto_trade: bool = True


def classify_item(row: dict, as_of_date: date | None = None) -> ReviewDueItem:
    """Classify one watchlist row by its review due status.

    USER_APPROVED / USER_REJECTED are always locked (AI cannot change them),
    but are still classified as OVERDUE / DUE_TODAY when their date has passed
    so humans are notified to re-confirm.

    No files are written. No status changes.
    """
    today = as_of_date or date.today()
    ticker = row.get("ticker", "")
    status = row.get("status", "")
    nrd_str = (row.get("next_review_date", "") or "").strip()
    confidence = row.get("confidence", "")
    reason_summary = (row.get("reason_summary", "") or "")[:60]
    is_locked = status in HUMAN_LOCKED_STATUSES

    nrd: date | None = None
    if nrd_str:
        try:
            nrd = date.fromisoformat(nrd_str)
        except ValueError:
            logger.warning(f"ReviewDue: invalid next_review_date for {ticker}: {nrd_str!r}")

    # Classify by date (locked items still show overdue/due_today for human notification)
    if nrd is None:
        due_cat = DueCategory.NO_DATE if not is_locked else DueCategory.LOCKED
    elif nrd < today:
        due_cat = DueCategory.OVERDUE
    elif nrd == today:
        due_cat = DueCategory.DUE_TODAY
    elif nrd <= today + timedelta(days=DUE_SOON_DAYS):
        due_cat = DueCategory.DUE_SOON
    elif is_locked:
        due_cat = DueCategory.LOCKED
    else:
        due_cat = DueCategory.ON_SCHEDULE

    recommendation = _RECOMMENDATIONS.get((status, due_cat), _DEFAULT_RECOMMENDATION)

    return ReviewDueItem(
        ticker=ticker,
        status=status,
        due_category=due_cat,
        next_review_date=nrd_str,
        confidence=confidence,
        reason_summary=reason_summary,
        recommendation=recommendation,
        is_locked=is_locked,
    )


def classify_watchlist(
    rows: list[dict],
    as_of_date: date | None = None,
) -> list[ReviewDueItem]:
    """Classify all watchlist rows and sort by priority (overdue first)."""
    classified = [classify_item(row, as_of_date) for row in rows]
    return sorted(classified, key=lambda i: _DUE_PRIORITY.get(i.due_category, 99))


def load_and_classify(
    watchlist_path: str | Path = DEFAULT_WATCHLIST_PATH,
    as_of_date: date | None = None,
    due_categories: list[DueCategory] | None = None,
) -> list[ReviewDueItem]:
    """Load watchlist.csv (read-only) and classify items by due status.

    Args:
        watchlist_path: path to watchlist.csv. Never modified.
        as_of_date: reference date (default: today).
        due_categories: if given, return only items matching these categories.

    Safety: never modifies watchlist / signal_history / ai_sleeve_state / etf_master.
    No order quantity. No auto-trade. No brokerage.
    """
    rows = load_watchlist(watchlist_path)
    classified = classify_watchlist(rows, as_of_date)
    if due_categories is not None:
        cat_set = set(due_categories)
        classified = [i for i in classified if i.due_category in cat_set]
    return classified


def build_due_markdown(
    items: list[ReviewDueItem],
    as_of_date: date | None = None,
    title: str = "Signal Review Due",
) -> str:
    """Build a Markdown report of review-due items for CLI display.

    Groups items by DueCategory (overdue first).
    No order execution language. No order quantity. No auto-trade.
    """
    today = (as_of_date or date.today()).isoformat()
    lines: list[str] = []
    lines.append(f"# {title} — {today}")
    lines.append("")
    lines.append(
        "> 助言専用：確認期限の通知のみです。"
        "実注文・注文数量計算・自動売買・証券口座連携は行いません。"
        "USER_APPROVED/USER_REJECTEDはAI変更不可。最終判断は人間が行います。"
    )
    lines.append("")

    if not items:
        lines.append("（確認対象のシグナルはありません）")
        lines.append("")
        _append_due_safety(lines)
        return "\n".join(lines)

    # Group by category in priority order
    grouped: dict[DueCategory, list[ReviewDueItem]] = {}
    for item in items:
        grouped.setdefault(item.due_category, []).append(item)

    for cat in DueCategory:
        cat_items = grouped.get(cat, [])
        if not cat_items:
            continue
        section_title = _SECTION_TITLES.get(cat, str(cat.value))
        lines.append(f"## {section_title} ({len(cat_items)}件)")
        lines.append("")
        lines.append("| symbol | status | confidence | next_review_date | recommendation |")
        lines.append("|---|---|---|---|---|")
        for item in cat_items:
            lock_mark = " [locked]" if item.is_locked else ""
            nrd_display = item.next_review_date or "(未設定)"
            lines.append(
                f"| {item.ticker} | {item.status}{lock_mark} | {item.confidence}"
                f" | {nrd_display} | {item.recommendation} |"
            )
        lines.append("")

    lines.append(f"合計: {len(items)}件")
    lines.append("")
    _append_due_safety(lines)

    text = "\n".join(lines)
    _check_forbidden(text)
    return text


def _append_due_safety(lines: list[str]) -> None:
    lines.append("## 安全注記")
    lines.append("")
    lines.append(
        "- 確認期限超過の通知は情報提供のみです。"
        "自動でUSERステータスへの変更は行いません。"
    )
    lines.append("- 注文数量は計算しません。自動売買は行いません。証券口座との連携はありません。")
    lines.append("- USER_APPROVED / USER_REJECTED はAIが変更できません（人間専用）。")
    lines.append(
        "- watchlist.csv / signal_history.csv / ai_sleeve_state.csv"
        " / etf_master.csv は変更しません。"
    )
    lines.append("")


def _check_forbidden(text: str) -> None:
    for word in FORBIDDEN_WORDS:
        if word in text:
            logger.warning(f"ReviewDue text contains forbidden word: '{word}'")
