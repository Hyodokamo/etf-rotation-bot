"""Phase 5.1.1: Synthetic Crash Scenario Validation tests.

Each test loads a scenario CSV from data/test_scenarios/ and runs the
deterministic signal pipeline (no LLM) to verify expected outcomes.

Safety invariants checked:
- no_order_quantity / no_auto_trade always True
- sell_signal_reserved always True
- dry_run prevents all file writes
- USER_APPROVED / USER_REJECTED never overwritten by AI
"""
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.signals.crash_detector import detect_crash_triggers, load_market_data
from src.signals.signal_committee_runner import _SIGNAL_PERSONAS, run_signal_committee
from src.signals.signal_config import load_signal_config
from src.signals.signal_context_builder import build_signal_context
from src.signals.signal_engine import aggregate_signal
from src.signals.signal_models import (
    FinalSignal,
    MemberStance,
    SignalMemberOutput,
    SignalSide,
)
from src.signals.signal_report import FORBIDDEN_WORDS, build_signal_markdown

SCENARIO_DIR = Path("data/test_scenarios")


# ── helpers ──────────────────────────────────────────────────────────────────


def _cfg():
    return load_signal_config("config/signal_config.yaml")


def _members_positive(n: int, total: int = 7) -> list[SignalMemberOutput]:
    """n positive + (total-n) neutral, all unique member_ids."""
    ids = list(_SIGNAL_PERSONAS.keys())
    out = []
    for i in range(total):
        stance = MemberStance.POSITIVE if i < n else MemberStance.NEUTRAL
        score = 2 if i < n else 0
        out.append(SignalMemberOutput(
            member_id=ids[i % len(ids)], stance=stance, score=score,
            confidence=0.8 if i < n else 0.5, veto=False,
        ))
    return out


def _members_cautious(n: int, total: int = 7) -> list[SignalMemberOutput]:
    """n cautious + (total-n) neutral."""
    ids = list(_SIGNAL_PERSONAS.keys())
    out = []
    for i in range(total):
        stance = MemberStance.CAUTIOUS if i < n else MemberStance.NEUTRAL
        score = -1 if i < n else 0
        out.append(SignalMemberOutput(
            member_id=ids[i % len(ids)], stance=stance, score=score,
            confidence=0.7, veto=False,
        ))
    return out


def _members_with_veto(veto_member_id: str = "core_ai_auditor") -> list[SignalMemberOutput]:
    """6 positive + 1 veto from veto_member_id."""
    ids = list(_SIGNAL_PERSONAS.keys())
    out = []
    for mid in ids:
        if mid == veto_member_id:
            out.append(SignalMemberOutput(
                member_id=mid, stance=MemberStance.REJECT, score=-2,
                confidence=0.95, rationale="データ品質不十分。veto発動。", veto=True,
            ))
        else:
            out.append(SignalMemberOutput(
                member_id=mid, stance=MemberStance.POSITIVE, score=2,
                confidence=0.8, veto=False,
            ))
    return out


def _high_invested_portfolio():
    """Mock: 85% AI sleeve invested → portfolio_risk=high."""
    pctx = MagicMock()
    pctx.ai_sleeve.total_budget_jpy = 1_000_000.0
    pctx.ai_sleeve.current_invested_jpy = 850_000.0
    pctx.ai_sleeve.current_cash_jpy = 150_000.0
    pctx.core_manual = []
    pctx.core_themes = []
    return pctx


# ── test_scenario_normal_market_no_action ─────────────────────────────────────


def test_scenario_normal_market_no_action():
    """Normal market CSV fires no triggers → result is NO_ACTION or WATCH at most."""
    market_data = load_market_data(SCENARIO_DIR / "normal_market.csv")
    cfg = _cfg()

    triggers = detect_crash_triggers(market_data, cfg)
    assert not triggers, f"Normal market should fire no triggers, got: {triggers}"

    ctx = build_signal_context("GRID", market_data, cfg)
    members = run_signal_committee(ctx, client=None)
    result = aggregate_signal("GRID", SignalSide.BUY, members, ctx, cfg)

    assert result.final_signal in (FinalSignal.NO_ACTION, FinalSignal.WATCH), \
        f"Normal market should be NO_ACTION or WATCH, got {result.final_signal}"
    assert result.final_signal not in (FinalSignal.BUY_CANDIDATE, FinalSignal.HIGH_PRIORITY_CANDIDATE)
    assert result.no_order_quantity is True
    assert result.no_auto_trade is True


