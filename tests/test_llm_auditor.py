import json

import pytest

from src.llm_auditor import (
    _check_forbidden_patterns,
    _extract_json,
    _validate_result,
)
from src.schemas import AdjustmentAction, AuditStatus, LlmAuditResult


# --- _extract_json ---

def test_extract_json_direct():
    data = {"status": "PASS", "summary": "ok"}
    result = _extract_json(json.dumps(data))
    assert result == data


def test_extract_json_from_code_block():
    text = '```json\n{"status": "PASS", "summary": "ok"}\n```'
    result = _extract_json(text)
    assert result["status"] == "PASS"


def test_extract_json_from_code_block_no_lang():
    text = '```\n{"status": "PASS"}\n```'
    result = _extract_json(text)
    assert result["status"] == "PASS"


def test_extract_json_first_brace():
    text = 'Some preamble text {"status": "PASS", "summary": "hi"} trailing'
    result = _extract_json(text)
    assert result["status"] == "PASS"


def test_extract_json_raises_on_invalid():
    with pytest.raises(ValueError):
        _extract_json("no json here at all")


# --- _check_forbidden_patterns ---

def test_no_forbidden_patterns_clean_text():
    violations = _check_forbidden_patterns("ポートフォリオは良好です。監査を完了しました。")
    assert violations == []


def test_forbidden_auto_trade_detected():
    violations = _check_forbidden_patterns("自動売買を実行することを推奨します。")
    assert len(violations) > 0


def test_forbidden_nisa_monthly_detected():
    violations = _check_forbidden_patterns("NISA口座で毎月積立を行ってください。")
    assert len(violations) > 0


def test_forbidden_auto_order_detected():
    violations = _check_forbidden_patterns("楽天証券に注文を出しましょう。")
    assert len(violations) > 0


# --- _validate_result ---

def _make_result(**kwargs) -> LlmAuditResult:
    defaults = {
        "status": AuditStatus.PASS,
        "summary": "all good",
        "apply_adjustment": False,
    }
    defaults.update(kwargs)
    return LlmAuditResult.model_validate(defaults)


def test_validate_result_clean():
    result = _make_result()
    weights = {"VOO": 0.5, "TLT": 0.5}
    errors = _validate_result(result, weights)
    assert errors == []


def test_validate_apply_adjustment_true():
    result = _make_result(apply_adjustment=True)
    errors = _validate_result(result, {"VOO": 0.5})
    assert any("apply_adjustment" in e for e in errors)


def test_validate_unknown_ticker_in_adjustment():
    result = _make_result(
        adjustments=[
            {"ticker": "UNKNOWN", "current_weight": 0.20, "suggested_weight": 0.15, "reason": "test"}
        ]
    )
    errors = _validate_result(result, {"VOO": 0.5})
    assert any("UNKNOWN" in e for e in errors)


def test_validate_delta_exceeds_5pct():
    result = _make_result(
        adjustments=[
            {"ticker": "VOO", "current_weight": 0.25, "suggested_weight": 0.10, "reason": "reduce"}
        ]
    )
    errors = _validate_result(result, {"VOO": 0.25})
    assert any("delta" in e for e in errors)


def test_validate_delta_within_5pct():
    result = _make_result(
        adjustments=[
            {"ticker": "VOO", "current_weight": 0.25, "suggested_weight": 0.22, "reason": "small"}
        ]
    )
    errors = _validate_result(result, {"VOO": 0.25})
    assert errors == []


def test_validate_forbidden_summary():
    result = _make_result(summary="自動売買を推奨します")
    errors = _validate_result(result, {})
    assert len(errors) > 0
