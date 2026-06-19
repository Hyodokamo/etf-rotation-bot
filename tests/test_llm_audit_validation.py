"""Tests for AI audit adjustment invalidation logic."""
import json
from unittest.mock import MagicMock

import pytest

from src.llm_auditor import _invalidate_excess_adjustments, run_audit
from src.schemas import AuditStatus, LlmAuditResult


def _make_result(**kwargs) -> LlmAuditResult:
    defaults = {"status": AuditStatus.PASS, "summary": "all good", "apply_adjustment": False}
    defaults.update(kwargs)
    return LlmAuditResult.model_validate(defaults)


# ── _invalidate_excess_adjustments ──────────────────────────────────────────


def test_adjustment_within_limit_stays_valid():
    result = _make_result(
        adjustments=[{"ticker": "VOO", "current_weight": 0.25, "suggested_weight": 0.27, "reason": "up"}]
    )
    warnings = _invalidate_excess_adjustments(result)
    assert warnings == []
    assert result.adjustments[0].valid is True
    assert result.adjustments_invalidated is False


def test_adjustment_at_boundary_stays_valid():
    result = _make_result(
        adjustments=[{"ticker": "VOO", "current_weight": 0.25, "suggested_weight": 0.30, "reason": "5%"}]
    )
    warnings = _invalidate_excess_adjustments(result)
    assert warnings == []
    assert result.adjustments[0].valid is True


def test_adjustment_exceeds_limit_is_invalidated():
    result = _make_result(
        adjustments=[{"ticker": "SGOV", "current_weight": 0.50, "suggested_weight": 0.25, "reason": "reduce"}]
    )
    warnings = _invalidate_excess_adjustments(result)
    assert any("SGOV" in w for w in warnings)
    assert result.adjustments[0].valid is False
    assert result.adjustments_invalidated is True


def test_invalidation_warnings_stored_in_result():
    result = _make_result(
        adjustments=[{"ticker": "VOO", "current_weight": 0.15, "suggested_weight": 0.40, "reason": "up"}]
    )
    _invalidate_excess_adjustments(result)
    assert len(result.validation_warnings) == 1
    assert "VOO" in result.validation_warnings[0]


def test_mixed_adjustments_partial_invalidation():
    result = _make_result(
        adjustments=[
            {"ticker": "VOO", "current_weight": 0.25, "suggested_weight": 0.27, "reason": "small"},
            {"ticker": "SGOV", "current_weight": 0.50, "suggested_weight": 0.20, "reason": "big"},
        ]
    )
    _invalidate_excess_adjustments(result)
    assert result.adjustments[0].valid is True   # VOO: 2% delta
    assert result.adjustments[1].valid is False  # SGOV: 30% delta
    assert result.adjustments_invalidated is True
    assert len(result.validation_warnings) == 1


def test_no_adjustments_no_invalidation():
    result = _make_result()
    warnings = _invalidate_excess_adjustments(result)
    assert warnings == []
    assert result.adjustments_invalidated is False


# ── run_audit integration ────────────────────────────────────────────────────


def test_audit_succeeds_with_invalid_adjustments():
    """Audit must return a result (not None) even when adjustments are invalidated."""
    payload = json.dumps({
        "status": "PASS_WITH_CAUTION",
        "summary": "some concern",
        "adjustments": [
            {"ticker": "VOO", "current_weight": 0.50, "suggested_weight": 0.20, "reason": "reduce"}
        ],
        "apply_adjustment": False,
    })
    client = MagicMock()
    client.complete.return_value = payload
    result = run_audit(context={}, weights={"VOO": 0.50, "TLT": 0.50}, client=client)
    assert result is not None
    assert result.status.value == "PASS_WITH_CAUTION"
    assert result.adjustments[0].valid is False
    assert result.adjustments_invalidated is True


def test_audit_status_preserved_despite_invalidation():
    payload = json.dumps({
        "status": "REVIEW_REQUIRED",
        "summary": "重大な懸念",
        "adjustments": [
            {"ticker": "VOO", "current_weight": 0.15, "suggested_weight": 0.60, "reason": "huge increase"}
        ],
        "apply_adjustment": False,
    })
    client = MagicMock()
    client.complete.return_value = payload
    result = run_audit(context={}, weights={"VOO": 0.15}, client=client)
    assert result is not None
    assert result.status.value == "REVIEW_REQUIRED"  # status unchanged


def test_apply_adjustment_always_false():
    payload = json.dumps({"status": "PASS", "summary": "ok", "apply_adjustment": True})
    client = MagicMock()
    client.complete.return_value = payload
    result = run_audit(context={}, weights={}, client=client)
    assert result is not None
    assert result.apply_adjustment is False


def test_final_allocation_not_modified_by_audit():
    """run_audit must not touch the weights dict."""
    original = {"VOO": 0.50, "TLT": 0.50}
    payload = json.dumps({
        "status": "REJECT",
        "summary": "bad",
        "adjustments": [
            {"ticker": "VOO", "current_weight": 0.50, "suggested_weight": 0.10, "reason": "cut"}
        ],
        "apply_adjustment": False,
    })
    client = MagicMock()
    client.complete.return_value = payload
    weights = dict(original)
    run_audit(context={}, weights=weights, client=client)
    assert weights == original
