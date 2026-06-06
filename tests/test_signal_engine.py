"""Tests for Phase 5.1: signal_engine.py + safety invariants."""
import pytest

from src.signals.signal_config import load_signal_config
from src.signals.signal_context_builder import build_signal_context
from src.signals.signal_engine import aggregate_signal
from src.signals.signal_models import (
    FinalSignal,
    MemberStance,
    SignalMemberOutput,
    SignalResult,
    SignalSide,
)


def _cfg():
    return load_signal_config("/nonexistent/no_config.yaml")


def _ctx(symbol="GRID", triggers=None, regime="correction", port_risk="low"):
    ctx = build_signal_context(symbol, {}, _cfg())
    ctx.market_regime = regime
    ctx.portfolio_risk = port_risk
    if triggers is not None:
        ctx.crash_triggers = triggers
    return ctx


def _members(
    positive=0, cautious=0, reject=0,
    ai_auditor_veto=False,
    veto_count_extra=0,
) -> list[SignalMemberOutput]:
    from src.signals.signal_committee_runner import _SIGNAL_PERSONAS
    member_ids = list(_SIGNAL_PERSONAS.keys())
    outputs: list[SignalMemberOutput] = []
    idx = 0
    for _ in range(positive):
        outputs.append(SignalMemberOutput(
            member_id=member_ids[idx % len(member_ids)],
            stance=MemberStance.POSITIVE, score=2, confidence=0.8,
            rationale="positive test", veto=False,
        ))
        idx += 1
    for _ in range(cautious):
        outputs.append(SignalMemberOutput(
            member_id=member_ids[idx % len(member_ids)],
            stance=MemberStance.CAUTIOUS, score=-1, confidence=0.7,
            rationale="cautious test", veto=False,
        ))
        idx += 1
    for _ in range(reject):
        outputs.append(SignalMemberOutput(
            member_id=member_ids[idx % len(member_ids)],
            stance=MemberStance.REJECT, score=-2, confidence=0.8,
            rationale="reject test", veto=False,
        ))
        idx += 1
    if ai_auditor_veto:
        outputs.append(SignalMemberOutput(
            member_id="core_ai_auditor", stance=MemberStance.REJECT,
            score=-2, confidence=0.95, rationale="veto test", veto=True,
        ))
    for _ in range(veto_count_extra):
        outputs.append(SignalMemberOutput(
            member_id=member_ids[idx % len(member_ids)],
            stance=MemberStance.REJECT, score=-2, confidence=0.8,
            rationale="extra veto", veto=True,
        ))
        idx += 1
    return outputs


# ── BUY_CANDIDATE ─────────────────────────────────────────────────────────────


def test_signal_engine_buy_candidate_when_committee_positive_no_veto():
    cfg = _cfg()
    # triggers + score >= 3 + positive >= 3 + no veto
    ctx = _ctx(triggers=["QQQ急落(-3.5%)"])
    members = _members(positive=4, cautious=1, reject=0)
    result = aggregate_signal("GRID", SignalSide.BUY, members, ctx, cfg)
    assert result.final_signal == FinalSignal.BUY_CANDIDATE
    assert result.veto_count == 0
    assert result.watchlist_update == "BUY_CANDIDATE"


# ── HOLD_OFF / WATCH ──────────────────────────────────────────────────────────


def test_signal_engine_hold_off_when_marks_or_arnott_cautious():
    cfg = _cfg()
    # Triggers present but committee majority cautious → HOLD_OFF or WATCH
    ctx = _ctx(triggers=["QQQ急落(-3.5%)"])
    members = _members(positive=1, cautious=4, reject=2)
    result = aggregate_signal("GRID", SignalSide.BUY, members, ctx, cfg)
    assert result.final_signal in (FinalSignal.HOLD_OFF, FinalSignal.WATCH, FinalSignal.REJECT_FOR_NOW)
    assert result.final_signal != FinalSignal.BUY_CANDIDATE


# ── REJECT on AI Auditor veto ─────────────────────────────────────────────────


def test_signal_engine_reject_when_ai_auditor_veto():
    cfg = _cfg()
    ctx = _ctx(triggers=["QQQ急落(-3.5%)"])
    members = _members(positive=4, cautious=0, reject=0, ai_auditor_veto=True)
    result = aggregate_signal("GRID", SignalSide.BUY, members, ctx, cfg)
    assert result.final_signal == FinalSignal.REJECT_FOR_NOW
    assert result.veto_count >= 1


# ── Portfolio risk high blocks BUY ────────────────────────────────────────────