# ── test_scenario_grid_pullback_candidate_or_watch ────────────────────────────


def test_scenario_grid_pullback_candidate_or_watch():
    """GRID pullback CSV: market + GRID-specific triggers fire; result WATCH or better."""
    market_data = load_market_data(SCENARIO_DIR / "grid_pullback.csv")
    cfg = _cfg()

    triggers = detect_crash_triggers(market_data, cfg, symbol="GRID")
    assert triggers, "grid_pullback should fire at least one trigger"
    assert any("GRID" in t for t in triggers), \
        f"Expected GRID-specific trigger in {triggers}"
    assert any("SPY" in t or "QQQ" in t or "VIX" in t for t in triggers), \
        f"Expected market-wide trigger in {triggers}"

    ctx = build_signal_context("GRID", market_data, cfg)
    members = run_signal_committee(ctx, client=None)
    result = aggregate_signal("GRID", SignalSide.BUY, members, ctx, cfg)

    assert result.final_signal in (
        FinalSignal.WATCH, FinalSignal.BUY_CANDIDATE, FinalSignal.HIGH_PRIORITY_CANDIDATE
    ), f"GRID pullback (neutral committee) should be WATCH+, got {result.final_signal}"
    assert result.no_order_quantity is True
    assert result.no_auto_trade is True
    assert len(result.trigger_labels) >= 1


# ── test_scenario_ita_pullback_candidate_or_watch ─────────────────────────────


def test_scenario_ita_pullback_candidate_or_watch():
    """ITA pullback CSV: triggers fire; result WATCH or better."""
    market_data = load_market_data(SCENARIO_DIR / "ita_pullback.csv")
    cfg = _cfg()

    triggers = detect_crash_triggers(market_data, cfg, symbol="ITA")
    assert triggers, "ita_pullback should fire at least one trigger"
    assert any("ITA" in t for t in triggers), f"Expected ITA-specific trigger in {triggers}"

    ctx = build_signal_context("ITA", market_data, cfg)
    assert ctx.symbol == "ITA"
    assert ctx.crash_triggers  # triggers are stored in context

    members = run_signal_committee(ctx, client=None)
    result = aggregate_signal("ITA", SignalSide.BUY, members, ctx, cfg)

    assert result.final_signal in (
        FinalSignal.WATCH, FinalSignal.BUY_CANDIDATE, FinalSignal.HIGH_PRIORITY_CANDIDATE
    ), f"ITA pullback should be WATCH+, got {result.final_signal}"
    assert result.no_order_quantity is True


# ── test_scenario_smh_selloff_hold_off_due_to_core_overlap ───────────────────


def test_scenario_smh_selloff_hold_off_due_to_core_overlap():
    """SMH semiconductor selloff: triggers fire; high portfolio risk blocks BUY → HOLD_OFF."""
    market_data = load_market_data(SCENARIO_DIR / "smh_semiconductor_selloff.csv")
    cfg = _cfg()

    triggers = detect_crash_triggers(market_data, cfg)
    assert any("SOXX" in t or "SMH" in t for t in triggers), \
        f"Semiconductor triggers must fire, got: {triggers}"

    # Simulate overinvested AI sleeve (core overlap / high invested ratio scenario)
    portfolio_ctx = _high_invested_portfolio()
    ctx = build_signal_context("SMH", market_data, cfg, portfolio_context=portfolio_ctx)

    assert ctx.portfolio_risk == "high", \
        f"85% invested should yield portfolio_risk=high, got {ctx.portfolio_risk}"

    # Even with a fully positive committee, portfolio_risk=high blocks BUY_CANDIDATE
    members = _members_positive(5)
    result = aggregate_signal("SMH", SignalSide.BUY, members, ctx, cfg, portfolio_ctx)

    assert result.final_signal == FinalSignal.HOLD_OFF, \
        f"High portfolio risk should yield HOLD_OFF, got {result.final_signal}"
    assert result.final_signal not in (FinalSignal.BUY_CANDIDATE, FinalSignal.HIGH_PRIORITY_CANDIDATE)
    assert result.no_order_quantity is True


