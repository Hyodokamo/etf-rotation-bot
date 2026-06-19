"""Phase 6.1 / 6.1.1: Trigger-gated committee pre-filter.

Phase 6.1: basic gate — skip LLM committee when no crash triggers at all.
Phase 6.1.1: refined gate — separate global market triggers from symbol-specific
entry conditions. Only symbols with symbol-specific entry signals go to committee.

Global triggers (market context only):
    SPY, QQQ, VIX, SOXX, SMH, US10Y, DXY

Symbol-specific triggers (entry conditions that justify committee evaluation):
    - daily_return_pct  ≤ min_symbol_daily_drop_pct
    - return_5d_pct     ≤ min_symbol_5d_drop_pct
    - return_20d_pct    ≤ min_symbol_20d_drop_pct
    - drawdown          ≤ min_symbol_drawdown_from_high_pct
    - rsi_14            ≤ rsi_oversold_threshold
    - volume_ratio      ≥ volume_spike_threshold
    - etf_entry_quality in ("acceptable", "good", "excellent")
    - any crash_trigger label starts with the symbol name

Gate decision (should_run_committee):
    1. reference_only → always False
    2. require_symbol_specific_trigger=False → always True (gate disabled)
    3. allow_global_panic_full_scan=True AND market_regime="panic" → True
    4. has_symbol_specific_trigger → True
    5. otherwise → False (global-only context: skip committee, return WATCH/NO_ACTION)

Safety invariants:
    - no_order_quantity / no_auto_trade always True on all returned results
    - watchlist_update always None for gate-skipped results
    - No file writes
"""
from __future__ import annotations

from src.signals.signal_models import FinalSignal, SignalContext, SignalResult, SignalSide

# Prefixes that identify global market triggers (not symbol-specific)
_GLOBAL_TRIGGER_PREFIXES: frozenset[str] = frozenset({
    "SPY", "QQQ", "VIX", "SOXX", "SMH", "US10Y", "DXY",
})


# ── Trigger classification ─────────────────────────────────────────────────────

def has_global_market_trigger(ctx: SignalContext) -> bool:
    """Return True if any crash trigger in ctx is from a global market symbol."""
    return any(
        any(t.startswith(prefix) for prefix in _GLOBAL_TRIGGER_PREFIXES)
        for t in ctx.crash_triggers
    )


def has_symbol_specific_trigger(ctx: SignalContext, gate_config) -> bool:
    """Return True if the symbol itself has entry-zone conditions.

    Checks (in order):
    1. Symbol name appears in ctx.crash_triggers (from crash_detector per-symbol check)
    2. Quantitative conditions in ctx.market_data vs gate_config thresholds
    3. etf_entry_quality is "acceptable", "good", or "excellent" (not "too_early")

    'too_early' means the ETF is extended/overbought — not a valid entry.
    """
    symbol = ctx.symbol.upper()

    # 1. Symbol-specific label from crash_detector
    for t in ctx.crash_triggers:
        if t.startswith(symbol) and symbol not in _GLOBAL_TRIGGER_PREFIXES:
            return True

    # 2. Quantitative entry conditions from market_data
    md = ctx.market_data
    if md:
        from src.signals.crash_detector import _num

        daily = _num(md, "daily_return_pct")
        if daily is not None and daily <= gate_config.min_symbol_daily_drop_pct:
            return True

        ret5d = _num(md, "return_5d_pct")
        if ret5d is not None and ret5d <= gate_config.min_symbol_5d_drop_pct:
            return True

        ret20d = _num(md, "return_20d_pct")
        if ret20d is not None and ret20d <= gate_config.min_symbol_20d_drop_pct:
            return True

        dd = _num(md, "drawdown_from_52w_high_pct")
        if dd is not None and dd <= gate_config.min_symbol_drawdown_from_high_pct:
            return True

        rsi = _num(md, "rsi_14")
        if rsi is not None and rsi <= gate_config.rsi_oversold_threshold:
            return True

        vol = _num(md, "volume_ratio")
        if vol is not None and vol >= gate_config.volume_spike_threshold:
            return True

    # 3. Entry quality: "acceptable" or better (not "too_early" = extended/overbought)
    if ctx.etf_entry_quality in ("acceptable", "good", "excellent"):
        return True

    return False


