"""Phase 5.1: signal config loader."""
from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field

DEFAULT_SIGNAL_CONFIG_PATH = "config/signal_config.yaml"


class MarketTriggerConfig(BaseModel):
    spy_daily_drop_pct: float = -2.0
    qqq_daily_drop_pct: float = -3.0
    qqq_drawdown_from_high_pct: float = -8.0
    qqq_severe_drawdown_from_high_pct: float = -15.0
    vix_stress_level: float = 25.0
    vix_panic_level: float = 35.0


class ThemeTriggerConfig(BaseModel):
    semiconductor_daily_drop_pct: float = -5.0
    semiconductor_drawdown_from_high_pct: float = -10.0
    theme_daily_drop_pct: float = -4.0
    theme_drawdown_from_high_pct: float = -10.0


class CandidateRulesConfig(BaseModel):
    min_total_score_for_buy_candidate: int = 3
    min_positive_members_for_buy_candidate: int = 3
    min_total_score_for_high_priority: int = 6
    min_positive_members_for_high_priority: int = 5
    block_if_portfolio_risk_high: bool = True
    block_if_ai_auditor_veto: bool = True
    block_if_any_major_veto: bool = True
    block_if_ai_sleeve_cash_jpy_lte: float = 0.0
    min_cooldown_hours_same_etf: int = 24
    min_cooldown_hours_same_theme: int = 12


class AiSleeveRulesConfig(BaseModel):
    max_initial_deployment_jpy: float = 250_000.0
    min_cash_buffer_ratio_after_signal: float = 0.50
    max_single_etf_watch_allocation_ratio: float = 0.25
    max_single_theme_watch_allocation_ratio: float = 0.40
    no_order_quantity: bool = True
    no_auto_trade: bool = True


class SignalsEnumConfig(BaseModel):
    allowed_signal_side: list[str] = Field(
        default_factory=lambda: ["BUY", "SELL", "HOLD", "RISK_REVIEW"]
    )
    implemented_signal_side: list[str] = Field(
        default_factory=lambda: ["BUY", "HOLD", "RISK_REVIEW"]
    )
    allowed_final_signal: list[str] = Field(
        default_factory=lambda: [
            "NO_ACTION", "WATCH", "BUY_CANDIDATE",
            "HIGH_PRIORITY_CANDIDATE", "HOLD_OFF", "REJECT_FOR_NOW",
        ]
    )


class SignalConfig(BaseModel):
    market_triggers: MarketTriggerConfig = Field(default_factory=MarketTriggerConfig)
    theme_triggers: ThemeTriggerConfig = Field(default_factory=ThemeTriggerConfig)
    candidate_rules: CandidateRulesConfig = Field(default_factory=CandidateRulesConfig)
    ai_sleeve_rules: AiSleeveRulesConfig = Field(default_factory=AiSleeveRulesConfig)
    signals: SignalsEnumConfig = Field(default_factory=SignalsEnumConfig)


def load_signal_config(
    path: str | Path = DEFAULT_SIGNAL_CONFIG_PATH,
) -> SignalConfig:
    """Load signal_config.yaml. Missing/invalid → safe defaults."""
    p = Path(path)
    if not p.exists():
        return SignalConfig()
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return SignalConfig()
    return SignalConfig.model_validate(data)