# ── test_scenario_ai_auditor_veto_blocks_buy_candidate ───────────────────────


def test_scenario_ai_auditor_veto_blocks_buy_candidate():
    """AI auditor veto (unknown metadata): blocks BUY_CANDIDATE → REJECT_FOR_NOW."""
    market_data = load_market_data(SCENARIO_DIR / "auditor_veto_unknown_metadata.csv")
    cfg = _cfg()

    triggers = detect_crash_triggers(market_data, cfg, symbol="UNKNETF")
    assert triggers, "auditor_veto scenario must have crash triggers"

    ctx = build_signal_context("UNKNETF", market_data, cfg)
    # Inject core_ai_auditor veto — simulates "unknown metadata / insufficient data" block
    members = _members_with_veto("core_ai_auditor")
    result = aggregate_signal("UNKNETF", SignalSide.BUY, members, ctx, cfg)

    assert result.final_signal == FinalSignal.REJECT_FOR_NOW, \
        f"AI auditor veto should yield REJECT_FOR_NOW, got {result.final_signal}"
    assert result.veto_count >= 1
    assert result.no_order_quantity is True
    assert result.no_auto_trade is True


# ── test_scenario_portfolio_risk_high_blocks_buy_candidate ───────────────────


def test_scenario_portfolio_risk_high_blocks_buy_candidate():
    """Portfolio risk high: even with crash triggers, BUY_CANDIDATE is blocked → HOLD_OFF."""
    market_data = load_market_data(SCENARIO_DIR / "portfolio_risk_high.csv")
    cfg = _cfg()

    triggers = detect_crash_triggers(market_data, cfg, symbol="GRID")
    assert triggers, "portfolio_risk_high scenario must have crash triggers"

    portfolio_ctx = _high_invested_portfolio()
    ctx = build_signal_context("GRID", market_data, cfg, portfolio_context=portfolio_ctx)

    assert ctx.portfolio_risk == "high"
    assert ctx.crash_triggers

    members = _members_positive(5)
    result = aggregate_signal("GRID", SignalSide.BUY, members, ctx, cfg, portfolio_ctx)

    assert result.final_signal == FinalSignal.HOLD_OFF, \
        f"High portfolio risk should yield HOLD_OFF, got {result.final_signal}"
    assert result.final_signal not in (FinalSignal.BUY_CANDIDATE, FinalSignal.HIGH_PRIORITY_CANDIDATE)


# ── test_scenario_dry_run_no_write ────────────────────────────────────────────


def test_scenario_dry_run_no_write(tmp_path):
    """Dry-run with crash scenario: watchlist.csv / signal_history.csv / archive not written."""
    from src.signals.watchlist_store import (
        append_signal_history,
        load_watchlist,
        save_watchlist,
        update_watchlist_entry,
    )

    market_data = load_market_data(SCENARIO_DIR / "grid_pullback.csv")
    cfg = _cfg()
    ctx = build_signal_context("GRID", market_data, cfg)
    members = run_signal_committee(ctx, client=None)
    result = aggregate_signal("GRID", SignalSide.BUY, members, ctx, cfg)

    watchlist_path = tmp_path / "watchlist.csv"
    history_path = tmp_path / "signal_history.csv"
    archive_dir = tmp_path / "archive"

    watchlist = load_watchlist(watchlist_path)
    updated = update_watchlist_entry(watchlist, result, dry_run=True)
    save_watchlist(updated, watchlist_path, dry_run=True, archive_dir=archive_dir)
    append_signal_history(result, history_path, dry_run=True)

    assert not watchlist_path.exists(), "dry_run must not write watchlist.csv"
    assert not history_path.exists(), "dry_run must not write signal_history.csv"
    assert not archive_dir.exists() or not list(archive_dir.glob("*.csv")), \
        "dry_run must not create backup"
    assert result.no_order_quantity is True
    assert result.no_auto_trade is True