def test_signal_engine_blocks_buy_when_portfolio_risk_high():
    cfg = _cfg()
    ctx = _ctx(triggers=["QQQ急落(-3.5%)"], port_risk="high")
    members = _members(positive=5, cautious=0, reject=0)
    result = aggregate_signal("GRID", SignalSide.BUY, members, ctx, cfg)
    assert result.final_signal not in (FinalSignal.BUY_CANDIDATE, FinalSignal.HIGH_PRIORITY_CANDIDATE)
    assert result.final_signal == FinalSignal.HOLD_OFF


# ── AI sleeve cash zero blocks BUY ───────────────────────────────────────────


def test_signal_engine_blocks_buy_when_ai_sleeve_cash_zero():
    cfg = _cfg()
    ctx = _ctx(triggers=["QQQ急落(-3.5%)"])
    members = _members(positive=5)
    # Mock portfolio context with zero cash
    from unittest.mock import MagicMock
    pctx = MagicMock()
    pctx.ai_sleeve.current_cash_jpy = 0.0
    result = aggregate_signal("GRID", SignalSide.BUY, members, ctx, cfg, portfolio_context=pctx)
    assert result.final_signal not in (FinalSignal.BUY_CANDIDATE, FinalSignal.HIGH_PRIORITY_CANDIDATE)


# ── HIGH_PRIORITY_CANDIDATE ───────────────────────────────────────────────────


def test_signal_engine_high_priority_candidate():
    cfg = _cfg()
    ctx = _ctx(triggers=["QQQ急落(-3.5%)", "VIXストレス(26)"])
    # score >= 6, positive >= 5, no veto
    members = _members(positive=6, cautious=1, reject=0)
    result = aggregate_signal("GRID", SignalSide.BUY, members, ctx, cfg)
    assert result.final_signal == FinalSignal.HIGH_PRIORITY_CANDIDATE
    assert result.watchlist_update == "HIGH_PRIORITY_CANDIDATE"


# ── SELL side reserved ────────────────────────────────────────────────────────


def test_signal_engine_sell_side_reserved_but_not_implemented():
    cfg = _cfg()
    ctx = _ctx()
    members = _members(positive=6)
    result = aggregate_signal("GRID", SignalSide.SELL, members, ctx, cfg)
    assert result.final_signal == FinalSignal.NO_ACTION
    assert result.sell_signal_reserved is True
    assert any("reserved" in f.lower() or "SELL" in f for f in result.veto_flags)


def test_signal_engine_sell_never_targets_core_manual():
    """SELL signal must never reference core_manual as a sell target."""
    cfg = _cfg()
    ctx = _ctx()
    members = _members(positive=6)
    result = aggregate_signal("GRID", SignalSide.SELL, members, ctx, cfg)
    # Result must not suggest selling core_manual symbols
    assert result.final_signal == FinalSignal.NO_ACTION
    blob = result.recommended_action_text + " ".join(result.risk_flags)
    for bad_phrase in ("core_manualを売却", "SPX売却", "FANG売却"):
        assert bad_phrase not in blob


# ── No triggers → WATCH at most ──────────────────────────────────────────────


def test_signal_engine_no_triggers_yields_watch_or_no_action():
    cfg = _cfg()
    ctx = _ctx(triggers=[])  # no triggers
    members = _members(positive=5)
    result = aggregate_signal("GRID", SignalSide.BUY, members, ctx, cfg)
    assert result.final_signal in (FinalSignal.NO_ACTION, FinalSignal.WATCH)
    assert result.final_signal != FinalSignal.BUY_CANDIDATE


# ── Safety invariants ─────────────────────────────────────────────────────────


def test_signal_does_not_calculate_order_quantity():
    fields = set(SignalResult.model_fields)
    for bad in ("quantity", "shares", "order_amount", "order_quantity", "num_shares"):
        assert bad not in fields, f"Forbidden field in SignalResult: {bad}"
    # Also check in member output
    member_fields = set(SignalMemberOutput.model_fields)
    for bad in ("quantity", "shares", "order_amount"):
        assert bad not in member_fields


def test_signal_does_not_trigger_auto_trade():
    cfg = _cfg()
    ctx = _ctx()
    members = _members(positive=5)
    result = aggregate_signal("GRID", SignalSide.BUY, members, ctx, cfg)
    assert result.no_auto_trade is True
    assert result.no_order_quantity is True
    text = result.recommended_action_text
    for bad in ("自動売買実行", "注文を出す", "APIで注文"):
        assert bad not in text


def test_signal_does_not_change_final_allocation():
    """SignalResult must not have final_allocation/weights/allocation_override fields."""
    fields = set(SignalResult.model_fields)
    for bad in ("final_allocation", "weights", "allocation_override"):
        assert bad not in fields
