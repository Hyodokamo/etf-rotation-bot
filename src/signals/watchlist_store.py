"""Phase 5.1: watchlist read/write.

AI can update statuses: WATCH / BUY_CANDIDATE / HIGH_PRIORITY_CANDIDATE / HOLD_OFF / REJECT_FOR_NOW.
AI can NEVER overwrite: USER_APPROVED / USER_REJECTED.

Rules:
- Always backup watchlist.csv before saving (data/archive/watchlist_YYYYMMDD_HHMMSS.csv).
- dry_run=True: no backup created, no file written.
- etf_master.csv is never modified.
- ai_sleeve_state.csv is never modified.
- signal_history.csv is append-only.
"""
from __future__ import annotations

import csv
import shutil
from datetime import datetime
from pathlib import Path

from src.logger import logger

DEFAULT_WATCHLIST_PATH = "data/watchlist.csv"
DEFAULT_SIGNAL_HISTORY_PATH = "logs/signal_history.csv"
DEFAULT_ARCHIVE_DIR = "data/archive"

WATCHLIST_COLUMNS = [
    "ticker", "name", "asset_class", "theme", "status", "priority_rank",
    "signal_side", "final_signal", "confidence", "suggested_buy_zone",
    "max_watch_allocation_jpy", "reason_summary", "risk_flags",
    "last_reviewed_at", "next_review_date", "expires_at", "updated_by",
]

SIGNAL_HISTORY_COLUMNS = [
    "as_of_date", "symbol", "signal_side", "final_signal", "total_score",
    "positive_members", "veto_count", "trigger_labels", "risk_flags", "updated_by",
]

AI_SETTABLE_STATUS = frozenset({
    "WATCH", "BUY_CANDIDATE", "HIGH_PRIORITY_CANDIDATE", "HOLD_OFF", "REJECT_FOR_NOW",
})
HUMAN_LOCKED_STATUS = frozenset({"USER_APPROVED", "USER_REJECTED"})


def load_watchlist(path: str | Path = DEFAULT_WATCHLIST_PATH) -> list[dict]:
    """Load watchlist CSV. Missing file → []."""
    p = Path(path)
    if not p.exists():
        return []
    try:
        with open(p, encoding="utf-8-sig", newline="") as f:
            return list(csv.DictReader(f))
    except OSError as e:
        logger.warning(f"Watchlist read failed: {e}")
        return []


def _backup_watchlist(
    path: str | Path,
    archive_dir: str | Path = DEFAULT_ARCHIVE_DIR,
    timestamp: str | None = None,
) -> str | None:
    p = Path(path)
    if not p.exists():
        return None
    ts = timestamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    dst_dir = Path(archive_dir)
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / f"watchlist_{ts}.csv"
    shutil.copy2(p, dst)
    logger.info(f"Watchlist backed up: {p} -> {dst}")
    return str(dst)


def save_watchlist(
    rows: list[dict],
    path: str | Path = DEFAULT_WATCHLIST_PATH,
    *,
    dry_run: bool = False,
    archive_dir: str | Path = DEFAULT_ARCHIVE_DIR,
) -> str | None:
    """Save watchlist CSV with backup. Returns backup path or None.

    dry_run=True: no backup, no write. etf_master and ai_sleeve_state are never touched.
    """
    if dry_run:
        logger.info(f"Watchlist save skipped (dry_run): {len(rows)} rows not written")
        return None
    backup = _backup_watchlist(path, archive_dir)
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=WATCHLIST_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    logger.info(f"Watchlist saved: {len(rows)} rows -> {p}")
    return backup


def update_watchlist_entry(
    rows: list[dict],
    signal,
    *,
    dry_run: bool = False,
    updated_by: str = "signal_engine",
) -> list[dict]:
    """Apply signal result to watchlist rows (in memory).

    - USER_APPROVED / USER_REJECTED are never overwritten.
    - watchlist_update=None → no status change, returns rows unchanged.
    - dry_run has no effect on the in-memory update (call save_watchlist with dry_run separately).
    """
    from src.signals.signal_models import SignalResult
    result: SignalResult = signal

    new_status = result.watchlist_update
    if new_status is None:
        return rows
    if new_status not in AI_SETTABLE_STATUS:
        logger.warning(f"Watchlist: '{new_status}' not AI-settable; skipped")
        return rows

    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    ticker = result.symbol.upper()
    out: list[dict] = []
    found = False

    for row in rows:
        if row.get("ticker", "").upper() == ticker:
            found = True
            existing = row.get("status", "")
            if existing in HUMAN_LOCKED_STATUS:
                logger.info(f"Watchlist: {ticker} status='{existing}' is human-locked; AI update skipped")
                out.append(row)
            else:
                updated = dict(row)
                updated.update({
                    "status": new_status,
                    "final_signal": result.final_signal.value,
                    "signal_side": result.signal_side.value,
                    "confidence": f"{result.confidence:.2f}",
                    "risk_flags": "|".join(result.risk_flags[:3]),
                    "reason_summary": result.recommended_action_text[:100],
                    "last_reviewed_at": now,
                    "updated_by": updated_by,
                })
                out.append(updated)
        else:
            out.append(row)

    if not found:
        new_row = {col: "" for col in WATCHLIST_COLUMNS}
        new_row.update({
            "ticker": ticker,
            "status": new_status,
            "final_signal": result.final_signal.value,
            "signal_side": result.signal_side.value,
            "confidence": f"{result.confidence:.2f}",
            "risk_flags": "|".join(result.risk_flags[:3]),
            "reason_summary": result.recommended_action_text[:100],
            "last_reviewed_at": now,
            "updated_by": updated_by,
        })
        out.append(new_row)

    return out


def append_signal_history(
    signal,
    path: str | Path = DEFAULT_SIGNAL_HISTORY_PATH,
    *,
    dry_run: bool = False,
) -> None:
    """Append one signal result to the append-only signal history CSV.

    dry_run=True: no file written.
    """
    if dry_run:
        logger.info(f"Signal history append skipped (dry_run): {signal.symbol}")
        return
    from src.signals.signal_models import SignalResult
    result: SignalResult = signal
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    new_file = not p.exists() or p.stat().st_size == 0
    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    with open(p, "a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=SIGNAL_HISTORY_COLUMNS)
        if new_file:
            writer.writeheader()
        writer.writerow({
            "as_of_date": now,
            "symbol": result.symbol,
            "signal_side": result.signal_side.value,
            "final_signal": result.final_signal.value,
            "total_score": result.total_score,
            "positive_members": result.positive_member_count,
            "veto_count": result.veto_count,
            "trigger_labels": "|".join(result.trigger_labels),
            "risk_flags": "|".join(result.risk_flags[:3]),
            "updated_by": "signal_engine",
        })
    logger.info(f"Signal history appended: {result.symbol} -> {result.final_signal.value}")
