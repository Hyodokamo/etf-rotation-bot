"""Phase 6.1.1: Trigger Scope Calibration / Symbol-Specific Committee Gate tests.

Verifies:
- Global market triggers (SPY/QQQ/VIX/SOXX/SMH) alone do NOT trigger committee
- Symbol-specific entry conditions (daily drop, RSI, drawdown, volume) DO trigger
- reference_only symbols never run committee
- allow_global_panic_full_scan config controls panic-mode behavior
- RunLog records global_only_skipped_count
- Slack digest shows committee gate counts
- Safety: no_order_quantity, no_auto_trade always True; watchlist_update=None for skipped

Safety invariants:
- no_order_quantity / no_auto_trade always True
- watchlist_update always None for gate-skipped results
- No brokerage, no auto-trade, no order quantity
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.signals.signal_config import CommitteeGateConfig
from src.signals.signal_models import FinalSignal, SignalContext, SignalSide


# ── Helpers ───────────────────────────────────────────────────────────────────

def _gate_cfg(**overrides) -> CommitteeGateConfig:
    defaults = dict(
        require_symbol_specific_trigger=True,
        allow_global_panic_full_scan=False,
        min_symbol_daily_drop_pct=-3.0,
        min_symbol_5d_drop_pct=-5.0,
        min_symbol_20d_drop_pct=-8.0,
        min_symbol_drawdown_from_high_pct=-10.0,
        rsi_oversold_threshold=35.0,
        volume_spike_threshold=1.5,
    )
    defaults.update(overrides)
    return CommitteeGateConfig(**defaults)


def _ctx_global_only(symbol: str = "ITA") -> SignalContext:
    """Symbol with global triggers only, no symbol-specific entry."""
    return SignalContext(
        symbol=symbol,
        signal_side=SignalSide.BUY,
        crash_triggers=["SPY急落(-2.6%)", "QQQ急落(-4.8%)", "VIXストレス(28)"],
        market_regime="correction",
        etf_entry_quality="too_early",  # extended; not a buying zone
        market_data={},  # no symbol-specific market data
        reference_only=False,
    )


def _ctx_symbol_specific(symbol: str = "ITA") -> SignalContext:
    """Symbol with both global triggers AND symbol-specific entry conditions."""
    return SignalContext(
        symbol=symbol,
        signal_side=SignalSide.BUY,
        crash_triggers=["SPY急落(-2.6%)", "ITA急落(-5.1%)", "ITA高値比下落(-12.3%)"],
        market_regime="correction",
        etf_entry_quality="good",
        market_data={
            "daily_return_pct": "-5.1",
            "return_5d_pct": "-8.3",
            "return_20d_pct": "-12.0",
            "drawdown_from_52w_high_pct": "-12.3",
            "rsi_14": "30",
            "volume_ratio": "2.1",
        },
        reference_only=False,
    )


def _ctx_no_triggers(symbol: str = "PAVE") -> SignalContext:
    """Symbol with no triggers whatsoever (flat in neutral market)."""
    return SignalContext(
        symbol=symbol,
        signal_side=SignalSide.BUY,
        crash_triggers=[],
        market_regime="neutral",
        etf_entry_quality="too_early",
        market_data={},
        reference_only=False,
    )


def _ctx_reference(symbol: str = "SPY") -> SignalContext:
    """Market reference symbol — should never run committee."""
    return SignalContext(
        symbol=symbol,
        signal_side=SignalSide.BUY,
        crash_triggers=["SPY急落(-2.6%)"],
        market_regime="correction",
        etf_entry_quality="too_early",
        market_data={"daily_return_pct": "-2.6"},
        reference_only=True,
    )


# ── 1: global market trigger only → committee NOT run ─────────────────────────

def test_global_market_trigger_only_does_not_run_committee():
    """Global triggers (SPY/QQQ/VIX) alone do not send symbol to committee."""
    from src.signals.trigger_gate import (
        has_global_market_trigger,
        has_symbol_specific_trigger,
        should_run_committee,
        make_deterministic_global_only,
    )
    ctx = _ctx_global_only()
    gate = _gate_cfg()

    assert has_global_market_trigger(ctx)
    assert not has_symbol_specific_trigger(ctx, gate)
    assert not should_run_committee(ctx, gate)

    result = make_deterministic_global_only("ITA", SignalSide.BUY, ctx)
    assert result.final_signal == FinalSignal.WATCH
    assert "global_market_trigger_only" in result.risk_flags
    assert result.watchlist_update is None
    assert result.member_outputs == []


# ── 2: symbol-specific trigger → committee IS run ─────────────────────────────

def test_symbol_specific_trigger_runs_committee():
    """Symbol with daily drop / drawdown conditions → should_run_committee = True."""
    from src.signals.trigger_gate import has_symbol_specific_trigger, should_run_committee

    ctx = _ctx_symbol_specific()
    gate = _gate_cfg()

    assert has_symbol_specific_trigger(ctx, gate)
    assert should_run_committee(ctx, gate)


# ── 3: global + symbol trigger → committee runs ──────────────────────────────

def test_global_plus_symbol_trigger_runs_committee():
    """When both global and symbol-specific triggers present, committee runs."""
    from src.signals.trigger_gate import should_run_committee

    ctx = _ctx_symbol_specific("PAVE")
    gate = _gate_cfg()
    assert should_run_committee(ctx, gate)


# ── 4: reference symbol never runs committee ──────────────────────────────────

def test_reference_symbol_never_runs_committee():
    """reference_only=True → should_run_committee=False regardless of triggers."""
    from src.signals.trigger_gate import should_run_committee

    gate = _gate_cfg()
    # reference with global triggers
    ctx = _ctx_reference("SPY")
    assert not should_run_committee(ctx, gate)

    # reference with symbol-specific triggers (shouldn't happen but guard it)
    ctx2 = SignalContext(
        symbol="QQQ",
        crash_triggers=["QQQ急落(-5%)", "QQQ高値比下落(-12%)"],
        market_data={"daily_return_pct": "-5.0", "rsi_14": "28"},
        etf_entry_quality="good",
        reference_only=True,
    )
    assert not should_run_committee(ctx2, gate)


# ── 5: semiconductor global stress does NOT force all candidates ──────────────

def test_semiconductor_global_stress_does_not_force_all_candidates():
    """SOXX/SMH semiconductor stress (global) alone does not run committee for all ETFs."""
    from src.signals.trigger_gate import (
        has_global_market_trigger,
        has_symbol_specific_trigger,
        should_run_committee,
    )

    # A flat ETF (e.g. AVUV) during semiconductor stress
    ctx = SignalContext(
        symbol="AVUV",
        crash_triggers=["SOXX急落(-10.4%)", "SMH急落(-9.2%)"],
        market_regime="correction",
        etf_entry_quality="too_early",
        market_data={},
        reference_only=False,
    )
    gate = _gate_cfg()

    assert has_global_market_trigger(ctx)
    assert not has_symbol_specific_trigger(ctx, gate)
    assert not should_run_committee(ctx, gate)


# ── 6: allow_global_panic_full_scan=False blocks panic exception ──────────────

def test_allow_global_panic_full_scan_false():
    """allow_global_panic_full_scan=False → panic market does NOT override gate."""
    from src.signals.trigger_gate import should_run_committee

    ctx = SignalContext(
        symbol="ITA",
        crash_triggers=["VIXパニック(42)", "SPY急落(-5.0%)"],
        market_regime="panic",
        etf_entry_quality="too_early",
        market_data={},
        reference_only=False,
    )
    gate = _gate_cfg(allow_global_panic_full_scan=False)
    assert not should_run_committee(ctx, gate)


# ── 7: allow_global_panic_full_scan=True enables panic exception ──────────────

def test_allow_global_panic_full_scan_true():
    """allow_global_panic_full_scan=True AND market_regime=panic → committee runs."""
    from src.signals.trigger_gate import should_run_committee

    ctx = SignalContext(
        symbol="ITA",
        crash_triggers=["VIXパニック(42)", "SPY急落(-5.0%)"],
        market_regime="panic",
        etf_entry_quality="too_early",
        market_data={},
        reference_only=False,
    )
    gate = _gate_cfg(allow_global_panic_full_scan=True)
    assert should_run_committee(ctx, gate)

    # Non-panic regime: gate still applies
    ctx_correction = SignalContext(
        symbol="ITA",
        crash_triggers=["SPY急落(-3.0%)"],
        market_regime="correction",
        etf_entry_quality="too_early",
        market_data={},
        reference_only=False,
    )
    assert not should_run_committee(ctx_correction, gate)


# ── 8: RunLog records global_only_skipped_count ───────────────────────────────

def test_daily_run_records_global_only_skipped_count(tmp_path):
    """run_daily_signal_check parses global_only from stderr and writes to RunLog."""
    from src.signals.scheduler_log import RunStatus, StepLog
    from scripts.daily_signal_check import parse_args, run_daily_signal_check

    def fake_step(cmd):
        if "--crash-signal-check" in cmd:
            return StepLog(
                command=" ".join(cmd),
                exit_code=0,
                duration_seconds=1.0,
                stderr_summary="[COMMITTEE] target=2 skipped=1 global_only=7\n",
            )
        return StepLog(command=" ".join(cmd), exit_code=0, duration_seconds=0.5)

    log_path = str(tmp_path / "run.jsonl")
    args = parse_args(["--no-slack"])
    log = run_daily_signal_check(args, log_path=log_path, _run_step_fn=fake_step)

    assert log.committee_target_count == 2
    assert log.skipped_count == 1
    assert log.global_only_skipped_count == 7
    assert log.status == RunStatus.SUCCESS

    # Verify JSONL output
    entry = json.loads(Path(log_path).read_text(encoding="utf-8").strip())
    assert entry["committee_target_count"] == 2
    assert entry["skipped_count"] == 1
    assert entry["global_only_skipped_count"] == 7
    assert entry["no_auto_trade"] is True
    assert entry["no_order_quantity"] is True


# ── 9: Slack digest shows committee gate counts ───────────────────────────────

def test_slack_digest_shows_committee_gate_counts():
    """build_signal_digest_text shows all 3 committee counts and global triggers."""
    from src.signals.slack_signal_digest import build_signal_digest_text

    text = build_signal_digest_text(
        results=[],
        committee_target_count=3,
        skipped_count=1,
        global_only_skipped_count=6,
        global_triggers=["SPY急落(-2.6%)", "QQQ急落(-4.8%)", "VIXストレス(28)"],
    )
    assert "3" in text
    assert "6" in text
    assert "Committee" in text or "本日" in text
    # Global trigger labels should appear
    assert "SPY" in text or "QQQ" in text or "VIX" in text
    # Forbidden words must not appear
    for word in ["買え", "売れ", "注文実行", "購入実行", "売却実行", "自動売買実行"]:
        assert word not in text


def test_slack_digest_all_zero_no_committee_section():
    """All counts zero → no committee summary section (backward compat)."""
    from src.signals.slack_signal_digest import build_signal_digest_text

    text = build_signal_digest_text(
        results=[],
        committee_target_count=0,
        skipped_count=0,
        global_only_skipped_count=0,
    )
    assert "本日Committee実行対象" not in text


# ── 10: no_order_quantity / no_auto_trade in gate-skipped results ─────────────

def test_trigger_scope_no_order_quantity_auto_trade():
    """make_deterministic_global_only always enforces safety invariants."""
    from src.signals.trigger_gate import make_deterministic_global_only

    # Global-only case (WATCH)
    ctx_global = _ctx_global_only("ITA")
    result = make_deterministic_global_only("ITA", SignalSide.BUY, ctx_global)
    assert result.no_order_quantity is True
    assert result.no_auto_trade is True
    assert result.sell_signal_reserved is True
    assert result.final_signal == FinalSignal.WATCH

    # No-trigger case (NO_ACTION)
    ctx_none = _ctx_no_triggers("PAVE")
    result2 = make_deterministic_global_only("PAVE", SignalSide.BUY, ctx_none)
    assert result2.no_order_quantity is True
    assert result2.no_auto_trade is True
    assert result2.final_signal == FinalSignal.NO_ACTION


# ── 11: watchlist_update=None for all gate-skipped results ────────────────────

def test_trigger_scope_dry_run_no_watchlist_update():
    """Gate-skipped results (global-only or no-trigger) never update watchlist."""
    from src.signals.trigger_gate import make_deterministic_global_only

    for sym, ctx in [
        ("ITA", _ctx_global_only("ITA")),
        ("PAVE", _ctx_no_triggers("PAVE")),
    ]:
        result = make_deterministic_global_only(sym, SignalSide.BUY, ctx)
        assert result.watchlist_update is None, (
            f"{sym}: gate-skipped result must not update watchlist"
        )


# ── Additional: gate config loaded from signal_config.yaml ────────────────────

def test_committee_gate_config_loaded_from_yaml():
    """CommitteeGateConfig is present in SignalConfig loaded from YAML."""
    from src.signals.signal_config import load_signal_config

    cfg = load_signal_config("config/signal_config.yaml")
    assert hasattr(cfg, "committee_gate")
    gate = cfg.committee_gate
    assert gate.require_symbol_specific_trigger is True
    assert gate.allow_global_panic_full_scan is False
    assert gate.min_symbol_daily_drop_pct == -3.0
    assert gate.rsi_oversold_threshold == 35.0
    assert gate.volume_spike_threshold == 1.5


def test_symbol_specific_trigger_rsi_condition():
    """RSI oversold alone qualifies as symbol-specific trigger."""
    from src.signals.trigger_gate import has_symbol_specific_trigger

    ctx = SignalContext(
        symbol="CIBR",
        crash_triggers=["SPY急落(-2.0%)"],
        market_regime="correction",
        etf_entry_quality="too_early",
        market_data={"rsi_14": "30", "daily_return_pct": "-1.0"},
        reference_only=False,
    )
    gate = _gate_cfg()
    assert has_symbol_specific_trigger(ctx, gate)  # RSI 30 ≤ 35


def test_symbol_specific_trigger_volume_spike():
    """High volume alone qualifies as symbol-specific trigger."""
    from src.signals.trigger_gate import has_symbol_specific_trigger

    ctx = SignalContext(
        symbol="XLE",
        crash_triggers=["SPY急落(-2.0%)"],
        market_regime="correction",
        etf_entry_quality="too_early",
        market_data={"volume_ratio": "2.0", "rsi_14": "50"},
        reference_only=False,
    )
    gate = _gate_cfg()
    assert has_symbol_specific_trigger(ctx, gate)  # volume_ratio 2.0 ≥ 1.5


def test_has_global_market_trigger_identifies_all_global_prefixes():
    """has_global_market_trigger correctly identifies SPY/QQQ/VIX/SOXX/SMH."""
    from src.signals.trigger_gate import has_global_market_trigger

    for trigger in [
        "SPY急落(-3.0%)", "QQQ急落(-5.0%)", "QQQ高値比下落(-12%)",
        "VIXパニック(40)", "VIXストレス(28)",
        "SOXX急落(-10%)", "SOXX高値比下落(-15%)",
        "SMH急落(-9%)", "SMH高値比下落(-11%)",
    ]:
        ctx = SignalContext(symbol="ITA", crash_triggers=[trigger], reference_only=False)
        assert has_global_market_trigger(ctx), f"Expected global trigger: {trigger}"

    # Symbol-specific trigger should NOT match
    ctx_sym = SignalContext(symbol="ITA", crash_triggers=["ITA急落(-5%)"], reference_only=False)
    assert not has_global_market_trigger(ctx_sym)
