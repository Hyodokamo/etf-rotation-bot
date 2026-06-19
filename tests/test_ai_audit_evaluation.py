"""Tests for Phase 2.5: AI audit quality evaluation."""
import json
import tempfile
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.ai_audit_evaluation import (
    build_evaluation_markdown,
    build_quality_checks,
    save_evaluation,
)
from src.schemas import AuditStatus, LlmAuditResult


def _make_audit(**kwargs) -> LlmAuditResult:
    defaults = {"status": AuditStatus.PASS, "summary": "all good", "apply_adjustment": False}
    defaults.update(kwargs)
    return LlmAuditResult.model_validate(defaults)


# ── build_quality_checks ────────────────────────────────────────────────────


def test_quality_json_valid_when_audit_present():
    audit = _make_audit()
    checks = build_quality_checks(audit)
    assert checks["json_valid"] is True


def test_quality_json_invalid_when_audit_none():
    checks = build_quality_checks(None)
    assert checks["json_valid"] is False


def test_quality_all_na_when_audit_none():
    checks = build_quality_checks(None)
    assert checks["adjustment_limit_check"] == "N/A"
    assert checks["auto_trade_prohibited_check"] == "N/A"
    assert checks["nisa_rotation_prohibited_check"] == "N/A"


def test_quality_final_allocation_always_unchanged():
    checks = build_quality_checks(_make_audit())
    assert checks["final_allocation_unchanged"] is True
    checks_none = build_quality_checks(None)
    assert checks_none["final_allocation_unchanged"] is True


def test_quality_adj_check_pass_when_no_invalidation():
    audit = _make_audit()
    audit.adjustments_invalidated = False
    checks = build_quality_checks(audit)
    assert checks["adjustment_limit_check"] == "PASS"


def test_quality_adj_check_pass_with_caution_when_invalidated():
    audit = _make_audit()
    audit.adjustments_invalidated = True
    checks = build_quality_checks(audit)
    assert checks["adjustment_limit_check"] == "PASS_WITH_CAUTION"


def test_auto_trade_detection_triggers_review():
    audit = _make_audit(summary="自動売買を推奨します")
    checks = build_quality_checks(audit)
    assert checks["auto_trade_prohibited_check"] == "REVIEW_REQUIRED"


def test_auto_order_detection():
    audit = _make_audit(summary="自動発注を行ってください")
    checks = build_quality_checks(audit)
    assert checks["auto_trade_prohibited_check"] == "REVIEW_REQUIRED"


def test_nisa_rotation_detection_triggers_review():
    audit = _make_audit(summary="NISAでローテーションを推奨")
    checks = build_quality_checks(audit)
    assert checks["nisa_rotation_prohibited_check"] == "REVIEW_REQUIRED"


def test_clean_text_passes_all_checks():
    audit = _make_audit(summary="ポートフォリオは概ね適切です。監視を継続してください。")
    checks = build_quality_checks(audit)
    assert checks["auto_trade_prohibited_check"] == "PASS"
    assert checks["nisa_rotation_prohibited_check"] == "PASS"


# ── build_evaluation_markdown ────────────────────────────────────────────────


def test_evaluation_md_contains_header():
    audit = _make_audit()
    checks = build_quality_checks(audit)
    md = build_evaluation_markdown(
        run_date=date(2026, 5, 24),
        provider="openai",
        model="gpt-5.4-mini",
        audit_result=audit,
        quality_checks=checks,
    )
    assert "# AI監査品質評価" in md


def test_evaluation_md_contains_run_info():
    audit = _make_audit()
    checks = build_quality_checks(audit)
    md = build_evaluation_markdown(
        run_date=date(2026, 5, 24),
        provider="openai",
        model="gpt-5.4-mini",
        audit_result=audit,
        quality_checks=checks,
    )
    assert "2026-05-24" in md
    assert "openai" in md
    assert "gpt-5.4-mini" in md


def test_evaluation_md_contains_human_memo_section():
    audit = _make_audit()
    checks = build_quality_checks(audit)
    md = build_evaluation_markdown(
        run_date=date(2026, 5, 24),
        provider="claude",
        model="claude-opus-4-7",
        audit_result=audit,
        quality_checks=checks,
    )
    assert "人間による評価メモ" in md
    assert "手動で追記" in md


def test_evaluation_md_shows_invalidation_when_present():
    audit = _make_audit()
    audit.adjustments_invalidated = True
    checks = build_quality_checks(audit)
    md = build_evaluation_markdown(
        run_date=date(2026, 5, 24),
        provider="openai",
        model="gpt-5.4-mini",
        audit_result=audit,
        quality_checks=checks,
    )
    assert "PASS_WITH_CAUTION" in md


def test_evaluation_md_works_with_none_audit():
    checks = build_quality_checks(None)
    md = build_evaluation_markdown(
        run_date=date(2026, 5, 24),
        provider="openai",
        model="gpt-5.4-mini",
        audit_result=None,
        quality_checks=checks,
    )
    assert "監査失敗" in md or "N/A" in md


# ── save_evaluation ──────────────────────────────────────────────────────────


def test_save_evaluation_creates_file():
    with tempfile.TemporaryDirectory() as tmp:
        path = save_evaluation("# test", tmp, date(2026, 5, 24))
        assert Path(path).exists()
        assert Path(path).read_text(encoding="utf-8") == "# test"


def test_save_evaluation_filename():
    with tempfile.TemporaryDirectory() as tmp:
        path = save_evaluation("content", tmp, date(2026, 5, 24))
        assert Path(path).name == "ai_audit_evaluation.md"