def should_run_committee(ctx: SignalContext, gate_config) -> bool:
    """Decide whether the LLM committee should run for this symbol.

    Rules (in priority order):
    1. reference_only → False (market reference symbols never become candidates)
    2. require_symbol_specific_trigger=False → True (gate explicitly disabled)
    3. allow_global_panic_full_scan=True AND market_regime="panic" → True
    4. has_symbol_specific_trigger → True
    5. default → False (global market triggers only, no symbol-specific entry)
    """
    if ctx.reference_only:
        return False

    if not gate_config.require_symbol_specific_trigger:
        return True  # gate disabled in config

    # Panic exception (opt-in via config)
    if gate_config.allow_global_panic_full_scan and ctx.market_regime == "panic":
        return True

    return has_symbol_specific_trigger(ctx, gate_config)


# ── Phase 6.1: backward-compatible basic gate ──────────────────────────────────

def symbol_has_triggers(ctx: SignalContext) -> bool:
    """Return True if the signal context has any crash/correction triggers.

    Phase 6.1 basic gate. Kept for backward compatibility.
    Prefer should_run_committee() for Phase 6.1.1+ logic.
    """
    return bool(ctx.crash_triggers)


# ── Result builders ───────────────────────────────────────────────────────────

def make_deterministic_no_action(
    symbol: str,
    signal_side: SignalSide,
    ctx: SignalContext,
) -> SignalResult:
    """Build a NO_ACTION result without running the LLM committee.

    Used when there are no triggers at all (neither global nor symbol-specific).
    Kept for backward compatibility — prefer make_deterministic_global_only().
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


def make_deterministic_global_only(
    symbol: str,
    signal_side: SignalSide,
    ctx: SignalContext,
) -> SignalResult:
    """Build a WATCH/NO_ACTION result when committee is skipped.

    Phase 6.1.1: used when:
    - Global market triggers exist but symbol has no entry-specific conditions
      → risk_flag = "global_market_trigger_only", final_signal = WATCH
    - No triggers at all
      → risk_flag = "committee_skipped_no_trigger", final_signal = NO_ACTION

    No LLM calls. watchlist_update=None. no_order_quantity/no_auto_trade always True.
    """
    has_global = has_global_market_trigger(ctx)

    if has_global:
        final_signal = FinalSignal.WATCH
        risk_flag = "global_market_trigger_only"
        action_text = (
            f"市場全体トリガーあり（{ctx.market_regime}）。"
            "ただし個別ETFの急落条件未達のため、AI委員会評価対象外。"
            "通常監視継続。自動売買は行いません。最終判断は人間が行います。"
        )
        trigger_labels = list(ctx.crash_triggers)
    else:
        final_signal = FinalSignal.NO_ACTION
        risk_flag = "committee_skipped_no_trigger"
        action_text = (
            "急落トリガーなし。本日のCommittee評価対象外。"
            "自動売買は行いません。最終判断は人間が行います。"
        )
        trigger_labels = []

    return SignalResult(
        symbol=symbol,
        signal_side=signal_side,
        final_signal=final_signal,
        confidence=0.0,
        total_score=0,
        positive_member_count=0,
        cautious_member_count=0,
        reject_member_count=0,
        veto_count=0,
        trigger_labels=trigger_labels,
        positive_reasons=[],
        risk_flags=[risk_flag],
        veto_flags=[],
        dissenting_views=[],
        recommended_action_text=action_text,
        watchlist_update=None,
        next_review_date=None,
        member_outputs=[],
    )
