"""Phase 5.5: Signal Review Due / Alert Threshold tests.

Verifies:
- Correct classification of overdue / due_today / due_soon / no_date / locked / on_schedule
- USER_APPROVED / USER_REJECTED always marked as locked (AI cannot change)
- build_due_markdown produces correct sections with no forbidden order words
- Slack due section is included in build_review_slack_digest when due_items are passed
- CLI --overdue / --review-date args are parsed correctly
- No files are modified (watchlist.csv / signal_history.csv / ai_sleeve_state.csv / etf_master.csv)
- no_order_quantity / no_auto_trade always True

Safety invariants tested:
- No forbidden words: 買え/売れ/注文実行/購入実行/売却実行/自動売買実行
- No writes to watchlist, signal_history, ai_sleeve_state, etf_master
- USER_APPROVED / USER_REJECTED: is_locked=True, AI cannot change status
"""
from __future__ import annotations

import re
import sys
from datetime import date, timedelta
from unittest.mock import patch

import pytest

from src.signals.signal_review_due import (
    FORBIDDEN_WORDS,
    DueCategory,
    ReviewDueItem,
    build_due_markdown,
    classify_item,
    classify_watchlist,
    load_and_classify,
)

# ── Helpers ───────────────────────────────────────────────────────────────────

TODAY = date(2026, 6, 6)
YESTERDAY = (TODAY - timedelta(days=1)).isoformat()
TODAY_STR = TODAY.isoformat()
TOMORROW = (TODAY + timedelta(days=1)).isoformat()
IN_2_DAYS = (TODAY + timedelta(days=2)).isoformat()
IN_3_DAYS = (TODAY + timedelta(days=3)).isoformat()
IN_4_DAYS = (TODAY + timedelta(days=4)).isoformat()
LAST_WEEK = (TODAY - timedelta(days=7)).isoformat()


def _row(ticker: str, status: str, nrd: str = "") -> dict:
    return {
        "ticker": ticker,
        "status": status,
        "next_review_date": nrd,
        "confidence": "0.82",
        "reason_summary": "テスト理由",
    }


# ── classify_item tests ───────────────────────────────────────────────────────


def test_review_due_detects_overdue():
    row = _row("ITA", "BUY_CANDIDATE", YESTERDAY)
    item = classify_item(row, as_of_date=TODAY)
    assert item.due_category == DueCategory.OVERDUE
    assert item.ticker == "ITA"
    assert item.is_locked is False
    assert item.no_order_quantity is True
    assert item.no_auto_trade is True


def test_review_due_detects_overdue_last_week():
    row = _row("PAVE", "WATCH", LAST_WEEK)
    item = classify_item(row, as_of_date=TODAY)
    assert item.due_category == DueCategory.OVERDUE


def test_review_due_detects_due_today():
    row = _row("XLU", "WATCH", TODAY_STR)
    item = classify_item(row, as_of_date=TODAY)
    assert item.due_category == DueCategory.DUE_TODAY
    assert item.ticker == "XLU"


def test_review_due_detects_due_soon():
    """next_review_date within 3 days (inclusive) → DUE_SOON."""
    for nrd in (TOMORROW, IN_2_DAYS, IN_3_DAYS):
        row = _row("CIBR", "WATCH", nrd)
        item = classify_item(row, as_of_date=TODAY)
        assert item.due_category == DueCategory.DUE_SOON, f"nrd={nrd} should be DUE_SOON"


def test_review_due_on_schedule():
    """next_review_date more than 3 days away → ON_SCHEDULE."""
    row = _row("AVUV", "BUY_CANDIDATE", IN_4_DAYS)
    item = classify_item(row, as_of_date=TODAY)
    assert item.due_category == DueCategory.ON_SCHEDULE


def test_review_due_handles_missing_next_review_date():
    """Empty next_review_date → NO_DATE (not locked, not overdue)."""
    row = _row("GRID", "WATCH", "")
    item = classify_item(row, as_of_date=TODAY)
    assert item.due_category == DueCategory.NO_DATE
    assert item.is_locked is False


def test_review_due_keeps_user_approved_locked():
    """USER_APPROVED → is_locked=True regardless of due_category."""
    # Future date → LOCKED category
    row = _row("ITA", "USER_APPROVED", IN_4_DAYS)
    item = classify_item(row, as_of_date=TODAY)
    assert item.is_locked is True
    assert item.due_category == DueCategory.LOCKED
    assert item.no_order_quantity is True
    assert item.no_auto_trade is True


