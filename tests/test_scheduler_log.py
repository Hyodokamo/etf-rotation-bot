"""Phase 6.0: Scheduler log tests.

Verifies:
- Append-only behavior (multiple runs → multiple JSONL lines)
- Secrets filtered from summaries (no token/password/key= in log)
- no_auto_trade / no_order_quantity always True
- run_id uniqueness
- sanitize_summary truncates and filters correctly
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.signals.scheduler_log import (
    RunLog,
    RunStatus,
    StepLog,
    append_run_log,
    make_run_id,
    sanitize_summary,
)


def _make_log(status: RunStatus = RunStatus.SUCCESS) -> RunLog:
    step = StepLog(
        command="python main.py --update-market-data",
        exit_code=0,
        duration_seconds=1.23,
        stdout_summary="Market data written: 11 rows",
        stderr_summary="",
    )
    return RunLog(
        run_id=make_run_id(),
        started_at="2026-06-06T10:00:00",
        finished_at="2026-06-06T10:01:00",
        status=status,
        steps=[step],
        no_auto_trade=True,
        no_order_quantity=True,
    )


# ── append_run_log ────────────────────────────────────────────────────────────


def test_scheduler_log_append_only(tmp_path):
    """Two separate runs → log file has 2 JSONL lines (append-only)."""
    log_path = tmp_path / "scheduler_run_log.jsonl"

    append_run_log(_make_log(RunStatus.SUCCESS), path=log_path)
    append_run_log(_make_log(RunStatus.PARTIAL_FAILURE), path=log_path)

    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2

    first = json.loads(lines[0])
    second = json.loads(lines[1])
    assert first["status"] == "SUCCESS"
    assert second["status"] == "PARTIAL_FAILURE"


def test_scheduler_log_no_secret(tmp_path):
    """Log entry must not contain secret/token keywords in stdout/stderr summary."""
    log_path = tmp_path / "run.jsonl"
    log = RunLog(
        run_id=make_run_id(),
        started_at="2026-06-06T10:00:00",
        finished_at="2026-06-06T10:01:00",
        status=RunStatus.SUCCESS,
        steps=[StepLog(
            command="python main.py --update-market-data",
            exit_code=0,
            duration_seconds=1.0,
            stdout_summary=sanitize_summary("OK\nSLACK_BOT_TOKEN=sk-fake-token\nMarket data done"),
            stderr_summary=sanitize_summary(""),
        )],
    )
    append_run_log(log, path=log_path)

    raw = log_path.read_text(encoding="utf-8")
    assert "sk-fake-token" not in raw
    assert "TOKEN" not in raw.upper() or "SLACK_BOT_TOKEN" not in raw


def test_scheduler_log_no_auto_trade_always_true(tmp_path):
    """no_auto_trade and no_order_quantity are always True in the written log."""
    log_path = tmp_path / "run.jsonl"
    log = _make_log()
    log.no_auto_trade = False  # attempt to set False
    log.no_order_quantity = False
    append_run_log(log, path=log_path)

    entry = json.loads(log_path.read_text(encoding="utf-8").strip())
    assert entry["no_auto_trade"] is True
    assert entry["no_order_quantity"] is True


def test_scheduler_log_fields_present(tmp_path):
    """Log entry contains all required fields."""
    log_path = tmp_path / "run.jsonl"
    append_run_log(_make_log(), path=log_path)

    entry = json.loads(log_path.read_text(encoding="utf-8").strip())
    required = {
        "run_id", "started_at", "finished_at", "status",
        "steps", "no_auto_trade", "no_order_quantity",
    }
    assert required.issubset(entry.keys())
    assert len(entry["steps"]) == 1
    step = entry["steps"][0]
    assert "command" in step
    assert "exit_code" in step
    assert "duration_seconds" in step


def test_scheduler_log_run_id_unique():
    """make_run_id generates unique IDs for consecutive calls."""
    ids = {make_run_id() for _ in range(10)}
    assert len(ids) == 10


# ── sanitize_summary ──────────────────────────────────────────────────────────


def test_sanitize_summary_filters_secrets():
    raw = "Market data done\nSLACK_BOT_TOKEN=sk-test\nMarket data: 11 rows"
    result = sanitize_summary(raw)
    assert "sk-test" not in result
    assert "Market data done" in result
    assert "Market data: 11 rows" in result


def test_sanitize_summary_truncates():
    long_text = "x" * 1000
    result = sanitize_summary(long_text, max_chars=500)
    assert len(result) <= 500


def test_sanitize_summary_empty():
    assert sanitize_summary("") == ""
    assert sanitize_summary(None) == ""  # type: ignore[arg-type]


def test_step_log_succeeded():
    s0 = StepLog("cmd", exit_code=0, duration_seconds=1.0)
    s1 = StepLog("cmd", exit_code=1, duration_seconds=1.0)
    assert s0.succeeded is True
    assert s1.succeeded is False
