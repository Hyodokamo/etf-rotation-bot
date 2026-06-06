"""Tests for Phase 5.1: watchlist_store.py."""
import csv
import pytest
from pathlib import Path

from src.signals.signal_models import FinalSignal, SignalResult, SignalSide
from src.signals.watchlist_store import (
    AI_SETTABLE_STATUS,
    HUMAN_LOCKED_STATUS,
    append_signal_history,
    load_watchlist,
    save_watchlist,
    update_watchlist_entry,
)


def _result(
    symbol="GRID",
    final_signal=FinalSignal.BUY_CANDIDATE,
    watchlist_update="BUY_CANDIDATE",
) -> SignalResult:
    return SignalResult(
        symbol=symbol,
        signal_side=SignalSide.BUY,
        final_signal=final_signal,
        watchlist_update=watchlist_update,
        confidence=0.75,
        risk_flags=["flag1"],
        recommended_action_text="テスト: Watchlist候補です。最終判断は人間が行います。",
    )


def _watchlist_row(ticker: str, status: str) -> dict:
    return {
        "ticker": ticker, "name": "", "asset_class": "", "theme": "",
        "status": status, "priority_rank": "", "signal_side": "", "final_signal": "",
        "confidence": "", "suggested_buy_zone": "", "max_watch_allocation_jpy": "",
        "reason_summary": "", "risk_flags": "", "last_reviewed_at": "",
        "next_review_date": "", "expires_at": "", "updated_by": "",
    }


# ── load / save ───────────────────────────────────────────────────────────────


def test_watchlist_load_missing_returns_empty(tmp_path):
    rows = load_watchlist(tmp_path / "no_watchlist.csv")
    assert rows == []


def test_watchlist_update_creates_backup(tmp_path):
    wl_path = tmp_path / "watchlist.csv"
    archive_dir = tmp_path / "archive"
    rows = [_watchlist_row("GRID", "WATCH")]
    # First write: no pre-existing file → no backup
    save_watchlist(rows, wl_path, dry_run=False, archive_dir=archive_dir)
    assert not list(archive_dir.glob("watchlist_*.csv")), "No backup on first write (nothing to back up)"
    # Second write: pre-existing file exists → backup created before overwrite
    save_watchlist(rows, wl_path, dry_run=False, archive_dir=archive_dir)
    backup_files = list(archive_dir.glob("watchlist_*.csv"))
    assert len(backup_files) == 1, "Backup should be created before overwrite on second write"


def test_watchlist_save_creates_header(tmp_path):
    wl_path = tmp_path / "watchlist.csv"
    rows = [_watchlist_row("GRID", "WATCH")]
    save_watchlist(rows, wl_path, dry_run=False, archive_dir=tmp_path / "archive")
    with open(wl_path, encoding="utf-8") as f:
        header = f.readline()
    assert "ticker" in header
    assert "status" in header


# ── AI-locked status ──────────────────────────────────────────────────────────


def test_watchlist_update_does_not_override_user_approved(tmp_path):
    rows = [_watchlist_row("GRID", "USER_APPROVED")]
    result = _result("GRID", FinalSignal.BUY_CANDIDATE, "BUY_CANDIDATE")
    updated = update_watchlist_entry(rows, result)
    grid_row = next(r for r in updated if r["ticker"].upper() == "GRID")
    assert grid_row["status"] == "USER_APPROVED", "AI must not overwrite USER_APPROVED"


def test_watchlist_update_does_not_override_user_rejected(tmp_path):
    rows = [_watchlist_row("GRID", "USER_REJECTED")]
    result = _result("GRID", FinalSignal.HIGH_PRIORITY_CANDIDATE, "HIGH_PRIORITY_CANDIDATE")
    updated = update_watchlist_entry(rows, result)
    grid_row = next(r for r in updated if r["ticker"].upper() == "GRID")
    assert grid_row["status"] == "USER_REJECTED", "AI must not overwrite USER_REJECTED"


