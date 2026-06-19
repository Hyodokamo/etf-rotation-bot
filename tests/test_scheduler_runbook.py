"""Phase 6.2: Windows Task Scheduler Setup / Operational Runbook tests.

Verifies that required docs and scripts exist and contain the mandatory content
specified in the Phase 6.2 runbook. Tests are purely static (read files, no
subprocess execution) so they run quickly and have no side effects.

Safety checks:
- Runbook must document dry-run default (watchlist.csv not updated by default)
- Runbook must document no-auto-trade, no-order-quantity, no-brokerage invariants
- bat file must NOT include --allow-watchlist-update in its default call
"""
from __future__ import annotations

from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────────────────────

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_RUNBOOK_PATH = _PROJECT_ROOT / "docs" / "daily_signal_scheduler_runbook.md"
_BAT_PATH     = _PROJECT_ROOT / "scripts" / "daily_signal_check.bat"
_PS1_PATH     = _PROJECT_ROOT / "scripts" / "daily_signal_check.ps1"


def _runbook() -> str:
    return _RUNBOOK_PATH.read_text(encoding="utf-8")


def _bat() -> str:
    return _BAT_PATH.read_text(encoding="utf-8")


def _ps1() -> str:
    return _PS1_PATH.read_text(encoding="utf-8")


# ── 1: Runbook exists ─────────────────────────────────────────────────────────

def test_scheduler_runbook_exists():
    """docs/daily_signal_scheduler_runbook.md must exist and be non-empty."""
    assert _RUNBOOK_PATH.exists(), f"Runbook not found: {_RUNBOOK_PATH}"
    content = _runbook()
    assert len(content) > 500, "Runbook is suspiciously short"
    assert "ETF" in content or "etf" in content.lower()


# ── 2: Task Scheduler mentioned ───────────────────────────────────────────────

def test_scheduler_runbook_mentions_task_scheduler():
    """Runbook must document Windows Task Scheduler setup."""
    content = _runbook()
    assert "Task Scheduler" in content or "タスクスケジューラ" in content
    # Must include at least a task name or setup instruction
    assert "ETF-Daily-Signal-Check" in content or "タスク" in content


# ── 3: dry-run default documented ─────────────────────────────────────────────

def test_scheduler_runbook_mentions_dry_run_default():
    """Runbook must state that dry-run is the default (watchlist.csv not updated)."""
    content = _runbook()
    assert "dry-run" in content or "dry_run" in content
    # Must explain that --allow-watchlist-update is required to change this
    assert "--allow-watchlist-update" in content


# ── 4: no-auto-trade documented ───────────────────────────────────────────────

def test_scheduler_runbook_mentions_no_auto_trade():
    """Runbook must state that auto-trade is never performed."""
    content = _runbook()
    assert "自動売買" in content or "no-auto-trade" in content or "no_auto_trade" in content


# ── 5: no-order-quantity documented ───────────────────────────────────────────

def test_scheduler_runbook_mentions_no_order_quantity():
    """Runbook must state that order quantity is never calculated."""
    content = _runbook()
    assert "注文数量" in content or "no_order_quantity" in content or "no-order-quantity" in content


# ── 6: no-brokerage-connection documented ─────────────────────────────────────

def test_scheduler_runbook_mentions_no_brokerage_connection():
    """Runbook must state that brokerage connection is not performed."""
    content = _runbook()
    assert "証券口座" in content or "brokerage" in content.lower()


# ── 7: bat file uses project root ─────────────────────────────────────────────

def test_bat_file_uses_project_root():
    """daily_signal_check.bat must cd to the project root directory."""
    content = _bat()
    # bat should use %~dp0 (relative to bat file location) or an absolute cd
    assert "cd /d" in content or "cd /D" in content or "cd /d" in content.lower()
    # Typical pattern: cd /d %~dp0.. (project root = parent of scripts\)
    assert "%~dp0" in content or "etf-rotation-bot" in content


# ── 8: bat file calls daily_signal_check.py ───────────────────────────────────

def test_bat_file_calls_daily_signal_check():
    """daily_signal_check.bat must invoke daily_signal_check.py."""
    content = _bat()
    assert "daily_signal_check.py" in content
    assert "python" in content.lower()


# ── 9: bat file does not allow-watchlist-update by default ────────────────────

def test_bat_file_does_not_allow_watchlist_update_by_default():
    """bat file must NOT pass --allow-watchlist-update to Python by default.

    The bat file may document the flag in comments, but the actual python call
    must not include it as a hardcoded default argument.
    """
    content = _bat()
    # Find lines that call python scripts\daily_signal_check.py
    python_call_lines = [
        line for line in content.splitlines()
        if "python" in line.lower() and "daily_signal_check.py" in line
        and not line.strip().startswith("REM")
        and not line.strip().startswith("::")
    ]
    assert python_call_lines, "bat file has no python call line"
    for line in python_call_lines:
        assert "--allow-watchlist-update" not in line, (
            f"bat file must not hardcode --allow-watchlist-update in default call: {line!r}"
        )


# ── Additional: ps1 file exists and is valid ──────────────────────────────────

def test_ps1_file_exists():
    """scripts/daily_signal_check.ps1 must exist."""
    assert _PS1_PATH.exists(), f"PS1 not found: {_PS1_PATH}"
    content = _ps1()
    assert len(content) > 100


def test_ps1_file_uses_project_root():
    """ps1 must navigate to project root via PSScriptRoot."""
    content = _ps1()
    assert "PSScriptRoot" in content or "etf-rotation-bot" in content
    assert "daily_signal_check.py" in content


def test_ps1_file_does_not_allow_watchlist_update_by_default():
    """ps1 must NOT hardcode --allow-watchlist-update in its default invocation."""
    content = _ps1()
    python_call_lines = [
        line for line in content.splitlines()
        if "python" in line.lower()
        and "daily_signal_check.py" in line
        and not line.strip().startswith("#")
    ]
    for line in python_call_lines:
        assert "--allow-watchlist-update" not in line, (
            f"ps1 must not hardcode --allow-watchlist-update: {line!r}"
        )


def test_ps1_propagates_exit_code():
    """ps1 must propagate exit code (exit $ExitCode or $LASTEXITCODE)."""
    content = _ps1()
    assert "exit $ExitCode" in content or "LASTEXITCODE" in content or "exit " in content


def test_runbook_mentions_log_files():
    """Runbook must document scheduler_run_log.jsonl and console log."""
    content = _runbook()
    assert "scheduler_run_log.jsonl" in content
    assert "daily_signal_check_console.log" in content or "console" in content.lower()


def test_runbook_mentions_recommended_time():
    """Runbook must document recommended execution time (JST)."""
    content = _runbook()
    # Should mention JST or Japanese time and a specific time
    assert "JST" in content or "日本時間" in content
    assert "07:30" in content or "08:00" in content or "7:30" in content


def test_runbook_git_ignore_section():
    """Runbook must mention that logs/data/reports should not be committed."""
    content = _runbook()
    assert "gitignore" in content or ".gitignore" in content or "コミット" in content
    assert "logs" in content
    assert "watchlist.csv" in content
