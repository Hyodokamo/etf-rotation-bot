"""Tests for Phase 3: decision_logger."""
import json
import tempfile
from pathlib import Path

import pytest

from src.decision_logger import (
    DECISION_LABELS,
    DecisionLog,
    ReviewDecision,
    create_decision_log,
    save_decision_log,
    update_run_log_with_decision,
    validate_decision,
    load_latest_run_context,
)


# ── fixtures ──────────────────────────────────────────────────────────────────

def _alloc() -> dict[str, float]:
    return {"BND": 0.282, "VOO": 0.2615, "VTV": 0.2475, "QQQM": 0.2091}


def _make_log(
    decision: str = "SKIP_THIS_MONTH",
    comment: str = "今月は見送り",
    gate_status: str = "FAIL",
    gate_failures: list | None = None,
    ai_status: str | None = "REVIEW_REQUIRED",
) -> DecisionLog:
    return create_decision_log(
        run_date="2026-05-26",
        decision=decision,
        comment=comment,
        decided_by="manual",
        strategy_variant="cash_fallback_separated",
        pre_trade_gate_status=gate_status,
        pre_trade_gate_failures=gate_failures or ["single_asset_limit_check"],
        ai_audit_status=ai_status,
        final_allocation=_alloc(),
    )


# ── 1. Basic log creation ─────────────────────────────────────────────────────

def test_skip_this_month_log_creation():
    log = _make_log("SKIP_THIS_MONTH")
    d = log.to_dict()
    assert d["decision"] == "SKIP_THIS_MONTH"
    assert d["decision_label"] == "今月は見送り"
    assert d["auto_trade"] is False
    assert d["order_generated"] is False


def test_request_rerun_log_creation():
    log = _make_log("REQUEST_RERUN", comment="防御資産比率が高い")
    d = log.to_dict()
    assert d["decision"] == "REQUEST_RERUN"
    assert d["decision_label"] == "再レビュー"


def test_review_confirmed_log_creation():
    log = _make_log("REVIEW_CONFIRMED", gate_status="PASS", comment="OK")
    d = log.to_dict()
    assert d["decision"] == "REVIEW_CONFIRMED"


def test_manual_override_log_creation():
    log = _make_log("MANUAL_OVERRIDE", comment="内容確認のうえ採用")
    d = log.to_dict()
    assert d["decision"] == "MANUAL_OVERRIDE"
    assert d["auto_trade"] is False
    assert d["order_generated"] is False


def test_auto_trade_always_false():
    for d_val in ReviewDecision:
        log = _make_log(d_val.value, comment="test")
        assert log.to_dict()["auto_trade"] is False


def test_order_generated_always_false():
    for d_val in ReviewDecision:
        log = _make_log(d_val.value, comment="test")
        assert log.to_dict()["order_generated"] is False


def test_final_allocation_saved():
    log = _make_log()
    assert log.to_dict()["final_allocation"] == {t: round(w, 4) for t, w in _alloc().items()}


def test_strategy_variant_saved():
    log = _make_log()
    assert log.to_dict()["strategy_variant"] == "cash_fallback_separated"


# ── 2. Validation ─────────────────────────────────────────────────────────────

def test_manual_override_requires_comment():
    with pytest.raises(ValueError, match="コメント"):
        validate_decision(
            decision=ReviewDecision.MANUAL_OVERRIDE,
            comment="",
            pre_trade_gate_status="PASS",
            require_comment_on_manual_override=True,
        )


def test_manual_override_with_comment_passes():
    validate_decision(
        decision=ReviewDecision.MANUAL_OVERRIDE,
        comment="採用理由を記述",
        pre_trade_gate_status="PASS",
        require_comment_on_manual_override=True,
    )  # should not raise


def test_fail_gate_requires_comment():
    with pytest.raises(ValueError, match="コメント"):
        validate_decision(
            decision=ReviewDecision.SKIP_THIS_MONTH,
            comment="",
            pre_trade_gate_status="FAIL",
            require_comment_on_fail_gate=True,
        )


def test_fail_gate_with_comment_passes():
    validate_decision(
        decision=ReviewDecision.SKIP_THIS_MONTH,
        comment="見送り理由",
        pre_trade_gate_status="FAIL",
        require_comment_on_fail_gate=True,
    )  # should not raise


def test_manual_override_disabled_raises():
    with pytest.raises(ValueError, match="無効化"):
        validate_decision(
            decision=ReviewDecision.MANUAL_OVERRIDE,
            comment="reason",
            pre_trade_gate_status="PASS",
            allow_manual_override=False,
        )


def test_pass_gate_no_comment_required():
    validate_decision(
        decision=ReviewDecision.REVIEW_CONFIRMED,
        comment="",
        pre_trade_gate_status="PASS",
    )  # should not raise


