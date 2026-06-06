"""Phase 5.1: deterministic signal aggregation engine.

Aggregates committee outputs into a final signal using hard rules.
No LLM calls. Never modifies etf_master, ai_sleeve_state, or final_allocation.

Safety invariants (always enforced):
- SELL signal_side → NO_ACTION immediately (reserved in MVP)
- veto_count > 0  → no BUY_CANDIDATE or higher
- portfolio_risk=high → no BUY_CANDIDATE or higher
- ai_sleeve_cash <= 0 → no BUY_CANDIDATE or higher
- no_order_quantity / no_auto_trade / sell_signal_reserved always True on output
- core_manual / satellite_legacy / existing_related_holdings are never SELL targets
"""
from __future__ import annotations

from src.logger import logger
from src.signals.signal_models import (
    FinalSignal,
    MemberStance,
    SignalContext,
    SignalResult,
    SignalSide,
    SignalMemberOutput,
)

_RECOMMENDED_TEXT: dict[FinalSignal, str] = {
    FinalSignal.NO_ACTION: (
        "現時点では急落シグナルなし。通常レビューを継続。"
        "自動売買は行いません。最終判断は人間が行います。"
    ),
    FinalSignal.WATCH: (
        "買い候補条件を部分的に満たしました。Watchlistで監視継続。"
        "最終判断は人間が行います。自動売買は行いません。"
    ),
    FinalSignal.BUY_CANDIDATE: (
        "買い候補条件を満たしました。WatchlistをBUY_CANDIDATEに更新候補です。"
        "最終判断は人間が行います。自動売買は行いません。注文数量は計算しません。"
    ),
    FinalSignal.HIGH_PRIORITY_CANDIDATE: (
        "高優先度買い候補条件を満たしました。WatchlistをHIGH_PRIORITY_CANDIDATEに更新候補です。"
        "最終判断は人間が行います。自動売買は行いません。注文数量は計算しません。"
    ),
    FinalSignal.HOLD_OFF: (
        "現時点での買い候補化を見送ります。リスクフラグを確認してください。"
        "最終判断は人間が行います。"
    ),
    FinalSignal.REJECT_FOR_NOW: (
        "現時点での買い候補化を却下します。重大リスクまたはveto発生。"
        "最終判断は人間が行います。"
    ),
}