def test_watchlist_update_allows_ai_settable_status(tmp_path):
    rows = [_watchlist_row("GRID", "WATCH")]
    result = _result("GRID", FinalSignal.BUY_CANDIDATE, "BUY_CANDIDATE")
    updated = update_watchlist_entry(rows, result)
    grid_row = next(r for r in updated if r["ticker"].upper() == "GRID")
    assert grid_row["status"] == "BUY_CANDIDATE"


def test_watchlist_update_inserts_new_row(tmp_path):
    rows: list[dict] = []
    result = _result("ITA", FinalSignal.WATCH, "WATCH")
    updated = update_watchlist_entry(rows, result)
    assert any(r["ticker"].upper() == "ITA" for r in updated)


def test_watchlist_update_no_change_when_watchlist_update_is_none(tmp_path):
    rows = [_watchlist_row("GRID", "WATCH")]
    result = _result("GRID", FinalSignal.HOLD_OFF, watchlist_update=None)
    updated = update_watchlist_entry(rows, result)
    # Rows should be unchanged
    assert updated == rows


# ── dry-run ───────────────────────────────────────────────────────────────────


def test_watchlist_dry_run_no_write(tmp_path):
    wl_path = tmp_path / "watchlist.csv"
    rows = [_watchlist_row("GRID", "WATCH")]
    save_watchlist(rows, wl_path, dry_run=True, archive_dir=tmp_path / "archive")
    assert not wl_path.exists(), "dry_run must not write the watchlist file"
    archive_dir = tmp_path / "archive"
    assert not archive_dir.exists() or not list(archive_dir.glob("*.csv")), "dry_run must not create backup"


def test_watchlist_dry_run_history_no_write(tmp_path):
    history_path = tmp_path / "signal_history.csv"
    result = _result()
    append_signal_history(result, history_path, dry_run=True)
    assert not history_path.exists(), "dry_run must not write signal_history.csv"


# ── signal history ────────────────────────────────────────────────────────────


def test_watchlist_append_signal_history(tmp_path):
    history_path = tmp_path / "signal_history.csv"
    result = _result("GRID", FinalSignal.BUY_CANDIDATE, "BUY_CANDIDATE")
    append_signal_history(result, history_path)
    rows = list(csv.DictReader(open(history_path, encoding="utf-8")))
    assert len(rows) == 1
    assert rows[0]["symbol"] == "GRID"
    assert rows[0]["final_signal"] == "BUY_CANDIDATE"


def test_watchlist_signal_history_is_append_only(tmp_path):
    history_path = tmp_path / "signal_history.csv"
    for sym in ("GRID", "ITA"):
        append_signal_history(_result(sym), history_path)
    rows = list(csv.DictReader(open(history_path, encoding="utf-8")))
    assert len(rows) == 2
    syms = {r["symbol"] for r in rows}
    assert syms == {"GRID", "ITA"}


# ── safety: no writes to external files ──────────────────────────────────────


def test_signal_does_not_update_ai_sleeve_state():
    import inspect
    from src.signals import watchlist_store
    src = inspect.getsource(watchlist_store)
    assert "update_ai_sleeve_state_csv" not in src


def test_signal_does_not_update_etf_master():
    import inspect
    import re
    from src.signals import watchlist_store
    from src import etf_master
    wl_src = inspect.getsource(watchlist_store)
    # watchlist_store must not import etf_master (no dependency on it at all)
    assert not re.search(r"import.*etf_master|from.*etf_master", wl_src), \
        "watchlist_store must not import etf_master"
    # etf_master module must not have a save/write API
    em_src = inspect.getsource(etf_master)
    for write_fn in (r"def save_etf", r"def write_etf"):
        assert not re.search(write_fn, em_src), \
            f"etf_master must not have write API matching: {write_fn}"


# ── status sets sanity ────────────────────────────────────────────────────────


def test_watchlist_ai_settable_and_human_locked_are_disjoint():
    assert AI_SETTABLE_STATUS.isdisjoint(HUMAN_LOCKED_STATUS)
