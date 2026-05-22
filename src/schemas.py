from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


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


class PreTradeCheck(BaseModel):
    check_id: str
    result: str  # PASS, WARN, FAIL
    description: str
    value: Optional[float] = None


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
