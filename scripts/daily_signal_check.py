"""Phase 6.0/6.1: Daily Signal Run orchestrator.

Runs market data update + crash signal check + overdue review in sequence.
Each step is a separate subprocess call to main.py.

Safety invariants (always enforced):
- Default: --crash-signal-check runs with --dry-run (watchlist.csv NOT updated)
- --allow-watchlist-update must be explicitly set to remove dry-run
- Default: step 2 uses --committee-on-trigger-only (LLM runs only for symbols with triggers)
- --full-committee-scan explicitly opts in to running LLM committee for all symbols
- No orders, no order quantities, no brokerage integration, no auto-trade
- Slack failure → PARTIAL_FAILURE (run continues, not aborted)
- ai_sleeve_state.csv / etf_master.csv / signal_history.csv never modified
- Logs sanitized: no secrets/tokens in stdout_summary or stderr_summary

CLI:
    python scripts/daily_signal_check.py
    python scripts/daily_signal_check.py --no-slack
    python scripts/daily_signal_check.py --skip-market-data
    python scripts/daily_signal_check.py --allow-watchlist-update
    python scripts/daily_signal_check.py --full-committee-scan
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPTS_DIR.parent

# Make project importable when run as a standalone script
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.signals.scheduler_log import (
    DEFAULT_SCHEDULER_LOG_PATH,
    RunLog,
    RunStatus,
    StepLog,
    append_run_log,
    make_run_id,
    sanitize_summary,
)


def run_step(cmd: list[str], *, timeout: int = 600) -> StepLog:
    """Run one command as a subprocess and return a StepLog.

    Never raises — all failures are captured in StepLog.exit_code.
    stdout/stderr are sanitized before storing (no secrets).
    """
    t0 = time.monotonic()
    try:
        result = subprocess.run(
            cmd,
            cwd=str(_PROJECT_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        duration = round(time.monotonic() - t0, 2)
        return StepLog(
            command=" ".join(cmd),
            exit_code=result.returncode,
            duration_seconds=duration,
            stdout_summary=sanitize_summary(result.stdout),
            stderr_summary=sanitize_summary(result.stderr),
        )
    except subprocess.TimeoutExpired:
        duration = round(time.monotonic() - t0, 2)
        return StepLog(
            command=" ".join(cmd),
            exit_code=-1,
            duration_seconds=duration,
            stderr_summary=f"Timeout after {timeout}s (increase via run_step timeout param if needed)",
        )
    except Exception as exc:
        duration = round(time.monotonic() - t0, 2)
        return StepLog(
            command=" ".join(cmd),
            exit_code=-2,
            duration_seconds=duration,
            stderr_summary=f"Exception: {type(exc).__name__}: {str(exc)[:200]}",
        )


def build_steps(args: argparse.Namespace) -> list[list[str]]:
    """Build the ordered list of command arrays based on CLI args.

    Step 1: --update-market-data (skip with --skip-market-data)
    Step 2: --crash-signal-check [--dry-run] [--signal-slack]
    Step 3: --signal-review --overdue [--signal-slack]

    Default: step 2 includes --dry-run (safe mode).
    --allow-watchlist-update removes --dry-run from step 2.
    """
    python_exe = sys.executable
    main_py = str(_PROJECT_ROOT / "main.py")

    steps: list[list[str]] = []

    # Step 1: market data update
    if not args.skip_market_data:
        steps.append([python_exe, main_py, "--update-market-data"])

    # Step 2: crash signal check (safe dry-run by default; trigger-gated by default)
    step2 = [python_exe, main_py, "--crash-signal-check"]
    if not args.allow_watchlist_update:
        step2.append("--dry-run")
    if not args.no_slack:
        step2.append("--signal-slack")
    # Phase 6.1: trigger gate — default on; --full-committee-scan opts out
    if not args.full_committee_scan:
        step2.append("--committee-on-trigger-only")
    else:
        step2.append("--full-committee-scan")
    steps.append(step2)

    # Step 3: review overdue
    step3 = [python_exe, main_py, "--signal-review", "--overdue"]
    if not args.no_slack:
        step3.append("--signal-slack")
    steps.append(step3)

    return steps


def determine_status(step_logs: list[StepLog]) -> RunStatus:
    """Determine overall run status from step results.

    SUCCESS      : all steps succeeded (exit_code == 0)
    PARTIAL_FAILURE : some steps failed (others may have succeeded)
    FAILED       : all steps failed (or no steps ran)
    """
    if not step_logs:
        return RunStatus.FAILED
    failed_count = sum(1 for s in step_logs if s.exit_code != 0)
    if failed_count == 0:
        return RunStatus.SUCCESS
    if failed_count == len(step_logs):
        return RunStatus.FAILED
    return RunStatus.PARTIAL_FAILURE


def run_daily_signal_check(
    args: argparse.Namespace,
    log_path: str = DEFAULT_SCHEDULER_LOG_PATH,
    _run_step_fn=None,
) -> RunLog:
    """Execute all daily signal check steps and log the result.

    Args:
        args: parsed CLI args.
        log_path: path for scheduler_run_log.jsonl.
        _run_step_fn: injectable step runner for testing (default: run_step).

    Returns RunLog. Never raises — all failures are captured in the log.

    Safety:
    - No orders, no auto-trade, no brokerage.
    - Default mode is dry-run (watchlist.csv not updated).
    - no_auto_trade and no_order_quantity are always True in the log.
    """
    run_fn = _run_step_fn or run_step
    run_id = make_run_id()
    started_at = datetime.now().isoformat()

    _print(f"=== Daily Signal Check: {run_id} ===")
    _print(f"Started: {started_at}")
    _print(
        "Mode: " + (
            "allow-watchlist-update (watchlist.csv may be updated)"
            if args.allow_watchlist_update
            else "dry-run (safe — watchlist.csv NOT updated)"
        )
    )
    _print("Slack: " + ("disabled (--no-slack)" if args.no_slack else "enabled"))
    _print(
        "Committee: " + (
            "full-scan (all symbols)"
            if args.full_committee_scan
            else "trigger-gated (LLM only for symbols with triggers)"
        )
    )
    _print("注文数量は計算しません。自動売買は行いません。証券口座との連携はありません。")
    _print("")

    step_cmds = build_steps(args)
    step_logs: list[StepLog] = []

    for i, cmd in enumerate(step_cmds, 1):
        label = " ".join(cmd[2:])  # skip python + main.py path
        _print(f"Step {i}/{len(step_cmds)}: {label}")
        step_log = run_fn(cmd)
        step_logs.append(step_log)
        status_str = "OK" if step_log.succeeded else f"FAILED (exit={step_log.exit_code})"
        _print(f"  -> {status_str} ({step_log.duration_seconds:.1f}s)")
        if not step_log.succeeded and step_log.stderr_summary:
            first_err = step_log.stderr_summary.splitlines()[0][:120]
            _print(f"  stderr: {first_err}")

    finished_at = datetime.now().isoformat()
    overall_status = determine_status(step_logs)

    # Phase 6.1: parse committee counts from crash signal step (reads stderr_summary)
    committee_target_count = 0
    skipped_count = 0
    for sl in step_logs:
        if "--crash-signal-check" in sl.command:
            committee_target_count, skipped_count = _parse_committee_counts(sl)
            break

    log = RunLog(
        run_id=run_id,
        started_at=started_at,
        finished_at=finished_at,
        status=overall_status,
        steps=step_logs,
        no_auto_trade=True,
        no_order_quantity=True,
        committee_target_count=committee_target_count,
        skipped_count=skipped_count,
    )

    try:
        append_run_log(log, path=log_path)
    except Exception as exc:
        _print(f"Warning: scheduler log write failed: {exc}")

    _print("")
    _print(f"=== Completed: {overall_status.value} ===")

    return log


def _parse_committee_counts(step_log: StepLog) -> tuple[int, int]:
    """Extract committee_target_count and skipped_count from a crash signal StepLog.

    main.py writes [COMMITTEE] target=N skipped=M to stderr (not stdout, which is
    dominated by logger output and gets truncated). Check stderr_summary first,
    then stdout_summary as fallback.
    Returns (0, 0) if not found.
    """
    for text in (step_log.stderr_summary, step_log.stdout_summary):
        m = re.search(r'\[COMMITTEE\] target=(\d+) skipped=(\d+)', text)
        if m:
            return int(m.group(1)), int(m.group(2))
    return 0, 0


def _print(text: str) -> None:
    """Print safely to stdout with UTF-8 encoding (Windows cp932 compatible)."""
    sys.stdout.buffer.write((text + "\n").encode("utf-8", errors="replace"))
    sys.stdout.buffer.flush()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "ETF Rotation Bot — Daily Signal Check Runner. "
            "Advisory only: no orders, no auto-trade, no brokerage."
        )
    )
    parser.add_argument(
        "--no-slack",
        action="store_true",
        default=False,
        dest="no_slack",
        help="Disable Slack notifications (useful for testing).",
    )
    parser.add_argument(
        "--skip-market-data",
        action="store_true",
        default=False,
        dest="skip_market_data",
        help="Skip the --update-market-data step (use existing data/market_data_latest.csv).",
    )
    parser.add_argument(
        "--allow-watchlist-update",
        action="store_true",
        default=False,
        dest="allow_watchlist_update",
        help=(
            "Remove --dry-run from crash signal check, allowing watchlist.csv updates. "
            "CAUTION: must be explicitly set. Default is safe dry-run mode. "
            "Even with this flag: no orders, no auto-trade, no brokerage."
        ),
    )
    parser.add_argument(
        "--log-path",
        default=DEFAULT_SCHEDULER_LOG_PATH,
        dest="log_path",
        help=f"Path for scheduler run log (default: {DEFAULT_SCHEDULER_LOG_PATH}).",
    )
    parser.add_argument(
        "--full-committee-scan",
        action="store_true",
        default=False,
        dest="full_committee_scan",
        help=(
            "Run LLM committee for ALL candidate symbols (overrides default trigger gate). "
            "Use when a comprehensive AI scan is needed regardless of trigger status. "
            "Even with this flag: no orders, no auto-trade, no brokerage."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    log = run_daily_signal_check(args, log_path=args.log_path)
    return 0 if log.status == RunStatus.SUCCESS else 1


if __name__ == "__main__":
    sys.exit(main())