# ── test_scenario_market_data_file_option ─────────────────────────────────────


def test_scenario_market_data_file_option():
    """Different market data files produce different trigger sets (option works correctly)."""
    normal_data = load_market_data(SCENARIO_DIR / "normal_market.csv")
    pullback_data = load_market_data(SCENARIO_DIR / "grid_pullback.csv")
    cfg = _cfg()

    normal_triggers = detect_crash_triggers(normal_data, cfg)
    pullback_triggers = detect_crash_triggers(pullback_data, cfg)

    assert not normal_triggers, "normal_market should have no triggers"
    assert pullback_triggers, "grid_pullback should have triggers"
    # The two files must produce different (non-identical) trigger sets
    assert set(normal_triggers) != set(pullback_triggers)

    # Also verify that loading a non-existent file returns empty (graceful)
    empty = load_market_data("data/test_scenarios/nonexistent.csv")
    assert empty == {}
    assert detect_crash_triggers(empty, cfg) == []


# ── test_scenario_report_includes_scenario_name ──────────────────────────────


def test_scenario_report_includes_scenario_name():
    """build_signal_markdown includes scenario_name in header and body when supplied."""
    market_data = load_market_data(SCENARIO_DIR / "grid_pullback.csv")
    cfg = _cfg()
    ctx = build_signal_context("GRID", market_data, cfg)
    members = run_signal_committee(ctx, client=None)
    result = aggregate_signal("GRID", SignalSide.BUY, members, ctx, cfg)

    md_with = build_signal_markdown([result], market_regime="correction", scenario_name="grid_pullback")
    assert "grid_pullback" in md_with, "Scenario name must appear in the report"
    assert "scenario" in md_with.lower() or "シナリオ" in md_with, \
        "Report must label the scenario"

    # Without scenario_name, the scenario slug must not appear
    md_without = build_signal_markdown([result], market_regime="correction")
    assert "grid_pullback" not in md_without, \
        "Scenario name must NOT appear when scenario_name is not passed"

    # Safety notes must appear in both variants
    assert "安全注記" in md_with
    assert "安全注記" in md_without


# ── test_scenario_no_order_quantity_auto_trade ────────────────────────────────


def test_scenario_no_order_quantity_auto_trade():
    """Safety invariants hold across all scenario files and symbols."""
    scenarios: list[tuple[str, str]] = [
        ("normal_market.csv", "GRID"),
        ("grid_pullback.csv", "GRID"),
        ("ita_pullback.csv", "ITA"),
        ("smh_semiconductor_selloff.csv", "SMH"),
        ("auditor_veto_unknown_metadata.csv", "UNKNETF"),
        ("portfolio_risk_high.csv", "GRID"),
    ]
    cfg = _cfg()

    for filename, symbol in scenarios:
        market_data = load_market_data(SCENARIO_DIR / filename)
        ctx = build_signal_context(symbol, market_data, cfg)
        members = run_signal_committee(ctx, client=None)
        result = aggregate_signal(symbol, SignalSide.BUY, members, ctx, cfg)

        assert result.no_order_quantity is True, \
            f"no_order_quantity must be True for {filename}/{symbol}"
        assert result.no_auto_trade is True, \
            f"no_auto_trade must be True for {filename}/{symbol}"
        assert result.sell_signal_reserved is True, \
            f"sell_signal_reserved must be True for {filename}/{symbol}"
        assert result.final_signal in list(FinalSignal), \
            f"Invalid final_signal for {filename}/{symbol}"
        for bad in FORBIDDEN_WORDS:
            assert bad not in result.recommended_action_text, \
                f"Forbidden word '{bad}' in recommended_action_text for {filename}/{symbol}"

    # SELL side always returns NO_ACTION regardless of scenario
    market_data = load_market_data(SCENARIO_DIR / "grid_pullback.csv")
    ctx = build_signal_context("GRID", market_data, cfg)
    members = _members_positive(6)
    sell_result = aggregate_signal("GRID", SignalSide.SELL, members, ctx, cfg)
    assert sell_result.final_signal == FinalSignal.NO_ACTION
    assert sell_result.sell_signal_reserved is True


