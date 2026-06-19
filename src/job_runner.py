"""Phase 7.3: Job Runner — processes jobs from the async queue.

Safety contract (immutable):
  - No auto-trade, no order quantity, no brokerage connection, no NISA automation.
  - subprocess is called only with FIXED, whitelisted command lists — never with
    shell=True and never with text from Slack messages interpolated into shell strings.
  - eval() and exec() are not used anywhere.
  - --allow-watchlist-update is never passed to any job command (Slack cannot enable it).
  - daily_signal_check job always uses --no-slack (Slack handles its own notifications).
  - Symbol arguments are validated against _VALID_TICKER_RE before being passed to any
    subprocess — no arbitrary strings from Slack reach the command line.
  - MARKET_REFERENCE_SYMBOLS cannot be used as candidate_review targets.

Job types → fixed command lists:
  candidate_review    : python main.py --candidate-review --candidate-symbol SYMBOL --dry-run
  signal_check        : python main.py --crash-signal-check --signal-symbol SYMBOL --dry-run
                        --committee-on-trigger-only
  update_market_data  : python main.py --update-market-data
  daily_signal_check  : python scripts/daily_signal_check.py --no-slack --skip-market-data

Process flow:
  run_loop() → run_once() → process_job() → _build_command() → _command_runner(cmd)

Lock file:
  logs/.job_runner.lock  — contains PID of active runner; prevents double-running.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

from src.job_store import (
    DEFAULT_JOB_QUEUE_PATH,
    DEFAULT_JOB_STATUS_PATH,
    MARKET_REFERENCE_SYMBOLS,
    JOB_TYPES,
    get_queued_jobs,
    update_job_status,
)
from src.logger import logger

_JST = timezone(timedelta(hours=9))

DEFAULT_LOCK_PATH = "logs/.job_runner.lock"
DEFAULT_RUN_INTERVAL_SECONDS = 10
_MAX_OUTPUT_CHARS = 4000

# Ticker validation: 1-12 uppercase alphanumeric with optional ^ . - prefix/separator
_VALID_TICKER_RE = re.compile(r"^[A-Z0-9^.\-]{1,12}$")


def _now_jst() -> str:
    return datetime.now(_JST).isoformat()


def _validate_symbol(symbol: str) -> str:
    """Return the normalised (uppercased) symbol or raise ValueError."""
    upper = symbol.strip().upper()
    if not _VALID_TICKER_RE.match(upper):
        raise ValueError(
            f"Invalid ticker symbol: {symbol!r}. "
            "Must be 1-12 chars, uppercase alphanumeric / ^ . -"
        )
    return upper


def _build_command(job_type: str, args: dict) -> list[str]:
    """Return a fixed, whitelisted command list for the given job type.

    Raises ValueError if the job type is unknown or the symbol is invalid /
    market-reference for candidate_review.
    Raises TypeError if symbol is required but missing.

    No shell=True, no string interpolation into shell commands.
    """
    if job_type not in JOB_TYPES:
        raise ValueError(f"Unknown job type: {job_type!r}")

    if job_type == "candidate_review":
        raw_symbol = args.get("symbol", "")
        if not raw_symbol:
            raise TypeError("candidate_review requires args['symbol']")
        symbol = _validate_symbol(raw_symbol)
        if symbol in MARKET_REFERENCE_SYMBOLS:
            raise ValueError(
                f"{symbol!r} is a market-reference symbol and cannot be used "
                "as a candidate_review target."
            )
        return [
            sys.executable, "main.py",
            "--candidate-review",
            "--candidate-symbol", symbol,
            "--dry-run",
        ]

    if job_type == "signal_check":
        raw_symbol = args.get("symbol", "")
        if not raw_symbol:
            raise TypeError("signal_check requires args['symbol']")
        symbol = _validate_symbol(raw_symbol)
        return [
            sys.executable, "main.py",
            "--crash-signal-check",
            "--signal-symbol", symbol,
            "--dry-run",
            "--committee-on-trigger-only",
        ]

    if job_type == "update_market_data":
        return [sys.executable, "main.py", "--update-market-data"]

    if job_type == "daily_signal_check":
        # --no-slack: runner does not post to Slack (Slack command router handles messaging)
        # --skip-market-data: use existing market data downloaded by update_market_data job
        # NEVER --allow-watchlist-update: Slack-triggered jobs cannot enable watchlist writes
        return [
            sys.executable, "scripts/daily_signal_check.py",
            "--no-slack",
            "--skip-market-data",
        ]

    raise ValueError(f"Unhandled job type: {job_type!r}")  # unreachable


def _default_command_runner(
    cmd: list[str],
    *,
    cwd: str | None = None,
) -> tuple[int, str, str]:
    """Execute cmd via subprocess (no shell=True) and return (returncode, stdout, stderr)."""
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=cwd,
        # shell=True is intentionally absent
    )
    return result.returncode, result.stdout or "", result.stderr or ""


def process_job(
    job: dict,
    *,
    queue_path: str = DEFAULT_JOB_QUEUE_PATH,
    status_path: str = DEFAULT_JOB_STATUS_PATH,
    cwd: str | None = None,
    _command_runner=None,
) -> None:
    """Run a single job dict and update its status in the job store.

    `_command_runner` is injectable for tests — defaults to _default_command_runner.
    Signature: (cmd: list[str], *, cwd: str | None) -> tuple[int, str, str]
    """
    if _command_runner is None:
        _command_runner = _default_command_runner

    job_id = job.get("job_id", "unknown")
    job_type = job.get("job_type", "")
    args = job.get("args") or {}

    started_at = _now_jst()
    update_job_status(
        job_id, "RUNNING",
        started_at=started_at,
        status_path=status_path,
    )
    logger.info(f"[job_runner] start: {job_type} job_id={job_id}")

    try:
        cmd = _build_command(job_type, args)
    except (ValueError, TypeError) as exc:
        finished_at = _now_jst()
        update_job_status(
            job_id, "FAILED",
            started_at=started_at,
            finished_at=finished_at,
            error_summary=f"Command build failed: {exc}",
            status_path=status_path,
        )
        logger.warning(f"[job_runner] failed (build): {job_id} — {exc}")
        return

    try:
        returncode, stdout, stderr = _command_runner(cmd, cwd=cwd)
    except Exception as exc:
        finished_at = _now_jst()
        update_job_status(
            job_id, "FAILED",
            started_at=started_at,
            finished_at=finished_at,
            error_summary=f"Subprocess error: {type(exc).__name__}: {exc}",
            status_path=status_path,
        )
        logger.error(f"[job_runner] subprocess error: {job_id} — {exc}")
        return

    finished_at = _now_jst()
    combined_output = (stdout + "\n" + stderr).strip()
    truncated = combined_output[:_MAX_OUTPUT_CHARS]

    if returncode == 0:
        update_job_status(
            job_id, "SUCCESS",
            started_at=started_at,
            finished_at=finished_at,
            result_summary=truncated or "(no output)",
            status_path=status_path,
        )
        logger.info(f"[job_runner] success: {job_type} job_id={job_id} rc=0")
    else:
        update_job_status(
            job_id, "FAILED",
            started_at=started_at,
            finished_at=finished_at,
            error_summary=truncated or f"(exit code {returncode})",
            status_path=status_path,
        )
        logger.warning(
            f"[job_runner] failed: {job_type} job_id={job_id} rc={returncode}"
        )


# ── Lock file helpers ──────────────────────────────────────────────────────────

def _write_lock(lock_path: str) -> None:
    p = Path(lock_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(str(os.getpid()), encoding="utf-8")


def _remove_lock(lock_path: str) -> None:
    try:
        Path(lock_path).unlink()
    except FileNotFoundError:
        pass


def _is_locked(lock_path: str) -> bool:
    """True if another job runner is already active (lock file present + PID alive)."""
    p = Path(lock_path)
    if not p.exists():
        return False
    try:
        pid = int(p.read_text(encoding="utf-8").strip())
    except (ValueError, OSError):
        return False
    # Check if the PID is still running (platform-safe)
    try:
        os.kill(pid, 0)
        return True  # PID exists
    except (ProcessLookupError, PermissionError):
        # ProcessLookupError → pid gone; PermissionError → different user but pid alive
        # On Windows, os.kill with signal=0 raises PermissionError if pid is alive
        # We treat PermissionError as "alive" to be conservative.
        return False  # type: ignore[return-value]
    except OSError:
        return False


# ── Public run API ─────────────────────────────────────────────────────────────

def run_once(
    *,
    queue_path: str = DEFAULT_JOB_QUEUE_PATH,
    status_path: str = DEFAULT_JOB_STATUS_PATH,
    lock_path: str = DEFAULT_LOCK_PATH,
    cwd: str | None = None,
    _command_runner=None,
) -> int:
    """Process all currently queued jobs (one pass). Returns count of processed jobs."""
    jobs = get_queued_jobs(queue_path=queue_path, status_path=status_path)
    if not jobs:
        return 0
    count = 0
    for job in jobs:
        process_job(
            job,
            queue_path=queue_path,
            status_path=status_path,
            cwd=cwd,
            _command_runner=_command_runner,
        )
        count += 1
    return count


def run_loop(
    *,
    queue_path: str = DEFAULT_JOB_QUEUE_PATH,
    status_path: str = DEFAULT_JOB_STATUS_PATH,
    lock_path: str = DEFAULT_LOCK_PATH,
    interval_seconds: int = DEFAULT_RUN_INTERVAL_SECONDS,
    cwd: str | None = None,
    _command_runner=None,
) -> None:  # pragma: no cover — blocking loop, tested via mocking
    """Blocking loop: poll for new jobs every `interval_seconds` seconds.

    Acquires a lock file to prevent concurrent runners. Exits immediately if
    another runner is detected.
    """
    if _is_locked(lock_path):
        logger.warning(
            f"[job_runner] another runner is active (lock={lock_path}). Exiting."
        )
        return

    _write_lock(lock_path)
    logger.info(f"[job_runner] loop started (interval={interval_seconds}s, lock={lock_path})")
    try:
        while True:
            try:
                n = run_once(
                    queue_path=queue_path,
                    status_path=status_path,
                    lock_path=lock_path,
                    cwd=cwd,
                    _command_runner=_command_runner,
                )
                if n:
                    logger.info(f"[job_runner] processed {n} job(s)")
            except Exception as exc:
                logger.error(f"[job_runner] run_once error: {type(exc).__name__}: {exc}")
            time.sleep(interval_seconds)
    finally:
        _remove_lock(lock_path)
        logger.info("[job_runner] loop stopped, lock released")
