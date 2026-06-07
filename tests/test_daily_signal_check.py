"""Phase 6.0: Daily Signal Check tests.

Verifies:
- Steps execute in correct order (market_data → crash_signal → review_overdue)
- Default mode uses --dry-run (watchlist.csv not updated)
- --no-slack removes --signal-slack from all steps
- --skip-market-data removes step 1
- --allow-watchlist-update removes --dry-run from step 2
- RunLog status: SUCCESS / PARTIAL_FAILURE / FAILED
- Log written correctly with no_auto_trade=True, no_order_quantity=True
- Source inspection: no writes to ai_sleeve_state / etf_master
- scripts/daily_signal_check.bat exists

Safety invariants tested:
- watchlist.csv NOT updated by default (--dry-run in step 2)
- no_auto_trade / no_order_quantity always True
- no writes to ai_sleeve_state.csv or etf_master.csv from scheduler_log
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from src.signals.scheduler_log import RunStatus, StepLog


# ── Helpers ───────────────────────────────────────────────────────────────────


def _fake_step_ok(cmd: list[str]) -> StepLog:
    return StepLog(
        command=" ".join(cmd),
        exit_code=0,
        duration_seconds=0.1,
        stdout_summary="OK",
    )


def _fake_step_fail(cmd: list[str]) -> StepLog:
    return StepLog(
        command=" ".join(cmd),
        exit_code=1,
        duration_seconds=0.1,
        stderr_summary="Simulated failure",
    )


# ── build_steps tests (pure function, no subprocess) ─────────────────────────


def test_daily_signal_check_runs_steps_in_order():
    """Default args → step 1=market_data, step 2=crash_signal, step 3=review_overdue."""
    from scripts.daily_signal_check import build_steps, parse_args

    args = parse_args(["--no-slack"])
    steps = build_steps(args)

    assert len(steps) == 3
    assert "--update-market-data" in steps[0]
    assert "--crash-signal-check" in steps[1]
    assert "--signal-review" in steps[2]
    assert "--overdue" in steps[2]


def test_daily_signal_check_default_uses_dry_run():
    """Default (no --allow-watchlist-update) → --dry-run in crash signal step."""
    from scripts.daily_signal_check import build_steps, parse_args

    args = parse_args([])
    steps = build_steps(args)
    crash_step = next(s for s in steps if "--crash-signal-check" in s)
    assert "--dry-run" in crash_step


def test_daily_signal_check_no_slack():
    """--no-slack removes --signal-slack from crash signal and review steps."""
    from scripts.daily_signal_check import build_steps, parse_args

    args = parse_args(["--no-slack"])
    steps = build_steps(args)
    for step in steps:
        assert "--signal-slack" not in step, f"--signal-slack found in {step}"


def test_daily_signal_check_slack_enabled_by_default():
    """Without --no-slack, --signal-slack appears in step 2 and step 3."""
    from scripts.daily_signal_check import build_steps, parse_args

    args = parse_args([])
    steps = build_steps(args)
    crash_step = next(s for s in steps if "--crash-signal-check" in s)
    review_step = next(s for s in steps if "--signal-review" in s)
    assert "--signal-slack" in crash_step
    assert "--signal-slack" in review_step


def test_daily_signal_check_skip_market_data():
    """--skip-market-data: first step is crash-signal-check, not market data."""
    from scripts.daily_signal_check import build_steps, parse_args

    args = parse_args(["--skip-market-data", "--no-slack"])
    steps = build_steps(args)

    assert len(steps) == 2
    assert "--update-market-data" not in steps[0]
    assert "--crash-signal-check" in steps[0]


def test_daily_signal_check_allow_watchlist_update_removes_dry_run():
    """--allow-watchlist-update removes --dry-run from crash signal step."""
    from scripts.daily_signal_check import build_steps, parse_args

    args = parse_args(["--allow-watchlist-update", "--no-slack"])
    steps = build_steps(args)
    crash_step = next(s for s in steps if "--crash-signal-check" in s)
    assert "--dry-run" not in crash_step


def test_daily_signal_check_does_not_update_watchlist_by_default():
    """Default mode: --dry-run ensures watchlist.csv is not updated."""
    from scripts.daily_signal_check import build_steps, parse_args

    args = parse_args([])
    steps = build_steps(args)
    crash_cmd = " ".join(next(s for s in steps if "--crash-signal-check" in s))
    assert "--dry-run" in crash_cmd, (
        "Default must include --dry-run to protect watchlist.csv from modification"
    )


# ── run_daily_signal_check tests (with injectable step runner) ────────────────


def test_daily_signal_check_logs_success(tmp_path):
    """All steps succeed → RunLog.status == SUCCESS."""
    from scripts.daily_signal_check import parse_args, run_daily_signal_check

    log_path = str(tmp_path / "run.jsonl")
    args = parse_args(["--no-slack"])
    log = run_daily_signal_check(args, log_path=log_path, _run_step_fn=_fake_step_ok)

    assert log.status == RunStatus.SUCCESS
    assert log.no_auto_trade is True
    assert log.no_order_quantity is True

    entry = json.loads(Path(log_path).read_text(encoding="utf-8").strip())
    assert entry["status"] == "SUCCESS"
    assert entry["no_auto_trade"] is True
    assert entry["no_order_quantity"] is True


def test_daily_signal_check_logs_partial_failure(tmp_path):
    """One step fails, others succeed → PARTIAL_FAILURE (not FAILED)."""
    from scripts.daily_signal_check import parse_args, run_daily_signal_check

    call_count = {"n": 0}

    def step_fn(cmd: list[str]) -> StepLog:
        call_count["n"] += 1
        # Fail step 1 (market data), succeed others
        if "--update-market-data" in cmd:
            return _fake_step_fail(cmd)
        return _fake_step_ok(cmd)

    log_path = str(tmp_path / "run.jsonl")
    args = parse_args(["--no-slack"])
    log = run_daily_signal_check(args, log_path=log_path, _run_step_fn=step_fn)

    assert log.status == RunStatus.PARTIAL_FAILURE
    # All steps still ran (no abort on failure)
    assert call_count["n"] == 3


def test_daily_signal_check_logs_all_failed(tmp_path):
    """All steps fail → RunLog.status == FAILED."""
    from scripts.daily_signal_check import parse_args, run_daily_signal_check

    log_path = str(tmp_path / "run.jsonl")
    args = parse_args(["--no-slack"])
    log = run_daily_signal_check(args, log_path=log_path, _run_step_fn=_fake_step_fail)

    assert log.status == RunStatus.FAILED


def test_daily_signal_check_log_written_to_file(tmp_path):
    """run_daily_signal_check appends to log_path JSONL."""
    from scripts.daily_signal_check import parse_args, run_daily_signal_check

    log_path = tmp_path / "scheduler_run_log.jsonl"
    args = parse_args(["--no-slack", "--skip-market-data"])
    run_daily_signal_check(args, log_path=str(log_path), _run_step_fn=_fake_step_ok)

    assert log_path.exists()
    entries = [json.loads(line) for line in log_path.read_text(encoding="utf-8").strip().splitlines()]
    assert len(entries) == 1
    e = entries[0]
    assert "run_id" in e
    assert "started_at" in e
    assert "finished_at" in e
    assert e["no_auto_trade"] is True
    assert e["no_order_quantity"] is True


# ── Safety: no file writes to protected CSVs ──────────────────────────────────


def test_daily_signal_check_does_not_update_ai_sleeve_state():
    """scheduler_log.py must not open/write ai_sleeve_state.csv."""
    import inspect
    import src.signals.scheduler_log as mod

    src_text = inspect.getsource(mod)
    assert re.search(r'open\s*\(.*ai_sleeve_state', src_text) is None
    assert re.search(r'^(import|from)\s+.*ai_sleeve', src_text, re.MULTILINE) is None


def test_daily_signal_check_does_not_update_etf_master():
    """scheduler_log.py must not open/write etf_master.csv."""
    import inspect
    import src.signals.scheduler_log as mod

    src_text = inspect.getsource(mod)
    assert re.search(r'open\s*\(.*etf_master', src_text) is None
    assert re.search(r'^(import|from)\s+.*etf_master', src_text, re.MULTILINE) is None


def test_daily_signal_check_daily_script_no_ai_sleeve_write():
    """daily_signal_check.py must not write ai_sleeve_state / etf_master."""
    import inspect
    import scripts.daily_signal_check as mod

    src_text = inspect.getsource(mod)
    assert re.search(r'open\s*\(.*ai_sleeve_state', src_text) is None
    assert re.search(r'open\s*\(.*etf_master', src_text) is None


# ── BAT file ──────────────────────────────────────────────────────────────────


def test_bat_file_exists():
    """scripts/daily_signal_check.bat must exist."""
    bat_path = Path(__file__).resolve().parent.parent / "scripts" / "daily_signal_check.bat"
    assert bat_path.exists(), f"BAT file not found at {bat_path}"


def test_bat_file_has_project_root():
    """BAT file must reference the project root directory."""
    bat_path = Path(__file__).resolve().parent.parent / "scripts" / "daily_signal_check.bat"
    content = bat_path.read_text(encoding="utf-8")
    assert "etf-rotation-bot" in content or "daily_signal_check.py" in content


# ── determine_status ──────────────────────────────────────────────────────────


def test_determine_status_success():
    from scripts.daily_signal_check import determine_status

    steps = [StepLog("cmd", 0, 1.0), StepLog("cmd", 0, 1.0)]
    assert determine_status(steps) == RunStatus.SUCCESS


def test_determine_status_partial_failure():
    from scripts.daily_signal_check import determine_status

    steps = [StepLog("cmd", 0, 1.0), StepLog("cmd", 1, 1.0), StepLog("cmd", 0, 1.0)]
    assert determine_status(steps) == RunStatus.PARTIAL_FAILURE


def test_determine_status_all_failed():
    from scripts.daily_signal_check import determine_status

    steps = [StepLog("cmd", 1, 1.0), StepLog("cmd", 1, 1.0)]
    assert determine_status(steps) == RunStatus.FAILED


def test_determine_status_empty():
    from scripts.daily_signal_check import determine_status

    assert determine_status([]) == RunStatus.FAILED
