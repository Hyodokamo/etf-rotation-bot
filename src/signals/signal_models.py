"""Phase 5.1: signal models.

Safety constants:
- no_order_quantity / no_auto_trade are always True on SignalResult.
- sell_signal_reserved is always True (SELL not implemented in MVP).
- SELL signal_side returns NO_ACTION immediately in the engine.
- core_manual / satellite_legacy / existing_related_holdings are never SELL targets.
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field, field_validator


class SignalSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"         # reserved — not implemented in MVP
    HOLD = "HOLD"
    RISK_REVIEW = "RISK_REVIEW"


class FinalSignal(str, Enum):
    NO_ACTION = "NO_ACTION"
    WATCH = "WATCH"
    BUY_CANDIDATE = "BUY_CANDIDATE"
    HIGH_PRIORITY_CANDIDATE = "HIGH_PRIORITY_CANDIDATE"
    HOLD_OFF = "HOLD_OFF"
    REJECT_FOR_NOW = "REJECT_FOR_NOW"


class MemberStance(str, Enum):
    POSITIVE = "positive"
    NEUTRAL = "neutral"
    CAUTIOUS = "cautious"
    REJECT = "reject"


class SignalMemberOutput(BaseModel):
    """Output from one committee member for signal evaluation.

    Scores: +2=strong buy / +1=mild buy / 0=neutral / -1=cautious / -2=reject.
    veto=True blocks BUY_CANDIDATE or higher (engine rule).
    """

    member_id: str
    stance: MemberStance = MemberStance.NEUTRAL
    score: int = 0
    confidence: float = 0.0
    rationale: str = ""
    dissenting_view: str = ""
    risk_flags: list[str] = Field(default_factory=list)
    veto: bool = False
    next_review_triggers: list[str] = Field(default_factory=list)

    @field_validator("score", mode="before")
    @classmethod
    def _clamp_score(cls, v):
        try:
            return max(-2, min(2, int(v)))
        except (TypeError, ValueError):
            return 0

    @field_validator("confidence", mode="before")
    @classmethod
    def _clamp_conf(cls, v):
        try:
            return round(max(0.0, min(1.0, float(v))), 4)
        except (TypeError, ValueError):
            return 0.0


class SignalContext(BaseModel):
    """Deterministic analysis context for one ETF signal evaluation."""

    symbol: str
    signal_side: SignalSide = SignalSide.BUY
    market_regime: str = "neutral"          # risk_on/neutral/correction/crash/panic
    semiconductor_stress: str = "normal"    # normal/healthy_pullback/macro_selloff/earnings_warning/structural_risk
    etf_entry_quality: str = "acceptable"   # too_early/acceptable/good/excellent
    portfolio_risk: str = "low"             # low/medium/high
    theme_conviction: str = "moderate"      # weak/moderate/strong
    skeptic_flags: list[str] = Field(default_factory=list)
    crash_triggers: list[str] = Field(default_factory=list)
    etf_master_metadata: dict = Field(default_factory=dict)
    portfolio_context_summary: dict = Field(default_factory=dict)
    market_data: dict = Field(default_factory=dict)


class SignalResult(BaseModel):
    """Aggregated result after committee evaluation.

    Invariants always True: no_order_quantity, no_auto_trade, sell_signal_reserved.
    watchlist_update is the status string to set in watchlist.csv, or None if no update.
    """

    symbol: str
    signal_side: SignalSide
    final_signal: FinalSignal
    confidence: float = 0.0
    total_score: int = 0
    positive_member_count: int = 0
    cautious_member_count: int = 0
    reject_member_count: int = 0
    veto_count: int = 0
    trigger_labels: list[str] = Field(default_factory=list)
    positive_reasons: list[str] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)
    veto_flags: list[str] = Field(default_factory=list)
    dissenting_views: list[str] = Field(default_factory=list)
    recommended_action_text: str = ""
    watchlist_update: str | None = None
    next_review_date: str | None = None
    no_order_quantity: bool = True
    no_auto_trade: bool = True
    sell_signal_reserved: bool = True
    member_outputs: list[SignalMemberOutput] = Field(default_factory=list)

    @field_validator("no_order_quantity", mode="before")
    @classmethod
    def _force_no_order_qty(cls, v):
        return True

    @field_validator("no_auto_trade", mode="before")
    @classmethod
    def _force_no_auto_trade(cls, v):
        return True

    @field_validator("sell_signal_reserved", mode="before")
    @classmethod
    def _force_sell_reserved(cls, v):
        return True