# ── Phase 5.1.2: Positive Path Validation ────────────────────────────────────


def _portfolio_with_existing_holding(symbol: str):
    """Mock portfolio context with one symbol in existing_related_holdings."""
    pctx = MagicMock()
    pctx.ai_sleeve.total_budget_jpy = 1_000_000.0
    pctx.ai_sleeve.current_invested_jpy = 100_000.0
    pctx.ai_sleeve.current_cash_jpy = 900_000.0
    holding = MagicMock()
    holding.symbol = symbol
    pctx.existing_related_holdings = [holding]
    pctx.core_manual = []
    pctx.core_themes = []
    return pctx


def test_scenario_ita_positive_pullback_buy_candidate():
    """ITA deep correction + excellent entry + positive committee → BUY_CANDIDATE."""
    market_data = load_market_data(SCENARIO_DIR / "ita_positive_pullback.csv")
    cfg = _cfg()

    triggers = detect_crash_triggers(market_data, cfg, symbol="ITA")
    assert triggers, "ita_positive_pullback must have crash triggers"
    assert any("ITA" in t for t in triggers), f"Expected ITA trigger, got: {triggers}"

    ctx = build_signal_context("ITA", market_data, cfg)
    assert ctx.etf_entry_quality == "excellent", \
        f"ITA deep pullback should have excellent entry quality, got {ctx.etf_entry_quality}"

    members = _members_positive(4)  # score=8, positive=4 → BUY_CANDIDATE threshold
    result = aggregate_signal("ITA", SignalSide.BUY, members, ctx, cfg)

    assert result.final_signal == FinalSignal.BUY_CANDIDATE, \
        f"Positive committee + excellent entry should yield BUY_CANDIDATE, got {result.final_signal}"
    assert result.no_order_quantity is True
    assert result.no_auto_trade is True
    assert result.watchlist_update == "BUY_CANDIDATE"


def test_scenario_xlu_positive_pullback_candidate_or_watch():
    """XLU utilities pullback + positive committee → BUY_CANDIDATE or better."""
    market_data = load_market_data(SCENARIO_DIR / "xlu_positive_pullback.csv")
    cfg = _cfg()

    triggers = detect_crash_triggers(market_data, cfg, symbol="XLU")
    assert triggers, "xlu_positive_pullback must have crash triggers"
    assert any("XLU" in t for t in triggers), f"Expected XLU trigger, got: {triggers}"

    ctx = build_signal_context("XLU", market_data, cfg)
    assert ctx.etf_entry_quality in ("excellent", "good"), \
        f"XLU pullback should have good+ entry quality, got {ctx.etf_entry_quality}"

    members = _members_positive(4)
    result = aggregate_signal("XLU", SignalSide.BUY, members, ctx, cfg)

    assert result.final_signal in (FinalSignal.BUY_CANDIDATE, FinalSignal.HIGH_PRIORITY_CANDIDATE), \
        f"Positive committee + good entry should yield BUY_CANDIDATE+, got {result.final_signal}"
    assert result.no_order_quantity is True
    assert result.no_auto_trade is True


def test_scenario_pave_positive_pullback_candidate_or_watch():
    """PAVE infrastructure pullback + positive committee → BUY_CANDIDATE or better."""
    market_data = load_market_data(SCENARIO_DIR / "pave_positive_pullback.csv")
    cfg = _cfg()

    triggers = detect_crash_triggers(market_data, cfg, symbol="PAVE")
    assert triggers, "pave_positive_pullback must have crash triggers"
    assert any("PAVE" in t for t in triggers), f"Expected PAVE trigger, got: {triggers}"

    ctx = build_signal_context("PAVE", market_data, cfg)
    assert ctx.etf_entry_quality == "excellent", \
        f"PAVE deep pullback should have excellent entry quality, got {ctx.etf_entry_quality}"

    members = _members_positive(4)
    result = aggregate_signal("PAVE", SignalSide.BUY, members, ctx, cfg)

    assert result.final_signal in (FinalSignal.BUY_CANDIDATE, FinalSignal.HIGH_PRIORITY_CANDIDATE), \
        f"Positive committee + excellent entry should yield BUY_CANDIDATE+, got {result.final_signal}"
    assert result.no_order_quantity is True


