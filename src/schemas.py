from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator


class AuditStatus(str, Enum):
    PASS = "PASS"
    PASS_WITH_CAUTION = "PASS_WITH_CAUTION"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    REJECT = "REJECT"


class AdjustmentAction(BaseModel):
    ticker: str
    current_weight: float
    suggested_weight: float
    reason: str
    valid: bool = True  # set by llm_auditor after delta check; not from LLM output


class PreTradeCheck(BaseModel):
    check_id: str
    result: str  # PASS, WARN, FAIL
    description: str
    value: Optional[Any] = None

    @field_validator("value", mode="before")
    @classmethod
    def coerce_value(cls, v: Any) -> Optional[float]:
        if v is None:
            return None
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return float(v)
        if isinstance(v, dict):
            # LLM occasionally returns a dict of metrics; use first non-bool numeric value
            for val in v.values():
                if isinstance(val, (int, float)) and not isinstance(val, bool):
                    return float(val)
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None


class NisaSuitabilityCheck(BaseModel):
    ticker: str
    is_nisa_suitable: bool
    reason: str


class LlmAuditResult(BaseModel):
    status: AuditStatus
    summary: str
    adjustments: list[AdjustmentAction] = Field(default_factory=list)
    pre_trade_checks: list[PreTradeCheck] = Field(default_factory=list)
    nisa_checks: list[NisaSuitabilityCheck] = Field(default_factory=list)
    apply_adjustment: bool = False
    raw_response: str = ""
    validation_warnings: list[str] = Field(default_factory=list)
    adjustments_invalidated: bool = False
