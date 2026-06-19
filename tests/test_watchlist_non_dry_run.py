"""Phase 5.1.3: Watchlist Non-Dry-Run Safety Tests.

Verifies that:
- watchlist.csv is safely written (non-dry-run)
- archive backup is created before overwrite
- signal_history.csv is append-only
- USER_APPROVED / USER_REJECTED rows are never overwritten by AI
- etf_master.csv / ai_sleeve_state.csv / total_portfolio_snapshot.csv are never modified
- no_order_quantity / no_auto_trade invariants hold through persistence
- HIGH_PRIORITY_CANDIDATE wording contains no order execution language

Safety invariants:
- no actual brokerage / order / quantity logic
- no etf_master / ai_sleeve_state / total_portfolio_snapshot writes
- SELL side always reserved
"""
from __future__ import annotations

import csv
from pathlib import Path

import pytest

from src.signals.signal_models import FinalSignal, SignalResult, SignalSide
from src.signals.watchlist_store import (
    AI_SETTABLE_STATUS,
    HUMAN_LOCKED_STATUS,
    WATCHLIST_COLUMNS,
    append_signal_history,
    load_watchlist,
    save_watchlist,
    update_watchlist_entry,
)


# ── helpers ───────────────────────────────────────────────────────────────────


def _make_result(
    symbol: str,
    final_signal: FinalSignal,
    triggers: list[str] | None = None,
    risk_flags: list[str] | None = None,
    confidence: float = 0.82,
) -> SignalResult:
    """Construct a deterministic SignalResult for testing persistence."""
    is_candidate = "CANDIDATE" in final_signal.value
    watchlist_update = final_signal.value if final_signal.value in AI_SETTABLE_STATUS else None
    trigger_prefix = triggers[0] if triggers else "QQQ急落(-3.5%)"
    return SignalResult(
        symbol=symbol,
        signal_side=SignalSide.BUY,
        final_signal=final_signal,
        total_score=4 if is_candidate else 1,
        positive_member_count=3 if is_candidate else 1,
        confidence=confidence,
        trigger_labels=triggers or ["QQQ急落(-3.5%)"],
        risk_flags=risk_flags or ["entry_quality=excellent"],
        recommended_action_text=(
            "Watchlist候補化の条件を満たしました。最終判断は人間が行います。"
            "自動売買は行いません。注文数量は計算しません。"
        ),
        watchlist_update=watchlist_update,
    )


def _empty_watchlist_row(ticker: str, status: str, updated_by: str = "human") -> dict:
    """Create a minimal watchlist row (all required columns populated)."""
    row = {col: "" for col in WATCHLIST_COLUMNS}
    row["ticker"] = ticker
    row["status"] = status
    row["updated_by"] = updated_by
    return row