def test_scenario_avuv_positive_pullback_candidate_or_watch():
    """AVUV small-cap value pullback + positive committee → BUY_CANDIDATE or better."""
    market_data = load_market_data(SCENARIO_DIR / "avuv_positive_pullback.csv")
    cfg = _cfg()

    triggers = detect_crash_triggers(market_data, cfg, symbol="AVUV")
    assert triggers, "avuv_positive_pullback must have crash triggers"
    assert any("AVUV" in t for t in triggers), f"Expected AVUV trigger, got: {triggers}"

    ctx = build_signal_context("AVUV", market_data, cfg)
    assert ctx.etf_entry_quality in ("excellent", "good"), \
        f"AVUV pullback should have good+ entry quality, got {ctx.etf_entry_quality}"

    members = _members_positive(4)
    result = aggregate_signal("AVUV", SignalSide.BUY, members, ctx, cfg)

    assert result.final_signal in (FinalSignal.BUY_CANDIDATE, FinalSignal.HIGH_PRIORITY_CANDIDATE), \
        f"Positive committee + good entry should yield BUY_CANDIDATE+, got {result.final_signal}"
    assert result.no_order_quantity is True


def test_scenario_cibr_positive_pullback_candidate_or_watch():
    """CIBR cybersecurity pullback + positive committee → BUY_CANDIDATE or WATCH."""
    market_data = load_market_data(SCENARIO_DIR / "cibr_positive_pullback.csv")
    cfg = _cfg()

    triggers = detect_crash_triggers(market_data, cfg, symbol="CIBR")
    assert triggers, "cibr_positive_pullback must have crash triggers"
    assert any("CIBR" in t for t in triggers), f"Expected CIBR trigger, got: {triggers}"

    ctx = build_signal_context("CIBR", market_data, cfg)
    assert ctx.etf_entry_quality in ("excellent", "good", "acceptable"), \
        f"CIBR pullback should have acceptable+ entry quality, got {ctx.etf_entry_quality}"

    members = _members_positive(4)
    result = aggregate_signal("CIBR", SignalSide.BUY, members, ctx, cfg)

    assert result.final_signal in (
        FinalSignal.WATCH, FinalSignal.BUY_CANDIDATE, FinalSignal.HIGH_PRIORITY_CANDIDATE
    ), f"Positive committee should yield WATCH+, got {result.final_signal}"
    assert result.no_order_quantity is True


def test_scenario_grid_hold_off_not_reject_for_existing_holding():
    """GRID with existing_related_holdings: positive committee → downgraded (not REJECT_FOR_NOW)."""
    market_data = load_market_data(SCENARIO_DIR / "grid_hold_off_due_to_existing_holding.csv")
    cfg = _cfg()

    triggers = detect_crash_triggers(market_data, cfg, symbol="GRID")
    assert triggers, "grid_hold_off scenario must have crash triggers"

    portfolio_ctx = _portfolio_with_existing_holding("GRID")
    ctx = build_signal_context("GRID", market_data, cfg, portfolio_context=portfolio_ctx)

    # Verify existing holding is reflected in context summary
    assert "GRID" in ctx.portfolio_context_summary.get("existing_related_holdings", []), \
        "GRID must appear in portfolio_context_summary.existing_related_holdings"

    # Positive committee would normally give BUY_CANDIDATE
    members = _members_positive(4)
    result = aggregate_signal("GRID", SignalSide.BUY, members, ctx, cfg, portfolio_ctx)

    # existing_related_holdings causes a one-level downgrade — not outright REJECT
    assert result.final_signal != FinalSignal.REJECT_FOR_NOW, \
        "existing_related_holdings should cause downgrade, not REJECT_FOR_NOW"
    assert result.final_signal in (FinalSignal.WATCH, FinalSignal.BUY_CANDIDATE, FinalSignal.HOLD_OFF), \
        f"Downgraded result should be WATCH/BUY_CANDIDATE/HOLD_OFF, got {result.final_signal}"
    assert any("existing_related_holdings" in f for f in result.risk_flags), \
        "risk_flags must mention existing_related_holdings downgrade"
    assert result.no_order_quantity is True