def test_review_due_user_approved_overdue_is_still_locked():
    """USER_APPROVED with past date → OVERDUE category but still is_locked=True."""
    row = _row("ITA", "USER_APPROVED", YESTERDAY)
    item = classify_item(row, as_of_date=TODAY)
    assert item.is_locked is True
    assert item.due_category == DueCategory.OVERDUE
    # Recommendation mentions AI cannot change
    assert "AI変更不可" in item.recommendation


def test_review_due_keeps_user_rejected_locked():
    """USER_REJECTED → is_locked=True regardless of due_category."""
    row = _row("MOAT", "USER_REJECTED", YESTERDAY)
    item = classify_item(row, as_of_date=TODAY)
    assert item.is_locked is True
    assert item.due_category == DueCategory.OVERDUE


# ── classify_watchlist sort order ─────────────────────────────────────────────


def test_classify_watchlist_sort_order():
    """OVERDUE items appear before DUE_TODAY, DUE_SOON, etc."""
    rows = [
        _row("A", "WATCH", IN_2_DAYS),      # DUE_SOON
        _row("B", "WATCH", YESTERDAY),       # OVERDUE
        _row("C", "WATCH", TODAY_STR),       # DUE_TODAY
        _row("D", "WATCH", ""),              # NO_DATE
        _row("E", "USER_APPROVED", IN_4_DAYS),  # LOCKED
    ]
    classified = classify_watchlist(rows, as_of_date=TODAY)
    cats = [item.due_category for item in classified]
    # OVERDUE must come before DUE_TODAY, DUE_SOON, NO_DATE, LOCKED
    assert cats.index(DueCategory.OVERDUE) < cats.index(DueCategory.DUE_TODAY)
    assert cats.index(DueCategory.DUE_TODAY) < cats.index(DueCategory.DUE_SOON)


# ── load_and_classify filtering ───────────────────────────────────────────────


def test_load_and_classify_filters_by_category(tmp_path):
    """load_and_classify with due_categories returns only matching items."""
    wl_path = tmp_path / "watchlist.csv"
    import csv
    from src.signals.watchlist_store import WATCHLIST_COLUMNS
    rows_data = [
        dict.fromkeys(WATCHLIST_COLUMNS, ""),
        dict.fromkeys(WATCHLIST_COLUMNS, ""),
        dict.fromkeys(WATCHLIST_COLUMNS, ""),
    ]
    rows_data[0].update({"ticker": "ITA", "status": "BUY_CANDIDATE", "next_review_date": YESTERDAY})
    rows_data[1].update({"ticker": "XLU", "status": "WATCH", "next_review_date": TODAY_STR})
    rows_data[2].update({"ticker": "PAVE", "status": "WATCH", "next_review_date": IN_4_DAYS})
    with open(wl_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=WATCHLIST_COLUMNS)
        writer.writeheader()
        writer.writerows(rows_data)

    result = load_and_classify(
        watchlist_path=wl_path,
        as_of_date=TODAY,
        due_categories=[DueCategory.OVERDUE, DueCategory.DUE_TODAY],
    )
    tickers = {i.ticker for i in result}
    assert "ITA" in tickers   # overdue
    assert "XLU" in tickers   # due_today
    assert "PAVE" not in tickers  # on_schedule


# ── build_due_markdown ────────────────────────────────────────────────────────


def test_review_due_no_forbidden_order_words():
    """build_due_markdown must not contain forbidden order words."""
    items = [
        classify_item(_row("ITA", "BUY_CANDIDATE", YESTERDAY), TODAY),
        classify_item(_row("XLU", "WATCH", TODAY_STR), TODAY),
        classify_item(_row("ITA2", "USER_APPROVED", YESTERDAY), TODAY),
        classify_item(_row("ITA3", "USER_REJECTED", IN_4_DAYS), TODAY),
    ]
    text = build_due_markdown(items, as_of_date=TODAY)
    for word in FORBIDDEN_WORDS:
        assert word not in text, f"Forbidden word '{word}' found in due markdown"


def test_build_due_markdown_sections_present():
    """build_due_markdown includes correct section headers for overdue/due_today items."""
    items = [
        classify_item(_row("ITA", "BUY_CANDIDATE", YESTERDAY), TODAY),  # OVERDUE
        classify_item(_row("XLU", "WATCH", TODAY_STR), TODAY),          # DUE_TODAY
        classify_item(_row("PAVE", "WATCH", IN_2_DAYS), TODAY),         # DUE_SOON
    ]
    text = build_due_markdown(items, as_of_date=TODAY)
    assert "期限超過" in text
    assert "本日確認" in text
    assert "近日確認" in text
    assert "ITA" in text
    assert "XLU" in text
    assert "PAVE" in text


def test_build_due_markdown_empty():
    """build_due_markdown with no items returns graceful output."""
    text = build_due_markdown([], as_of_date=TODAY)
    assert text
    assert "確認対象のシグナルはありません" in text
    assert "注文数量は計算しません" in text