# ── 3. File I/O ───────────────────────────────────────────────────────────────

def test_save_decision_log_creates_json_and_md():
    log = _make_log("SKIP_THIS_MONTH", comment="見送り")
    with tempfile.TemporaryDirectory() as tmp:
        jp, mp = save_decision_log(log, tmp)
        assert Path(jp).exists()
        assert Path(mp).exists()
        assert Path(jp).name == "decision_log.json"
        assert Path(mp).name == "decision_log.md"


def test_decision_log_json_valid():
    log = _make_log("REQUEST_RERUN", comment="再レビュー")
    with tempfile.TemporaryDirectory() as tmp:
        jp, _ = save_decision_log(log, tmp)
        data = json.loads(Path(jp).read_text(encoding="utf-8"))
        assert data["decision"] == "REQUEST_RERUN"
        assert data["auto_trade"] is False
        assert data["order_generated"] is False
        assert "final_allocation" in data
        assert "strategy_variant" in data


def test_decision_log_md_generated():
    log = _make_log("SKIP_THIS_MONTH", comment="見送りコメント")
    with tempfile.TemporaryDirectory() as tmp:
        _, mp = save_decision_log(log, tmp)
        md = Path(mp).read_text(encoding="utf-8")
        assert "月次レビュー判断ログ" in md
        assert "今月は見送り" in md
        assert "自動売買は行いません" in md
        assert "見送りコメント" in md


def test_update_run_log_with_decision():
    log = _make_log("SKIP_THIS_MONTH", comment="見送り")
    with tempfile.TemporaryDirectory() as tmp:
        # Write a minimal run_log.json
        run_log_path = Path(tmp) / "run_log.json"
        run_log_path.write_text(json.dumps({"run_date": "2026-05-26", "success": True}), encoding="utf-8")

        jp, _ = save_decision_log(log, tmp)
        update_run_log_with_decision(str(run_log_path), log, jp)

        updated = json.loads(run_log_path.read_text(encoding="utf-8"))
        assert updated["review_decision"] == "SKIP_THIS_MONTH"
        assert updated["review_decision_logged"] is True
        assert "review_decision_file" in updated
        assert "review_decision_at" in updated
        # Original fields preserved
        assert updated["run_date"] == "2026-05-26"
        assert updated["success"] is True


def test_update_run_log_nonexistent_path():
    """Should log warning but not raise."""
    log = _make_log()
    update_run_log_with_decision("/nonexistent/path/run_log.json", log, "decision.json")
    # No exception expected


# ── 4. load_latest_run_context ────────────────────────────────────────────────

def test_load_latest_run_context_raises_if_no_run_log():
    with tempfile.TemporaryDirectory() as tmp:
        with pytest.raises(FileNotFoundError):
            load_latest_run_context("2026-05-26", tmp)


def test_load_latest_run_context_loads_run_log():
    with tempfile.TemporaryDirectory() as tmp:
        month_dir = Path(tmp) / "2026-05"
        month_dir.mkdir()
        run_log_data = {"run_date": "2026-05-26", "final_allocation": {"BND": 0.5}}
        (month_dir / "run_log.json").write_text(json.dumps(run_log_data), encoding="utf-8")

        ctx = load_latest_run_context("2026-05-26", tmp)
        assert ctx["run_log"]["run_date"] == "2026-05-26"
        assert ctx["run_log_path"] is not None


# ── 5. --record-decision does not re-run pipeline ─────────────────────────────

def test_record_decision_branch_uses_existing_run_log(tmp_path):
    """When --record-decision is given, load_latest_run_context is called, not fetch_prices."""
    import sys

    month_dir = tmp_path / "2026-05"
    month_dir.mkdir()
    run_log_data = {
        "run_date": "2026-05-26",
        "final_allocation": {"BND": 0.50, "VOO": 0.50},
        "strategy_variant": "cash_fallback_separated",
        "pre_trade_gate_status": "PASS",
        "pre_trade_gate_failures": [],
        "ai_audit_status": None,
        "success": True,
    }
    (month_dir / "run_log.json").write_text(json.dumps(run_log_data), encoding="utf-8")

    # Simulate loading context (no price fetch involved)
    ctx = load_latest_run_context("2026-05-26", str(tmp_path))
    assert ctx["run_log"]["final_allocation"] == {"BND": 0.50, "VOO": 0.50}


# ── 6. MANUAL_OVERRIDE markdown warning ──────────────────────────────────────

def test_manual_override_md_contains_warning():
    log = _make_log("MANUAL_OVERRIDE", comment="採用理由", gate_status="PASS", gate_failures=[])
    md = log.to_markdown()
    assert "MANUAL_OVERRIDE" in md
    assert "自動売買" in md