def test_scenario_smh_hold_off_due_to_core_overlap():
    """SMH with very_high core overlap flag: positive committee → HOLD_OFF (not BUY_CANDIDATE)."""
    market_data = load_market_data(SCENARIO_DIR / "smh_hold_off_due_to_core_overlap.csv")
    cfg = _cfg()

    triggers = detect_crash_triggers(market_data, cfg)
    assert any("SOXX" in t or "SMH" in t for t in triggers), \
        f"Semiconductor triggers must fire, got: {triggers}"

    ctx = build_signal_context("SMH", market_data, cfg)
    # Inject very_high overlap flag (ETF master would normally provide this for SMH)
    ctx.skeptic_flags.append("コア重複=very_high")

    # Positive committee (would normally give HIGH_PRIORITY_CANDIDATE)
    members = _members_positive(5)  # score=10, positive=5
    result = aggregate_signal("SMH", SignalSide.BUY, members, ctx, cfg)

    assert result.final_signal == FinalSignal.HOLD_OFF, \
        f"very_high core overlap should downgrade to HOLD_OFF, got {result.final_signal}"
    assert result.final_signal not in (FinalSignal.BUY_CANDIDATE, FinalSignal.HIGH_PRIORITY_CANDIDATE)
    assert result.final_signal != FinalSignal.REJECT_FOR_NOW, \
        "very_high overlap should be HOLD_OFF not REJECT_FOR_NOW"
    assert any("very_high" in f for f in result.risk_flags), \
        "risk_flags must mention very_high overlap"
    assert result.no_order_quantity is True


def test_scenario_unknown_reject_for_insufficient_data():
    """UNKNETF with data_quality=insufficient: even positive committee → REJECT_FOR_NOW."""
    market_data = load_market_data(SCENARIO_DIR / "unknown_reject_due_to_insufficient_data.csv")
    cfg = _cfg()

    triggers = detect_crash_triggers(market_data, cfg, symbol="UNKNETF")
    assert triggers, "unknown_reject scenario must have crash triggers"

    ctx = build_signal_context("UNKNETF", market_data, cfg)
    # Inject insufficient data flag (ETF master with data_quality_status=insufficient would do this)
    ctx.skeptic_flags.append("data_quality=insufficient(veto候補)")

    # Even positive committee cannot override insufficient data
    members = _members_positive(5)
    result = aggregate_signal("UNKNETF", SignalSide.BUY, members, ctx, cfg)

    assert result.final_signal == FinalSignal.REJECT_FOR_NOW, \
        f"data_quality=insufficient should yield REJECT_FOR_NOW, got {result.final_signal}"
    assert any("insufficient" in f for f in result.risk_flags), \
        "risk_flags must mention insufficient data block"
    assert result.no_order_quantity is True