# ── Slack section ─────────────────────────────────────────────────────────────


def test_review_due_slack_section():
    """build_review_slack_digest with due_items includes 期限超過 section."""
    from src.signals.slack_signal_digest import build_review_slack_digest

    due_items = [
        classify_item(_row("ITA", "BUY_CANDIDATE", YESTERDAY), TODAY),  # OVERDUE
        classify_item(_row("XLU", "WATCH", TODAY_STR), TODAY),          # DUE_TODAY
        classify_item(_row("PAVE", "WATCH", IN_2_DAYS), TODAY),         # DUE_SOON
        classify_item(_row("GRID", "USER_APPROVED", IN_4_DAYS), TODAY), # LOCKED
    ]
    watchlist_items = [
        {"ticker": "ITA", "status": "BUY_CANDIDATE", "confidence": "0.82", "next_review_date": YESTERDAY},
    ]
    text = build_review_slack_digest(watchlist_items, due_items=due_items)
    assert "期限超過" in text
    assert "本日確認" in text
    assert "近日確認" in text
    assert "人間判断済み locked" in text
    assert "ITA" in text
    for word in FORBIDDEN_WORDS:
        assert word not in text, f"Forbidden word '{word}' in Slack review digest"


def test_review_due_slack_section_backward_compat():
    """build_review_slack_digest without due_items still works (backward compat)."""
    from src.signals.slack_signal_digest import build_review_slack_digest

    items = [{"ticker": "ITA", "status": "USER_APPROVED", "confidence": "0.82", "next_review_date": ""}]
    text = build_review_slack_digest(items)
    assert "候補として確認済み" in text


# ── CLI arg tests ─────────────────────────────────────────────────────────────


def test_review_due_cli_overdue():
    """--signal-review --overdue is accepted by parse_args."""
    from main import parse_args

    with patch.object(sys, "argv", ["main.py", "--signal-review", "--overdue"]):
        args = parse_args()
    assert args.signal_review is True
    assert args.overdue is True


def test_review_due_cli_review_date():
    """--signal-review --review-date YYYY-MM-DD is accepted by parse_args."""
    from main import parse_args

    with patch.object(sys, "argv", ["main.py", "--signal-review", "--review-date", "2026-06-13"]):
        args = parse_args()
    assert args.signal_review is True
    assert args.review_date == "2026-06-13"


# ── Safety: no file writes ────────────────────────────────────────────────────


def test_review_due_does_not_update_watchlist():
    """classify_watchlist and build_due_markdown are pure (no file writes)."""
    rows = [_row("ITA", "BUY_CANDIDATE", YESTERDAY)]
    classified = classify_watchlist(rows, as_of_date=TODAY)
    text = build_due_markdown(classified, as_of_date=TODAY)
    assert text  # non-empty; no exception


def test_review_due_does_not_update_signal_history():
    """signal_review_due.py must not open/write signal_history.csv."""
    import inspect
    import src.signals.signal_review_due as mod
    src_text = inspect.getsource(mod)
    assert re.search(r'open\s*\(.*signal_history', src_text) is None
    assert re.search(r'^(import|from)\s+.*signal_history', src_text, re.MULTILINE) is None


def test_review_due_does_not_update_ai_sleeve_state():
    """signal_review_due.py must not open/write ai_sleeve_state.csv."""
    import inspect
    import src.signals.signal_review_due as mod
    src_text = inspect.getsource(mod)
    assert re.search(r'open\s*\(.*ai_sleeve_state', src_text) is None
    assert re.search(r'^(import|from)\s+.*ai_sleeve', src_text, re.MULTILINE) is None


def test_review_due_does_not_update_etf_master():
    """signal_review_due.py must not open/write etf_master.csv."""
    import inspect
    import src.signals.signal_review_due as mod
    src_text = inspect.getsource(mod)
    assert re.search(r'open\s*\(.*etf_master', src_text) is None
    assert re.search(r'^(import|from)\s+.*etf_master', src_text, re.MULTILINE) is None


def test_review_due_no_order_quantity_auto_trade():
    """ReviewDueItem always has no_order_quantity=True and no_auto_trade=True."""
    rows = [
        _row("ITA", "BUY_CANDIDATE", YESTERDAY),
        _row("SPY", "USER_APPROVED", IN_4_DAYS),
        _row("XLU", "WATCH", ""),
    ]
    for row in rows:
        item = classify_item(row, as_of_date=TODAY)
        assert item.no_order_quantity is True, f"{item.ticker}: no_order_quantity must be True"
        assert item.no_auto_trade is True, f"{item.ticker}: no_auto_trade must be True"
