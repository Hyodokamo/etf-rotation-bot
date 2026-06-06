"""Phase 6.1: Trigger-gated committee pre-filter.

Checks per-symbol crash triggers before running the LLM committee.
Purely deterministic — no LLM calls.

When committee_on_trigger_only=True in the crash signal handler:
- Symbols WITH crash triggers  → run LLM committee (committee_target_count++)
- Symbols WITHOUT any triggers → skip committee, return deterministic NO_ACTION (skipped_count++)

This reduces daily run time and API cost during normal (no-crash) markets.

Safety invariants:
- no_order_quantity / no_auto_trade always True
- watchlist_update is always None for skipped symbols
- No file writes
"""
from __future__ import annotations

from src.signals.signal_models import FinalSignal, SignalContext, SignalResult, SignalSide


def symbol_has_triggers(ctx: SignalContext) -> bool:
    """Return True if the signal context has any crash/correction triggers."""
    return bool(ctx.crash_triggers)


def make_deterministic_no_action(
    symbol: str,
    signal_side: SignalSide,
    ctx: SignalContext,
) -> SignalResult:
    """Build a NO_ACTION SignalResult without running the LLM committee.

    Used when committee_on_trigger_only=True and the symbol has no triggers.
    No LLM calls. watchlist_update=None (watchlist unchanged).
    no_order_quantity / no_auto_trade always True.
    """
    return SignalResult(
        symbol=symbol,
        signal_side=signal_side,
        final_signal=FinalSignal.NO_ACTION,
        confidence=0.0,
        total_score=0,
        positive_member_count=0,
        cautious_member_count=0,
        reject_member_count=0,
        veto_count=0,
        trigger_labels=[],
        positive_reasons=[],
        risk_flags=["committee_skipped_no_trigger"],
        veto_flags=[],
        dissenting_views=[],
        recommended_action_text=(
            "急落トリガーなし。本日のCommittee評価対象外。"
            "自動売買は行いません。最終判断は人間が行います。"
        ),
        watchlist_update=None,
        next_review_date=None,
        member_outputs=[],
    )