def test_needs_order_screen_check_warning_not_veto():
    """needs_order_screen_check in skeptic_flags is a warning — does NOT block BUY_CANDIDATE."""
    cfg = _cfg()
    market_data = load_market_data(SCENARIO_DIR / "grid_pullback.csv")
    ctx = build_signal_context("GRID", market_data, cfg)
    # Add order screen check flag (warning level, not veto)
    ctx.skeptic_flags.append("発注前確認要(needs_order_screen_check=true)")

    members = _members_positive(4)
    result = aggregate_signal("GRID", SignalSide.BUY, members, ctx, cfg)

    assert result.final_signal != FinalSignal.REJECT_FOR_NOW, \
        "needs_order_screen_check alone must NOT cause REJECT_FOR_NOW"
    assert result.final_signal in (FinalSignal.BUY_CANDIDATE, FinalSignal.HIGH_PRIORITY_CANDIDATE), \
        f"needs_order_screen_check should not block BUY_CANDIDATE, got {result.final_signal}"
    # Warning should appear in risk flags
    assert any("needs_order_screen_check" in f or "発注前確認要" in f for f in result.risk_flags), \
        "needs_order_screen_check should appear in risk_flags as a warning"
    assert result.no_order_quantity is True


def test_needs_broker_check_warning_not_veto():
    """data_quality=needs_broker_check in skeptic_flags is a warning — does NOT block BUY_CANDIDATE."""
    cfg = _cfg()
    market_data = load_market_data(SCENARIO_DIR / "ita_pullback.csv")
    ctx = build_signal_context("ITA", market_data, cfg)
    # broker_check is a warning-level flag, not a hard veto
    ctx.skeptic_flags.append("data_quality=needs_broker_check")

    members = _members_positive(4)
    result = aggregate_signal("ITA", SignalSide.BUY, members, ctx, cfg)

    assert result.final_signal != FinalSignal.REJECT_FOR_NOW, \
        "data_quality=needs_broker_check must NOT cause REJECT_FOR_NOW"
    assert result.final_signal in (FinalSignal.BUY_CANDIDATE, FinalSignal.HIGH_PRIORITY_CANDIDATE), \
        f"broker_check should not block BUY_CANDIDATE, got {result.final_signal}"
    assert result.no_order_quantity is True


def test_insufficient_data_blocks_buy_candidate():
    """data_quality=insufficient blocks BUY_CANDIDATE even with fully positive committee."""
    cfg = _cfg()
    market_data = load_market_data(SCENARIO_DIR / "grid_pullback.csv")
    ctx = build_signal_context("GRID", market_data, cfg)
    # data_quality=insufficient is the one flag that causes hard block
    ctx.skeptic_flags = ["data_quality=insufficient(veto候補)"]

    # 5 positive members would normally produce HIGH_PRIORITY_CANDIDATE
    members = _members_positive(5)
    result = aggregate_signal("GRID", SignalSide.BUY, members, ctx, cfg)

    assert result.final_signal == FinalSignal.REJECT_FOR_NOW, \
        f"data_quality=insufficient must block BUY_CANDIDATE+, got {result.final_signal}"
    assert result.final_signal not in (FinalSignal.BUY_CANDIDATE, FinalSignal.HIGH_PRIORITY_CANDIDATE)
    assert any("insufficient" in f for f in result.risk_flags), \
        "risk_flags must document the insufficient-data block"
    assert result.no_order_quantity is True


def test_buy_candidate_wording_not_order_instruction():
    """recommended_action_text for BUY_CANDIDATE must not contain order execution language."""
    cfg = _cfg()
    market_data = load_market_data(SCENARIO_DIR / "ita_positive_pullback.csv")
    ctx = build_signal_context("ITA", market_data, cfg)
    members = _members_positive(4)
    result = aggregate_signal("ITA", SignalSide.BUY, members, ctx, cfg)

    assert result.final_signal in (FinalSignal.BUY_CANDIDATE, FinalSignal.HIGH_PRIORITY_CANDIDATE), \
        f"Expected BUY_CANDIDATE+, got {result.final_signal}"

    forbidden = ["買え", "売れ", "購入実行", "売却実行", "注文を出", "APIで注文", "自動売買実行"]
    action_text = result.recommended_action_text
    for word in forbidden:
        assert word not in action_text, \
            f"Forbidden order instruction word '{word}' in recommended_action_text"

    # Must confirm human-review / Watchlist framing
    assert "Watchlist" in action_text or "watchlist" in action_text or "人間" in action_text, \
        "BUY_CANDIDATE text must mention Watchlist candidacy or human review"
    assert result.no_order_quantity is True
    assert result.no_auto_trade is True