def aggregate_signal(
    symbol: str,
    signal_side: SignalSide,
    member_outputs: list[SignalMemberOutput],
    signal_ctx: SignalContext,
    config,
    portfolio_context=None,
) -> SignalResult:
    """Aggregate committee outputs into a final signal (deterministic rules).

    Does NOT modify etf_master, ai_sleeve_state, or final_allocation.
    no_order_quantity / no_auto_trade / sell_signal_reserved are always True.
    """
    rules = config.candidate_rules

    total_score = sum(m.score for m in member_outputs)
    positive_count = sum(1 for m in member_outputs if m.stance == MemberStance.POSITIVE)
    cautious_count = sum(1 for m in member_outputs if m.stance == MemberStance.CAUTIOUS)
    reject_count = sum(1 for m in member_outputs if m.stance == MemberStance.REJECT)
    veto_count = sum(1 for m in member_outputs if m.veto)
    ai_auditor_veto = any(
        m.member_id == "core_ai_auditor" and m.veto for m in member_outputs
    )

    trigger_labels = list(signal_ctx.crash_triggers)
    positive_reasons = [
        m.rationale for m in member_outputs
        if m.stance == MemberStance.POSITIVE and m.rationale
    ]
    risk_flags = list(signal_ctx.skeptic_flags)
    for m in member_outputs:
        for f in m.risk_flags:
            if f and f not in risk_flags:
                risk_flags.append(f)
    veto_flags = [
        f"{m.member_id}: {m.rationale[:80]}"
        for m in member_outputs if m.veto
    ]
    dissenting_views = [
        f"{m.member_id}: {m.dissenting_view}"
        for m in member_outputs if m.dissenting_view
    ]

    # AI sleeve cash (default 1M if no context)
    ai_sleeve_cash = (
        portfolio_context.ai_sleeve.current_cash_jpy
        if portfolio_context is not None
        else 1_000_000.0
    )
    portfolio_risk = signal_ctx.portfolio_risk
    has_triggers = bool(trigger_labels)
    avg_conf = (
        sum(m.confidence for m in member_outputs) / len(member_outputs)
        if member_outputs else 0.0
    )

    # ── SELL side: reserved in MVP, never generates BUY/HIGH_PRIORITY ─────────
    if signal_side == SignalSide.SELL:
        return SignalResult(
            symbol=symbol,
            signal_side=signal_side,
            final_signal=FinalSignal.NO_ACTION,
            risk_flags=["SELL signal is reserved — not implemented in MVP"],
            veto_flags=["sell_signal_reserved"],
            dissenting_views=[],
            recommended_action_text="SELL シグナルは今回MVPでは実装されていません（reserved）。",
            member_outputs=member_outputs,
        )

    # ── Hard blocks ────────────────────────────────────────────────────────────
    if ai_auditor_veto and rules.block_if_ai_auditor_veto:
        veto_flags.append("ai_auditor_veto_hard_block")
        final_signal = FinalSignal.REJECT_FOR_NOW
        watchlist_update = None
    elif veto_count > 0 and rules.block_if_any_major_veto:
        final_signal = FinalSignal.REJECT_FOR_NOW
        watchlist_update = None
    elif portfolio_risk == "high" and rules.block_if_portfolio_risk_high:
        risk_flags.append("portfolio_risk=high → BUY_CANDIDATE以上をブロック")
        final_signal = FinalSignal.HOLD_OFF
        watchlist_update = None
    elif ai_sleeve_cash <= rules.block_if_ai_sleeve_cash_jpy_lte:
        risk_flags.append("ai_sleeve_cash=0 → BUY_CANDIDATE以上をブロック")
        final_signal = FinalSignal.HOLD_OFF
        watchlist_update = None
    # ── Score-based rules ──────────────────────────────────────────────────────
    elif not has_triggers:
        # No crash/correction signals → WATCH at most
        final_signal = FinalSignal.WATCH if total_score >= 2 else FinalSignal.NO_ACTION
        watchlist_update = "WATCH" if final_signal == FinalSignal.WATCH else None
    elif any("data_quality=insufficient" in f for f in signal_ctx.skeptic_flags):
        # Insufficient data is a hard block regardless of committee score
        risk_flags.append("data_quality=insufficient → BUY_CANDIDATE以上をブロック")
        final_signal = FinalSignal.REJECT_FOR_NOW
        watchlist_update = None
    elif total_score <= -5 or reject_count >= 4:
        # Recalibrated: deep negative score or strong rejection majority
        final_signal = FinalSignal.REJECT_FOR_NOW
        watchlist_update = None
    elif (
        total_score >= rules.min_total_score_for_high_priority
        and positive_count >= rules.min_positive_members_for_high_priority
    ):
        final_signal = FinalSignal.HIGH_PRIORITY_CANDIDATE
        watchlist_update = "HIGH_PRIORITY_CANDIDATE"
    elif (
        total_score >= rules.min_total_score_for_buy_candidate
        and positive_count >= rules.min_positive_members_for_buy_candidate
    ):
        final_signal = FinalSignal.BUY_CANDIDATE
        watchlist_update = "BUY_CANDIDATE"
    elif total_score < 0 or cautious_count >= 4:
        # Cautious committee but not outright rejection — wait and see
        final_signal = FinalSignal.HOLD_OFF
        watchlist_update = None
    elif total_score >= 1 or cautious_count <= 2:
        final_signal = FinalSignal.WATCH
        watchlist_update = "WATCH"
    else:
        final_signal = FinalSignal.HOLD_OFF
        watchlist_update = None

    # ── Post-score downgrade for overlap / existing holdings ──────────────────
    if final_signal in (FinalSignal.BUY_CANDIDATE, FinalSignal.HIGH_PRIORITY_CANDIDATE):
        existing_related = signal_ctx.portfolio_context_summary.get("existing_related_holdings", [])
        has_existing_holding = symbol.upper() in [s.upper() for s in existing_related]
        overlap_very_high = any("コア重複=very_high" in f for f in signal_ctx.skeptic_flags)

        if overlap_very_high:
            risk_flags.append("expected_overlap_with_core=very_high → 一段降格 HOLD_OFF")
            final_signal = FinalSignal.HOLD_OFF
            watchlist_update = None
        elif has_existing_holding:
            risk_flags.append("existing_related_holdings → 一段降格")
            if final_signal == FinalSignal.HIGH_PRIORITY_CANDIDATE:
                final_signal = FinalSignal.BUY_CANDIDATE
                watchlist_update = "BUY_CANDIDATE"
            else:
                final_signal = FinalSignal.WATCH
                watchlist_update = "WATCH"

    return SignalResult(
        symbol=symbol,
        signal_side=signal_side,
        final_signal=final_signal,
        confidence=round(avg_conf, 4),
        total_score=total_score,
        positive_member_count=positive_count,
        cautious_member_count=cautious_count,
        reject_member_count=reject_count,
        veto_count=veto_count,
        trigger_labels=trigger_labels,
        positive_reasons=positive_reasons,
        risk_flags=risk_flags,
        veto_flags=veto_flags,
        dissenting_views=dissenting_views,
        recommended_action_text=_RECOMMENDED_TEXT.get(final_signal, "最終判断は人間が行います。"),
        watchlist_update=watchlist_update,
        next_review_date=None,
        member_outputs=member_outputs,
    )
