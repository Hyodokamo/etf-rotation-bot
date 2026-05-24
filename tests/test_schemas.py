import pytest
from pydantic import ValidationError

from src.schemas import (
    AdjustmentAction,
    AuditStatus,
    LlmAuditResult,
    NisaSuitabilityCheck,
    PreTradeCheck,
)


def test_audit_status_values():
    assert AuditStatus.PASS == "PASS"
    assert AuditStatus.PASS_WITH_CAUTION == "PASS_WITH_CAUTION"
    assert AuditStatus.REVIEW_REQUIRED == "REVIEW_REQUIRED"
    assert AuditStatus.REJECT == "REJECT"


def test_llm_audit_result_defaults():
    result = LlmAuditResult(status=AuditStatus.PASS, summary="all good")
    assert result.apply_adjustment is False
    assert result.adjustments == []
    assert result.pre_trade_checks == []
    assert result.nisa_checks == []
    assert result.raw_response == ""


def test_llm_audit_result_with_adjustment():
    adj = AdjustmentAction(
        ticker="VOO",
        current_weight=0.25,
        suggested_weight=0.20,
        reason="too concentrated",
    )
    result = LlmAuditResult(
        status=AuditStatus.PASS_WITH_CAUTION,
        summary="minor concern",
        adjustments=[adj],
    )
    assert len(result.adjustments) == 1
    assert result.adjustments[0].ticker == "VOO"


def test_llm_audit_result_model_validate():
    data = {
        "status": "PASS",
        "summary": "portfolio is fine",
        "adjustments": [],
        "pre_trade_checks": [
            {"check_id": "cap_check", "result": "PASS", "description": "within caps", "value": None}
        ],
        "nisa_checks": [],
        "apply_adjustment": False,
    }
    result = LlmAuditResult.model_validate(data)
    assert result.status == AuditStatus.PASS
    assert len(result.pre_trade_checks) == 1


def test_apply_adjustment_forced_false():
    data = {
        "status": "PASS",
        "summary": "ok",
        "apply_adjustment": True,
    }
    result = LlmAuditResult.model_validate(data)
    # apply_adjustment may be True from model, but run_audit() forces it False
    # here we just verify parsing works regardless
    assert result.status == AuditStatus.PASS


def test_nisa_check_model():
    nc = NisaSuitabilityCheck(ticker="TLT", is_nisa_suitable=True, reason="eligible ETF")
    assert nc.ticker == "TLT"
    assert nc.is_nisa_suitable is True


def test_pre_trade_check_model():
    chk = PreTradeCheck(check_id="vol_check", result="WARN", description="high vol", value=0.35)
    assert chk.value == pytest.approx(0.35)
    assert chk.result == "WARN"


def test_pre_trade_check_value_none():
    chk = PreTradeCheck(check_id="c", result="PASS", description="d", value=None)
    assert chk.value is None


def test_pre_trade_check_value_dict_coerced():
    """LLM sometimes returns a dict for value — first numeric value is extracted."""
    chk = PreTradeCheck(
        check_id="c", result="PASS", description="d",
        value={"max_asset_weight": 0.50, "core_equity": 0.15},
    )
    assert chk.value == pytest.approx(0.50)


def test_pre_trade_check_value_dict_no_numeric_returns_none():
    chk = PreTradeCheck(
        check_id="c", result="PASS", description="d",
        value={"asset_over_limit": True, "category_over_limit": False},
    )
    assert chk.value is None


def test_pre_trade_check_value_string_coerced():
    chk = PreTradeCheck(check_id="c", result="PASS", description="d", value="0.42")
    assert chk.value == pytest.approx(0.42)
