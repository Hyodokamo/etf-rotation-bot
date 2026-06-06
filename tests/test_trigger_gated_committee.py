"""Phase 6.1: Trigger-gated committee tests.

Verifies:
- Symbols without crash triggers skip LLM committee (deterministic NO_ACTION)
- Symbols with crash triggers run LLM committee
- daily_signal_check.py uses --committee-on-trigger-only by default
- --full-committee-scan opts out of trigger gate
- RunLog records committee_target_count / skipped_count
- Skipped symbols: watchlist_update=None, no_order_quantity=True, no_auto_trade=True
- Slack digest includes "本日Committee実行対象: N件"

Safety invariants:
- no_order_quantity / no_auto_trade always True even in skipped results
- watchlist_update is always None for trigger-gate skipped symbols
"""
from __future__ import annotations

import json
from pathlib import Path

from src.signals.signal_models import FinalSignal, SignalContext, SignalSide


def _make_ctx(with_triggers: bool, symbol: str = "ITA") -> SignalContext:
    return SignalContext(
        symbol=symbol,
        signal_side=SignalSide.BUY,
        crash_triggers=["SPY急落(-3.5%)"] if with_triggers else [],
    )


# ── 1: skips symbols without triggers ────────────────────────────────────────

def test_trigger_gated_committee_skips_no_trigger_symbols():
    """symbol_has_triggers=False → make_deterministic_no_action returns NO_ACTION."""
    from src.signals.trigger_gate import make_deterministic_no_action, symbol_has_triggers

    ctx = _make_ctx(with_triggers=False)
    assert not symbol_has_triggers(ctx)

    result = make_deterministic_no_action("ITA", SignalSide.BUY, ctx)
    assert result.final_signal == FinalSignal.NO_ACTION
    assert "committee_skipped_no_trigger" in result.risk_flags
    assert result.watchlist_update is None
    assert result.member_outputs == []
    assert result.no_order_quantity is True
    assert result.no_auto_trade is True


# ── 2: runs committee for triggered symbols ────────────────────────────────────

def test_trigger_gated_committee_runs_for_triggered_symbol():
    """symbol_has_triggers=True → committee should run (not skipped)."""
    from src.signals.trigger_gate import symbol_has_triggers

    ctx = _make_ctx(with_triggers=True)
    assert symbol_has_triggers(ctx)
    # Verify trigger content
    assert any("SPY" in t for t in ctx.crash_triggers)


def test_trigger_gated_no_triggers_means_empty_list():
    """SignalContext with no crash_triggers → symbol_has_triggers=False."""
    from src.signals.trigger_gate import symbol_has_triggers

    ctx = _make_ctx(with_triggers=False)
    assert ctx.crash_triggers == []
    assert not symbol_has_triggers(ctx)


# ── 3: daily_signal_check.py default includes --committee-on-trigger-only ─────

def test_trigger_gated_committee_daily_default():
    """build_steps with default args includes --committee-on-trigger-only in crash step."""
    from scripts.daily_signal_check import build_steps, parse_args

    args = parse_args(["--no-slack"])
    steps = build_steps(args)
    crash_step = next(s for s in steps if "--crash-signal-check" in s)
    assert "--committee-on-trigger-only" in crash_step
    assert "--full-committee-scan" not in crash_step


# ── 4: --full-committee-scan opts out of trigger gate ─────────────────────────

def test_full_committee_scan_opt_in():
    """--full-committee-scan: crash step has --full-committee-scan, not --committee-on-trigger-only."""
    from scripts.daily_signal_check import build_steps, parse_args

    args = parse_args(["--no-slack", "--full-committee-scan"])
    steps = build_steps(args)
    crash_step = next(s for s in steps if "--crash-signal-check" in s)
    assert "--committee-on-trigger-only" not in crash_step
    assert "--full-committee-scan" in crash_step


# ── 5: RunLog records committee_target_count / skipped_count ─────────────────

def test_scheduler_log_records_committee_target_count(tmp_path):
    """RunLog has committee_target_count and skipped_count; both written to JSONL."""
    from src.signals.scheduler_log import (
        RunLog, RunStatus, StepLog, append_run_log, make_run_id,
    )

    log = RunLog(
        run_id=make_run_id(),
        started_at="2026-06-06T10:00:00",
        finished_at="2026-06-06T10:01:00",
        status=RunStatus.SUCCESS,
        steps=[StepLog("cmd", 0, 1.0)],
        committee_target_count=3,
        skipped_count=4,
    )
    log_path = tmp_path / "run.jsonl"
    append_run_log(log, path=log_path)

    entry = json.loads(log_path.read_text(encoding="utf-8").strip())
    assert entry["committee_target_count"] == 3
    assert entry["skipped_count"] == 4
    assert entry["no_auto_trade"] is True
    assert entry["no_order_quantity"] is True


def test_scheduler_log_committee_counts_default_zero(tmp_path):
    """committee_target_count / skipped_count default to 0 when not specified."""
    from src.signals.scheduler_log import (
        RunLog, RunStatus, StepLog, append_run_log, make_run_id,
    )

    log = RunLog(
        run_id=make_run_id(),
        started_at="2026-06-06T10:00:00",
        finished_at="2026-06-06T10:00:05",
        status=RunStatus.SUCCESS,
        steps=[StepLog("cmd", 0, 1.0)],
    )
    log_path = tmp_path / "run.jsonl"
    append_run_log(log, path=log_path)

    entry = json.loads(log_path.read_text(encoding="utf-8").strip())
    assert entry["committee_target_count"] == 0
    assert entry["skipped_count"] == 0


# ── 6: no watchlist update for skipped symbols ────────────────────────────────

