"""Phase 5.2: Signal Review / Human Decision Flow tests.

Tests that:
- Human decisions are recorded correctly in watchlist.csv
- USER_APPROVED / USER_REJECTED cannot be overwritten by AI
- USER_NOTE_ONLY does not change status
- archive backup is created on watchlist write
- signal_human_decision_log.jsonl receives append-only entries
- signal_history.csv is never modified
- etf_master.csv / ai_sleeve_state.csv are never modified
- No order quantities, no auto-trade language anywhere
- No forbidden buy/sell/order wording
- Invalid symbol → clean ValueError (not unhandled crash)
- Invalid decision → clean ValueError
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from src.signals.signal_review import (
    HUMAN_DECISIONS,
    HUMAN_LOCKED_STATUSES,
    build_signal_review_summary,
    load_watchlist_review_items,
    record_human_signal_decision,
)
from src.signals.watchlist_store import (
    WATCHLIST_COLUMNS,
    load_watchlist,
    save_watchlist,
    update_watchlist_entry,
)
from src.signals.signal_models import FinalSignal, SignalResult, SignalSide


# ── helpers ───────────────────────────────────────────────────────────────────


def _row(ticker: str, status: str, confidence: str = "0.82",
         reason: str = "", updated_by: str = "signal_engine",
         next_review_date: str = "2026-06-13") -> dict:
    r = {col: "" for col in WATCHLIST_COLUMNS}
    r.update({
        "ticker": ticker,
        "status": status,
        "confidence": confidence,
        "reason_summary": reason,
        "updated_by": updated_by,
        "next_review_date": next_review_date,
    })
    return r


def _write_watchlist(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=WATCHLIST_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _ai_result(symbol: str, final_signal: FinalSignal) -> SignalResult:
    """Minimal SignalResult for testing AI update protection."""
    wl_update = final_signal.value if "CANDIDATE" in final_signal.value or final_signal.value == "WATCH" else None
    return SignalResult(
        symbol=symbol,
        signal_side=SignalSide.BUY,
        final_signal=final_signal,
        total_score=4,
        positive_member_count=3,
        confidence=0.85,
        trigger_labels=["QQQ急落(-3.5%)"],
        risk_flags=[],
        recommended_action_text="Watchlist候補化。最終判断は人間が行います。",
        watchlist_update=wl_update,
    )


# ── 1. load_watchlist_review_items ───────────────────────────────────────────


def test_signal_review_lists_watchlist_items(tmp_path):
    """load_watchlist_review_items returns items sorted by priority."""
    wl_path = tmp_path / "watchlist.csv"
    _write_watchlist(wl_path, [
        _row("GRID", "HOLD_OFF"),
        _row("ITA", "BUY_CANDIDATE"),
        _row("XLU", "HIGH_PRIORITY_CANDIDATE"),
        _row("CIBR", "WATCH"),
        _row("OLD", "USER_APPROVED", updated_by="human:USER_APPROVED"),
    ])

    items = load_watchlist_review_items(wl_path)

    assert len(items) == 5
    # HIGH_PRIORITY_CANDIDATE must come first
    assert items[0]["ticker"] == "XLU"
    assert items[1]["ticker"] == "ITA"
    # USER_APPROVED must come last
    assert items[-1]["ticker"] == "OLD"
    # All items present
    tickers = [r["ticker"] for r in items]
    for t in ("GRID", "ITA", "XLU", "CIBR", "OLD"):
        assert t in tickers


# ── 2. USER_APPROVED ─────────────────────────────────────────────────────────


def test_signal_review_records_user_approved(tmp_path):
    """USER_APPROVED sets status and cannot be overwritten by AI afterward."""
    wl_path = tmp_path / "watchlist.csv"
    log_path = tmp_path / "log.jsonl"
    archive_dir = tmp_path / "archive"
    _write_watchlist(wl_path, [_row("ITA", "BUY_CANDIDATE")])

    result = record_human_signal_decision(
        "ITA", "USER_APPROVED", "候補として確認済み。発注前に取扱確認する",
        watchlist_path=wl_path, log_path=log_path, archive_dir=archive_dir,
    )

    assert result["decision"] == "USER_APPROVED"
    assert result["new_status"] == "USER_APPROVED"
    assert result["prev_status"] == "BUY_CANDIDATE"
    assert result["no_order_quantity"] is True
    assert result["no_auto_trade"] is True
    assert result["sell_signal_reserved"] is True

    loaded = load_watchlist(wl_path)
    ita = next(r for r in loaded if r["ticker"] == "ITA")
    assert ita["status"] == "USER_APPROVED"
    assert "human:USER_APPROVED" in ita["updated_by"]
    assert ita["next_review_date"]  # must be set


# ── 3. USER_REJECTED ─────────────────────────────────────────────────────────


def test_signal_review_records_user_rejected(tmp_path):
    """USER_REJECTED sets status; note is included in reason_summary."""
    wl_path = tmp_path / "watchlist.csv"
    log_path = tmp_path / "log.jsonl"
    archive_dir = tmp_path / "archive"
    _write_watchlist(wl_path, [_row("XLU", "WATCH")])

    result = record_human_signal_decision(
        "XLU", "USER_REJECTED", "テーマ不一致のため除外",
        watchlist_path=wl_path, log_path=log_path, archive_dir=archive_dir,
    )

    assert result["new_status"] == "USER_REJECTED"
    loaded = load_watchlist(wl_path)
    xlu = next(r for r in loaded if r["ticker"] == "XLU")
    assert xlu["status"] == "USER_REJECTED"
    assert "USER_REJECTED" in xlu["updated_by"]
    assert "テーマ不一致" in xlu.get("reason_summary", "")


# ── 4. USER_WATCH ─────────────────────────────────────────────────────────────


def test_signal_review_records_user_watch(tmp_path):
    """USER_WATCH sets status to WATCH (AI can still update later)."""
    wl_path = tmp_path / "watchlist.csv"
    log_path = tmp_path / "log.jsonl"
    archive_dir = tmp_path / "archive"
    _write_watchlist(wl_path, [_row("PAVE", "BUY_CANDIDATE")])

    result = record_human_signal_decision(
        "PAVE", "USER_WATCH", "金利動向をもう少し確認",
        watchlist_path=wl_path, log_path=log_path, archive_dir=archive_dir,
    )

    assert result["new_status"] == "WATCH"
    loaded = load_watchlist(wl_path)
    pave = next(r for r in loaded if r["ticker"] == "PAVE")
    assert pave["status"] == "WATCH"
    # Status is AI-settable (WATCH is not locked)
    from src.signals.watchlist_store import AI_SETTABLE_STATUS
    assert "WATCH" in AI_SETTABLE_STATUS


# ── 5. USER_HOLD_OFF ─────────────────────────────────────────────────────────


def test_signal_review_records_user_hold_off(tmp_path):
    """USER_HOLD_OFF sets status to HOLD_OFF."""
    wl_path = tmp_path / "watchlist.csv"
    log_path = tmp_path / "log.jsonl"
    archive_dir = tmp_path / "archive"
    _write_watchlist(wl_path, [_row("GRID", "BUY_CANDIDATE")])

    result = record_human_signal_decision(
        "GRID", "USER_HOLD_OFF", "既存関連保有があるため追加は待つ",
        watchlist_path=wl_path, log_path=log_path, archive_dir=archive_dir,
    )

    assert result["new_status"] == "HOLD_OFF"
    loaded = load_watchlist(wl_path)
    grid = next(r for r in loaded if r["ticker"] == "GRID")
    assert grid["status"] == "HOLD_OFF"


# ── 6. USER_REQUEST_RERUN ─────────────────────────────────────────────────────


def test_signal_review_records_user_request_rerun(tmp_path):
    """USER_REQUEST_RERUN resets status to WATCH with immediate next_review_date."""
    wl_path = tmp_path / "watchlist.csv"
    log_path = tmp_path / "log.jsonl"
    archive_dir = tmp_path / "archive"
    _write_watchlist(wl_path, [_row("AVUV", "REJECT_FOR_NOW", next_review_date="2026-07-01")])

    result = record_human_signal_decision(
        "AVUV", "USER_REQUEST_RERUN", "市場条件が変わったので再評価",
        watchlist_path=wl_path, log_path=log_path, archive_dir=archive_dir,
    )

    assert result["new_status"] == "WATCH"
    loaded = load_watchlist(wl_path)
    avuv = next(r for r in loaded if r["ticker"] == "AVUV")
    assert avuv["status"] == "WATCH"
    # next_review_date should be today (immediate re-evaluation)
    from datetime import date
    assert avuv["next_review_date"] == date.today().isoformat(), \
        "USER_REQUEST_RERUN should set next_review_date to today"


# ── 7. USER_NOTE_ONLY ─────────────────────────────────────────────────────────


def test_signal_review_note_only_does_not_change_status(tmp_path):
    """USER_NOTE_ONLY records note in log without changing watchlist status."""
    wl_path = tmp_path / "watchlist.csv"
    log_path = tmp_path / "log.jsonl"
    archive_dir = tmp_path / "archive"
    _write_watchlist(wl_path, [_row("ITA", "BUY_CANDIDATE", updated_by="signal_engine")])

    result = record_human_signal_decision(
        "ITA", "USER_NOTE_ONLY", "防衛テーマは継続確認",
        watchlist_path=wl_path, log_path=log_path, archive_dir=archive_dir,
    )

    assert result["decision"] == "USER_NOTE_ONLY"
    assert result["new_status"] == "BUY_CANDIDATE"  # unchanged

    # Watchlist status must be unchanged
    loaded = load_watchlist(wl_path)
    ita = next(r for r in loaded if r["ticker"] == "ITA")
    assert ita["status"] == "BUY_CANDIDATE", "USER_NOTE_ONLY must not change status"
    assert ita["updated_by"] == "signal_engine", "updated_by must not change for NOTE_ONLY"

    # Log must contain the note
    assert log_path.exists()
    entries = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert any(e["decision"] == "USER_NOTE_ONLY" and "防衛テーマ" in e["note"] for e in entries)

    # No backup created (no watchlist write)
    backups = list(archive_dir.glob("watchlist_*.csv")) if archive_dir.exists() else []
    assert not backups, "USER_NOTE_ONLY must not create a watchlist backup"


# ── 8. backup is created on status change ────────────────────────────────────


def test_signal_review_creates_backup(tmp_path):
    """record_human_signal_decision creates archive backup when writing watchlist."""
    wl_path = tmp_path / "watchlist.csv"
    log_path = tmp_path / "log.jsonl"
    archive_dir = tmp_path / "archive"
    _write_watchlist(wl_path, [_row("ITA", "BUY_CANDIDATE")])

    record_human_signal_decision(
        "ITA", "USER_APPROVED",
        watchlist_path=wl_path, log_path=log_path, archive_dir=archive_dir,
    )

    backups = list(archive_dir.glob("watchlist_*.csv"))
    assert len(backups) == 1, f"Expected 1 backup, got {len(backups)}"
    # Backup must contain the pre-existing BUY_CANDIDATE data
    backup_content = backups[0].read_text(encoding="utf-8")
    assert "BUY_CANDIDATE" in backup_content


# ── 9. JSONL log is append-only ───────────────────────────────────────────────


def test_signal_review_appends_human_decision_log(tmp_path):
    """Human decision log is append-only JSONL (never truncated)."""
    wl_path = tmp_path / "watchlist.csv"
    log_path = tmp_path / "log.jsonl"
    archive_dir = tmp_path / "archive"
    _write_watchlist(wl_path, [
        _row("ITA", "BUY_CANDIDATE"),
        _row("XLU", "WATCH"),
        _row("GRID", "HOLD_OFF"),
    ])

    record_human_signal_decision(
        "ITA", "USER_APPROVED", "候補として確認",
        watchlist_path=wl_path, log_path=log_path, archive_dir=archive_dir,
    )
    record_human_signal_decision(
        "XLU", "USER_WATCH", "継続監視",
        watchlist_path=wl_path, log_path=log_path, archive_dir=archive_dir,
    )
    record_human_signal_decision(
        "GRID", "USER_NOTE_ONLY", "既存保有確認",
        watchlist_path=wl_path, log_path=log_path, archive_dir=archive_dir,
    )

    assert log_path.exists()
    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3, f"Expected 3 log entries, got {len(lines)}"

    entries = [json.loads(line) for line in lines]
    assert entries[0]["symbol"] == "ITA"
    assert entries[0]["decision"] == "USER_APPROVED"
    assert entries[1]["symbol"] == "XLU"
    assert entries[2]["symbol"] == "GRID"
    assert entries[2]["decision"] == "USER_NOTE_ONLY"

    # Each entry has safety flags
    for e in entries:
        assert e["no_order_quantity"] is True
        assert e["no_auto_trade"] is True
        assert e["sell_signal_reserved"] is True


# ── 10. signal_history.csv is never modified ─────────────────────────────────


def test_signal_review_does_not_change_signal_history(tmp_path):
    """record_human_signal_decision must never modify signal_history.csv."""
    import re
    import inspect
    from src.signals import signal_review as sr

    # Static check: no import of append_signal_history; no open() on signal_history
    src = inspect.getsource(sr)
    assert not re.search(r"append_signal_history", src), \
        "signal_review must not import or call append_signal_history"
    assert not re.search(r"^(import|from)\s+.*signal_history", src, re.MULTILINE), \
        "signal_review must not import signal_history modules"
    assert not re.search(r'open\s*\(.*signal_history', src), \
        "signal_review must not open signal_history.csv"

    # Functional check: signal_history unchanged after human decision
    wl_path = tmp_path / "watchlist.csv"
    history_path = tmp_path / "signal_history.csv"
    history_path.write_text(
        "as_of_date,symbol,signal_side,final_signal,total_score\n"
        "2026-06-06,ITA,BUY,BUY_CANDIDATE,4\n",
        encoding="utf-8",
    )
    original = history_path.read_text(encoding="utf-8")
    _write_watchlist(wl_path, [_row("ITA", "BUY_CANDIDATE")])

    record_human_signal_decision(
        "ITA", "USER_APPROVED",
        watchlist_path=wl_path,
        log_path=tmp_path / "log.jsonl",
        archive_dir=tmp_path / "archive",
    )

    assert history_path.read_text(encoding="utf-8") == original, \
        "signal_history.csv must be unchanged after human decision"


# ── 11. etf_master.csv is never modified ─────────────────────────────────────


def test_signal_review_does_not_update_etf_master():
    """signal_review.py must not import from or write to etf_master."""
    import re
    import inspect
    from src.signals import signal_review as sr
    src = inspect.getsource(sr)

    assert not re.search(r"^(import|from)\s+.*etf_master", src, re.MULTILINE), \
        "signal_review must not import etf_master"
    assert not re.search(r'open\s*\(.*etf_master', src), \
        "signal_review must not open etf_master.csv"


# ── 12. ai_sleeve_state.csv is never modified ────────────────────────────────


def test_signal_review_does_not_update_ai_sleeve_state():
    """signal_review.py must not import from or reference ai_sleeve_state."""
    import re
    import inspect
    from src.signals import signal_review as sr
    src = inspect.getsource(sr)

    assert not re.search(r"^(import|from)\s+.*ai_sleeve", src, re.MULTILINE), \
        "signal_review must not import ai_sleeve modules"
    assert "ai_sleeve_state.csv" not in src.replace(
        "- ai_sleeve_state.csv / etf_master.csv", ""
    ), "signal_review must not open ai_sleeve_state.csv"


# ── 13. No order quantity calculation ────────────────────────────────────────


def test_signal_review_does_not_calculate_order_quantity(tmp_path):
    """Human decision result must not contain order quantity / shares fields."""
    wl_path = tmp_path / "watchlist.csv"
    _write_watchlist(wl_path, [_row("ITA", "BUY_CANDIDATE")])

    result = record_human_signal_decision(
        "ITA", "USER_APPROVED",
        watchlist_path=wl_path,
        log_path=tmp_path / "log.jsonl",
        archive_dir=tmp_path / "archive",
    )

    for bad_key in ("quantity", "shares", "num_shares", "order_amount", "order_quantity"):
        assert bad_key not in result, f"Result must not contain key: {bad_key}"
    assert result["no_order_quantity"] is True

    # Watchlist CSV must not contain order quantity fields
    raw = (tmp_path / "watchlist.csv").read_text(encoding="utf-8")
    for bad_word in ("order_qty", "num_shares", "shares_to_buy", "order_quantity"):
        assert bad_word.lower() not in raw.lower(), \
            f"Watchlist CSV must not contain order quantity: '{bad_word}'"


# ── 14. No auto-trade language ───────────────────────────────────────────────


def test_signal_review_does_not_trigger_auto_trade(tmp_path):
    """Human decision result and log must not contain auto-trade language."""
    wl_path = tmp_path / "watchlist.csv"
    log_path = tmp_path / "log.jsonl"
    _write_watchlist(wl_path, [_row("ITA", "HIGH_PRIORITY_CANDIDATE")])

    result = record_human_signal_decision(
        "ITA", "USER_APPROVED",
        watchlist_path=wl_path, log_path=log_path,
        archive_dir=tmp_path / "archive",
    )

    assert result["no_auto_trade"] is True

    log_text = log_path.read_text(encoding="utf-8")
    for bad in ("auto_trade=true", "auto_execute", "order_placed", "自動売買実行"):
        assert bad.lower() not in log_text.lower(), \
            f"Auto-trade language '{bad}' must not appear in decision log"


# ── 15. No buy/sell/order wording in review output ───────────────────────────


def test_signal_review_no_buy_sell_order_wording(tmp_path):
    """build_signal_review_summary must not contain forbidden order execution words."""
    wl_path = tmp_path / "watchlist.csv"
    _write_watchlist(wl_path, [
        _row("ITA", "BUY_CANDIDATE", reason="Watchlist候補。最終判断は人間が行います。"),
        _row("XLU", "HIGH_PRIORITY_CANDIDATE"),
    ])

    items = load_watchlist_review_items(wl_path)
    summary = build_signal_review_summary(items)

    from src.signals.signal_review import FORBIDDEN_WORDS
    for word in FORBIDDEN_WORDS:
        assert word not in summary, \
            f"Forbidden word '{word}' found in review summary"

    # Must contain safe advisory framing
    assert "最終判断は人間" in summary
    assert "自動売買は行いません" in summary


# ── 16. CLI list mode ─────────────────────────────────────────────────────────


def test_signal_review_cli_list(tmp_path):
    """build_signal_review_summary shows all items with correct structure."""
    wl_path = tmp_path / "watchlist.csv"
    _write_watchlist(wl_path, [
        _row("ITA", "BUY_CANDIDATE", confidence="0.78", reason="ITA deep pullback candidate"),
        _row("XLU", "WATCH", confidence="0.65"),
    ])

    items = load_watchlist_review_items(wl_path)
    summary = build_signal_review_summary(items, as_of_date="2026-06-06")

    assert "Signal Review" in summary
    assert "2026-06-06" in summary
    assert "ITA" in summary
    assert "XLU" in summary
    assert "BUY_CANDIDATE" in summary
    assert "USER_APPROVED" in summary      # decision types listed
    assert "USER_NOTE_ONLY" in summary     # decision types listed
    assert "候補として確認済み" in summary   # non-order framing
    assert "安全注記" in summary


# ── 17. CLI record mode ───────────────────────────────────────────────────────


def test_signal_review_cli_record_decision(tmp_path):
    """record_human_signal_decision full round-trip (list → record → verify)."""
    wl_path = tmp_path / "watchlist.csv"
    log_path = tmp_path / "log.jsonl"
    archive_dir = tmp_path / "archive"

    # Set up with two watchlist items
    _write_watchlist(wl_path, [
        _row("ITA", "BUY_CANDIDATE"),
        _row("XLU", "WATCH"),
    ])

    # List mode (display)
    items = load_watchlist_review_items(wl_path)
    assert len(items) == 2

    # Record decision for ITA
    result = record_human_signal_decision(
        "ITA", "USER_APPROVED", "少額候補として確認済み",
        watchlist_path=wl_path, log_path=log_path, archive_dir=archive_dir,
    )
    assert result["symbol"] == "ITA"
    assert result["new_status"] == "USER_APPROVED"

    # Record decision for XLU
    result2 = record_human_signal_decision(
        "XLU", "USER_WATCH", "金利動向確認中",
        watchlist_path=wl_path, log_path=log_path, archive_dir=archive_dir,
    )
    assert result2["new_status"] == "WATCH"

    # Verify final watchlist state
    final = load_watchlist(wl_path)
    by_ticker = {r["ticker"]: r for r in final}
    assert by_ticker["ITA"]["status"] == "USER_APPROVED"
    assert by_ticker["XLU"]["status"] == "WATCH"

    # Log has 2 entries
    entries = [json.loads(line) for line in log_path.read_text().splitlines()]
    assert len(entries) == 2


# ── 18. Missing symbol raises clean error ─────────────────────────────────────


def test_signal_review_missing_symbol_safe_error(tmp_path):
    """record_human_signal_decision raises ValueError for unknown symbol."""
    wl_path = tmp_path / "watchlist.csv"
    _write_watchlist(wl_path, [_row("ITA", "BUY_CANDIDATE")])

    with pytest.raises(ValueError, match="not found"):
        record_human_signal_decision(
            "NOTEXIST", "USER_APPROVED",
            watchlist_path=wl_path,
            log_path=tmp_path / "log.jsonl",
            archive_dir=tmp_path / "archive",
        )

    # Watchlist must be unchanged after the error
    loaded = load_watchlist(wl_path)
    assert len(loaded) == 1
    assert loaded[0]["ticker"] == "ITA"
    assert loaded[0]["status"] == "BUY_CANDIDATE"


# ── 19. Invalid decision raises clean error ───────────────────────────────────


def test_signal_review_invalid_decision_rejected(tmp_path):
    """record_human_signal_decision raises ValueError for unknown decision string."""
    wl_path = tmp_path / "watchlist.csv"
    _write_watchlist(wl_path, [_row("ITA", "BUY_CANDIDATE")])

    with pytest.raises(ValueError, match="Invalid decision"):
        record_human_signal_decision(
            "ITA", "JUST_BUY_IT",
            watchlist_path=wl_path,
            log_path=tmp_path / "log.jsonl",
            archive_dir=tmp_path / "archive",
        )


# ── 20. USER_APPROVED cannot be overwritten by AI ────────────────────────────


def test_signal_review_user_approved_not_overwritten_by_ai(tmp_path):
    """After USER_APPROVED, AI signal update must not overwrite the status."""
    wl_path = tmp_path / "watchlist.csv"
    log_path = tmp_path / "log.jsonl"
    archive_dir = tmp_path / "archive"

    # Step 1: AI sets BUY_CANDIDATE
    _write_watchlist(wl_path, [_row("ITA", "BUY_CANDIDATE", updated_by="signal_engine")])

    # Step 2: Human approves
    record_human_signal_decision(
        "ITA", "USER_APPROVED", "確認済み",
        watchlist_path=wl_path, log_path=log_path, archive_dir=archive_dir,
    )

    # Step 3: AI tries to re-run signal update (e.g. score changes to WATCH)
    rows = load_watchlist(wl_path)
    ai_result = _ai_result("ITA", FinalSignal.WATCH)
    updated = update_watchlist_entry(rows, ai_result, updated_by="signal_engine")

    # Step 4: Save AI update attempt
    save_watchlist(updated, wl_path, dry_run=False, archive_dir=archive_dir)

    # ITA must still be USER_APPROVED
    final = load_watchlist(wl_path)
    ita = next(r for r in final if r["ticker"] == "ITA")
    assert ita["status"] == "USER_APPROVED", \
        f"AI must not overwrite USER_APPROVED, got: {ita['status']}"
    assert "human:USER_APPROVED" in ita["updated_by"], \
        "updated_by must remain human-set after AI update attempt"