def _write_initial_watchlist(path: Path, rows: list[dict]) -> None:
    """Write an initial watchlist CSV for setup."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=WATCHLIST_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


# ── 1. Non-dry-run creates watchlist.csv ─────────────────────────────────────


def test_non_dry_run_updates_watchlist(tmp_path):
    """Non-dry-run saves signal result to watchlist.csv with all expected fields."""
    wl_path = tmp_path / "watchlist.csv"
    archive_dir = tmp_path / "archive"

    result = _make_result("ITA", FinalSignal.BUY_CANDIDATE,
                          triggers=["SPY急落(-2.5%)", "ITA急落(-5.0%)"])
    rows = update_watchlist_entry([], result, updated_by="crash_signal_committee")
    save_watchlist(rows, wl_path, dry_run=False, archive_dir=archive_dir)

    assert wl_path.exists(), "watchlist.csv must be created"
    loaded = load_watchlist(wl_path)
    assert loaded, "watchlist must contain at least one row"

    ita = next((r for r in loaded if r["ticker"] == "ITA"), None)
    assert ita is not None, "ITA must be in watchlist"
    assert ita["status"] == "BUY_CANDIDATE"
    assert ita["final_signal"] == "BUY_CANDIDATE"
    assert ita["updated_by"] == "crash_signal_committee"
    assert ita["confidence"], "confidence must be populated"
    assert ita["reason_summary"], "reason_summary must be populated"
    assert ita["risk_flags"], "risk_flags must be populated"
    assert ita["next_review_date"], "next_review_date must be set"
    assert ita["last_reviewed_at"], "last_reviewed_at must be set"

    # Trigger labels must appear in reason_summary
    assert "ITA急落" in ita["reason_summary"] or "SPY急落" in ita["reason_summary"], \
        "reason_summary should include trigger label prefix"

    assert result.no_order_quantity is True
    assert result.no_auto_trade is True


# ── 2. Backup created on overwrite ───────────────────────────────────────────


def test_non_dry_run_creates_watchlist_backup(tmp_path):
    """save_watchlist creates an archive backup when overwriting existing file."""
    wl_path = tmp_path / "watchlist.csv"
    archive_dir = tmp_path / "archive"

    result = _make_result("XLU", FinalSignal.HIGH_PRIORITY_CANDIDATE)
    rows = update_watchlist_entry([], result, updated_by="crash_signal_committee")

    # First write: no pre-existing file → no backup
    save_watchlist(rows, wl_path, dry_run=False, archive_dir=archive_dir)
    backups_after_first = list(archive_dir.glob("watchlist_*.csv")) if archive_dir.exists() else []
    assert not backups_after_first, "No backup expected on first write (no prior file)"

    # Second write: pre-existing file → backup created
    result2 = _make_result("PAVE", FinalSignal.BUY_CANDIDATE)
    rows2 = update_watchlist_entry(rows, result2, updated_by="crash_signal_committee")
    save_watchlist(rows2, wl_path, dry_run=False, archive_dir=archive_dir)

    backups = list(archive_dir.glob("watchlist_*.csv"))
    assert len(backups) == 1, f"Exactly one backup expected, got {len(backups)}"
    # Backup must contain original content (ITA/XLU from first write)
    backup_content = backups[0].read_text(encoding="utf-8")
    assert "XLU" in backup_content, "Backup must contain pre-existing data"


# ── 3. Signal history is append-only ─────────────────────────────────────────


def test_non_dry_run_appends_signal_history(tmp_path):
    """append_signal_history creates and grows history CSV append-only."""
    history_path = tmp_path / "signal_history.csv"

    result1 = _make_result("ITA", FinalSignal.BUY_CANDIDATE)
    result2 = _make_result("XLU", FinalSignal.HIGH_PRIORITY_CANDIDATE)

    append_signal_history(result1, history_path, dry_run=False)
    append_signal_history(result2, history_path, dry_run=False)

    assert history_path.exists()
    lines = history_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3, f"Expected 1 header + 2 rows, got {len(lines)}"
    assert "symbol" in lines[0], "First line must be CSV header"
    assert "ITA" in lines[1]
    assert "XLU" in lines[2]

    # Third append must NOT truncate previous data
    result3 = _make_result("PAVE", FinalSignal.WATCH)
    append_signal_history(result3, history_path, dry_run=False)
    lines = history_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 4, f"Expected 1 header + 3 rows after third append, got {len(lines)}"
    assert "ITA" in "\n".join(lines), "ITA row must still exist after third append"


# ── 4. USER_APPROVED is never overwritten ────────────────────────────────────


def test_non_dry_run_preserves_user_approved(tmp_path):
    """AI must not overwrite a USER_APPROVED watchlist entry."""
    wl_path = tmp_path / "watchlist.csv"
    archive_dir = tmp_path / "archive"

    # Write initial watchlist with ITA as USER_APPROVED
    initial = [_empty_watchlist_row("ITA", "USER_APPROVED", updated_by="human_operator")]
    _write_initial_watchlist(wl_path, initial)

    # AI signal: BUY_CANDIDATE for ITA
    result = _make_result("ITA", FinalSignal.BUY_CANDIDATE)
    rows = load_watchlist(wl_path)
    updated = update_watchlist_entry(rows, result, updated_by="crash_signal_committee")
    save_watchlist(updated, wl_path, dry_run=False, archive_dir=archive_dir)

    reloaded = load_watchlist(wl_path)
    ita = next((r for r in reloaded if r["ticker"] == "ITA"), None)
    assert ita is not None

    assert ita["status"] == "USER_APPROVED", \
        f"USER_APPROVED must not be overwritten, got: {ita['status']}"
    assert ita["updated_by"] == "human_operator", \
        "updated_by must remain the human operator, not the AI"
    assert result.no_order_quantity is True


# ── 5. USER_REJECTED is never overwritten ────────────────────────────────────


def test_non_dry_run_preserves_user_rejected(tmp_path):
    """AI must not overwrite a USER_REJECTED watchlist entry."""
    wl_path = tmp_path / "watchlist.csv"
    archive_dir = tmp_path / "archive"

    initial = [_empty_watchlist_row("CIBR", "USER_REJECTED", updated_by="human_reviewer")]
    _write_initial_watchlist(wl_path, initial)

    result = _make_result("CIBR", FinalSignal.HIGH_PRIORITY_CANDIDATE)
    rows = load_watchlist(wl_path)
    updated = update_watchlist_entry(rows, result, updated_by="crash_signal_committee")
    save_watchlist(updated, wl_path, dry_run=False, archive_dir=archive_dir)

    reloaded = load_watchlist(wl_path)
    cibr = next((r for r in reloaded if r["ticker"] == "CIBR"), None)
    assert cibr is not None

    assert cibr["status"] == "USER_REJECTED", \
        f"USER_REJECTED must not be overwritten, got: {cibr['status']}"
    assert cibr["updated_by"] == "human_reviewer"


# ── 6. etf_master.csv is never modified ──────────────────────────────────────


def test_non_dry_run_does_not_update_etf_master():
    """watchlist_store.py must not import from or write to etf_master."""
    import re
    import inspect
    from src.signals import watchlist_store as wls
    src = inspect.getsource(wls)

    # No import from etf_master (docstring mentions are OK, imports are not)
    assert not re.search(r"^(import|from)\s+.*etf_master", src, re.MULTILINE), \
        "watchlist_store must not import etf_master"
    # No open() call on etf_master.csv
    assert "etf_master.csv" not in src.replace(
        "- etf_master.csv is never modified.", ""
    ), "watchlist_store must not open etf_master.csv"

    # Confirm etf_master module has no write functions (read-only by design)
    from src import etf_master as etf_m
    etf_src = inspect.getsource(etf_m)
    for fn_name in ("def save_etf", "def write_etf", "def update_etf"):
        assert fn_name not in etf_src, \
            f"etf_master must not have a write function: {fn_name}"


# ── 7. ai_sleeve_state.csv is never modified ─────────────────────────────────


def test_non_dry_run_does_not_update_ai_sleeve_state():
    """watchlist_store.py must not import from or open ai_sleeve_state.csv."""
    import re
    import inspect
    from src.signals import watchlist_store as wls
    src = inspect.getsource(wls)

    # No import from ai_sleeve
    assert not re.search(r"^(import|from)\s+.*ai_sleeve", src, re.MULTILINE), \
        "watchlist_store must not import ai_sleeve modules"
    # No open() call on ai_sleeve_state.csv
    assert "ai_sleeve_state.csv" not in src.replace(
        "- ai_sleeve_state.csv is never modified.", ""
    ), "watchlist_store must not open ai_sleeve_state.csv"


# ── 8. total_portfolio_snapshot.csv is never modified ────────────────────────


def test_non_dry_run_does_not_update_total_portfolio_snapshot():
    """watchlist_store.py must not import from or open total_portfolio_snapshot.csv."""
    import re
    import inspect
    from src.signals import watchlist_store as wls
    src = inspect.getsource(wls)

    # No import from portfolio_context
    assert not re.search(r"^(import|from)\s+.*portfolio_context", src, re.MULTILINE), \
        "watchlist_store must not import portfolio_context"
    # No open() call on portfolio_snapshot
    assert "total_portfolio_snapshot" not in src.replace(
        "# portfolio snapshot", ""
    ), "watchlist_store must not reference total_portfolio_snapshot"


# ── 9. dry_run still writes nothing ──────────────────────────────────────────


def test_dry_run_still_no_write(tmp_path):
    """dry_run=True must write no files even with valid signal results."""
    wl_path = tmp_path / "watchlist.csv"
    history_path = tmp_path / "signal_history.csv"
    archive_dir = tmp_path / "archive"

    result = _make_result("ITA", FinalSignal.BUY_CANDIDATE)
    rows = update_watchlist_entry([], result, updated_by="crash_signal_committee")
    save_watchlist(rows, wl_path, dry_run=True, archive_dir=archive_dir)
    append_signal_history(result, history_path, dry_run=True)

    assert not wl_path.exists(), "dry_run must not write watchlist.csv"
    assert not history_path.exists(), "dry_run must not write signal_history.csv"
    assert not archive_dir.exists() or not list(archive_dir.glob("*.csv")), \
        "dry_run must not create archive backup"

    # Safety invariants hold regardless
    assert result.no_order_quantity is True
    assert result.no_auto_trade is True
    assert result.sell_signal_reserved is True


# ── 10. HIGH_PRIORITY_CANDIDATE wording is not an order instruction ───────────


def test_high_priority_wording_is_not_order_instruction():
    """HIGH_PRIORITY_CANDIDATE recommended_action_text must use Watchlist framing."""
    from src.signals.signal_engine import _RECOMMENDED_TEXT
    text = _RECOMMENDED_TEXT[FinalSignal.HIGH_PRIORITY_CANDIDATE]

    forbidden_order_words = ["買え", "売れ", "購入実行", "売却実行", "注文を出", "APIで注文", "自動売買実行"]
    for word in forbidden_order_words:
        assert word not in text, \
            f"Forbidden order instruction word '{word}' found in HIGH_PRIORITY text: {text}"

    # Must contain Watchlist candidacy framing, not order framing
    assert "Watchlist" in text or "候補" in text or "人間" in text, \
        "HIGH_PRIORITY text must mention Watchlist candidacy or human review"

    # BUY_CANDIDATE text also safe
    bc_text = _RECOMMENDED_TEXT[FinalSignal.BUY_CANDIDATE]
    for word in forbidden_order_words:
        assert word not in bc_text


# ── 11. Watchlist row has no order quantity fields ────────────────────────────


def test_watchlist_update_no_order_quantity(tmp_path):
    """Saved watchlist row must not contain order quantity / share count fields."""
    wl_path = tmp_path / "watchlist.csv"
    archive_dir = tmp_path / "archive"

    result = _make_result("ITA", FinalSignal.BUY_CANDIDATE)

    # Verify SignalResult itself has no quantity fields
    result_fields = set(SignalResult.model_fields)
    for bad_field in ("quantity", "shares", "order_quantity", "num_shares", "order_amount"):
        assert bad_field not in result_fields, f"SignalResult must not have field: {bad_field}"

    # Write to file and verify CSV content
    rows = update_watchlist_entry([], result, updated_by="crash_signal_committee")
    save_watchlist(rows, wl_path, dry_run=False, archive_dir=archive_dir)
    raw = wl_path.read_text(encoding="utf-8")

    for bad_word in ("quantity", "num_shares", "order_qty", "shares_to_buy"):
        assert bad_word.lower() not in raw.lower(), \
            f"Watchlist CSV must not contain order quantity reference: '{bad_word}'"

    assert result.no_order_quantity is True


# ── 12. no_auto_trade invariant survives persistence roundtrip ────────────────


def test_watchlist_update_no_auto_trade(tmp_path):
    """no_auto_trade=True invariant must hold through the full store roundtrip."""
    wl_path = tmp_path / "watchlist.csv"
    archive_dir = tmp_path / "archive"
    history_path = tmp_path / "signal_history.csv"

    result = _make_result(
        "PAVE", FinalSignal.HIGH_PRIORITY_CANDIDATE,
        triggers=["SPY急落(-2.5%)", "PAVE急落(-5.0%)", "VIXストレス(27)"],
    )

    # SignalResult invariants before persistence
    assert result.no_auto_trade is True
    assert result.no_order_quantity is True
    assert result.sell_signal_reserved is True

    # Save to watchlist and history
    rows = update_watchlist_entry([], result, updated_by="crash_signal_committee")
    save_watchlist(rows, wl_path, dry_run=False, archive_dir=archive_dir)
    append_signal_history(result, history_path, dry_run=False)

    # Reload watchlist row — auto_trade language must not appear in any field
    loaded = load_watchlist(wl_path)
    pave = next((r for r in loaded if r["ticker"] == "PAVE"), None)
    assert pave is not None

    auto_trade_words = ["自動売買実行", "auto_trade=true", "auto_execute", "order_placed"]
    for word in auto_trade_words:
        for col, val in pave.items():
            assert word.lower() not in str(val).lower(), \
                f"Auto-trade language '{word}' must not appear in watchlist column '{col}'"

    # History row also free of auto-trade language
    history_text = history_path.read_text(encoding="utf-8")
    for word in auto_trade_words:
        assert word.lower() not in history_text.lower(), \
            f"Auto-trade language '{word}' must not appear in signal_history.csv"