def test_trigger_gated_no_watchlist_update_by_default():
    """Skipped (no-trigger) result always has watchlist_update=None."""
    from src.signals.trigger_gate import make_deterministic_no_action

    for sym in ("ITA", "GRID", "PAVE", "AVUV"):
        ctx = _make_ctx(with_triggers=False, symbol=sym)
        result = make_deterministic_no_action(sym, SignalSide.BUY, ctx)
        assert result.watchlist_update is None, (
            f"{sym}: expected watchlist_update=None for trigger-skipped result"
        )


# ── 7: no_order_quantity / no_auto_trade always True ─────────────────────────

def test_trigger_gated_no_order_quantity_auto_trade():
    """Skipped result enforces no_order_quantity=True / no_auto_trade=True / sell_signal_reserved=True."""
    from src.signals.trigger_gate import make_deterministic_no_action

    ctx = _make_ctx(with_triggers=False)
    result = make_deterministic_no_action("ITA", SignalSide.BUY, ctx)
    assert result.no_order_quantity is True
    assert result.no_auto_trade is True
    assert result.sell_signal_reserved is True


# ── 8: Slack digest includes committee summary line ───────────────────────────

def test_trigger_gated_slack_summary():
    """build_signal_digest_text with committee counts → includes summary line."""
    from src.signals.slack_signal_digest import build_signal_digest_text

    text = build_signal_digest_text(
        results=[],
        committee_target_count=2,
        skipped_count=5,
    )
    assert "Committee" in text or "本日" in text
    assert "2" in text
    assert "5" in text
    # Forbidden words must never appear
    forbidden = ["買え", "売れ", "注文実行", "購入実行", "売却実行", "自動売買実行"]
    for word in forbidden:
        assert word not in text, f"Forbidden word '{word}' found in digest"


def test_trigger_gated_slack_summary_zero_counts():
    """build_signal_digest_text with zero counts → no committee line (backward compat)."""
    from src.signals.slack_signal_digest import build_signal_digest_text

    text = build_signal_digest_text(
        results=[], committee_target_count=0, skipped_count=0, global_only_skipped_count=0,
    )
    # No committee summary section when all are 0
    assert "本日Committee実行対象" not in text


# ── _parse_committee_counts ───────────────────────────────────────────────────

def test_parse_committee_counts_found_in_stderr():
    """_parse_committee_counts finds [COMMITTEE] in stderr_summary (primary source)."""
    from src.signals.scheduler_log import StepLog
    from scripts.daily_signal_check import _parse_committee_counts

    step = StepLog(
        command="main.py --crash-signal-check",
        exit_code=0,
        duration_seconds=1.0,
        stdout_summary="[DRY-RUN] Signal Report...",
        stderr_summary="[COMMITTEE] target=5 skipped=2 global_only=3\n",
    )
    target, skipped, global_only = _parse_committee_counts(step)
    assert target == 5
    assert skipped == 2
    assert global_only == 3


def test_parse_committee_counts_found_in_stdout_fallback():
    """_parse_committee_counts falls back to stdout_summary if not in stderr."""
    from src.signals.scheduler_log import StepLog
    from scripts.daily_signal_check import _parse_committee_counts

    step = StepLog(
        command="main.py --crash-signal-check",
        exit_code=0,
        duration_seconds=1.0,
        stdout_summary="[COMMITTEE] target=7 skipped=1 global_only=2\nOK",
        stderr_summary="",
    )
    target, skipped, global_only = _parse_committee_counts(step)
    assert target == 7
    assert skipped == 1
    assert global_only == 2


def test_parse_committee_counts_backward_compat_2field():
    """Old 2-field [COMMITTEE] format still parses (global_only defaults to 0)."""
    from src.signals.scheduler_log import StepLog
    from scripts.daily_signal_check import _parse_committee_counts

    step = StepLog(
        command="cmd", exit_code=0, duration_seconds=1.0,
        stderr_summary="[COMMITTEE] target=4 skipped=1\n",
    )
    target, skipped, global_only = _parse_committee_counts(step)
    assert target == 4
    assert skipped == 1
    assert global_only == 0


def test_parse_committee_counts_not_found():
    """_parse_committee_counts returns (0, 0, 0) when line not present."""
    from src.signals.scheduler_log import StepLog
    from scripts.daily_signal_check import _parse_committee_counts

    step = StepLog(command="cmd", exit_code=0, duration_seconds=1.0)
    assert _parse_committee_counts(step) == (0, 0, 0)

    step2 = StepLog(
        command="cmd", exit_code=0, duration_seconds=1.0,
        stdout_summary="no committee line here", stderr_summary="also none",
    )
    assert _parse_committee_counts(step2) == (0, 0, 0)


def test_parse_committee_counts_in_run_log(tmp_path):
    """run_daily_signal_check parses all 3 committee counts into RunLog."""
    from src.signals.scheduler_log import RunStatus, StepLog
    from scripts.daily_signal_check import parse_args, run_daily_signal_check

    def fake_step(cmd):
        if "--crash-signal-check" in cmd:
            return StepLog(
                command=" ".join(cmd),
                exit_code=0,
                duration_seconds=1.0,
                stderr_summary="[COMMITTEE] target=3 skipped=1 global_only=6\n",
            )
        return StepLog(command=" ".join(cmd), exit_code=0, duration_seconds=0.5)

    log_path = str(tmp_path / "run.jsonl")
    args = parse_args(["--no-slack"])
    log = run_daily_signal_check(args, log_path=log_path, _run_step_fn=fake_step)

    assert log.committee_target_count == 3
    assert log.skipped_count == 1
    assert log.global_only_skipped_count == 6
    assert log.status == RunStatus.SUCCESS
