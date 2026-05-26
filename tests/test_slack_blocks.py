"""Tests for Phase 3: slack_blocks."""
import pytest

from src.slack_blocks import build_review_decision_section, build_review_decision_blocks


# ── build_review_decision_section (text) ─────────────────────────────────────

def test_pass_shows_review_confirmed():
    text = build_review_decision_section("PASS")
    assert "REVIEW_CONFIRMED" in text


def test_pass_shows_skip_and_rerun():
    text = build_review_decision_section("PASS")
    assert "SKIP_THIS_MONTH" in text
    assert "REQUEST_RERUN" in text


def test_fail_does_not_show_review_confirmed():
    text = build_review_decision_section("FAIL", ["single_asset_limit_check"])
    assert "REVIEW_CONFIRMED" not in text


def test_fail_shows_skip_rerun_manual_override():
    text = build_review_decision_section("FAIL", ["single_asset_limit_check"])
    assert "SKIP_THIS_MONTH" in text
    assert "REQUEST_RERUN" in text
    assert "MANUAL_OVERRIDE" in text


def test_fail_shows_warning():
    text = build_review_decision_section("FAIL", ["single_asset_limit_check"])
    assert "自動売買" in text


def test_fail_shows_gate_failures():
    text = build_review_decision_section("FAIL", ["single_asset_limit_check", "category_limit_check"])
    assert "single_asset_limit_check" in text
    assert "category_limit_check" in text


def test_pass_with_caution_shows_all_four_options():
    text = build_review_decision_section("PASS_WITH_CAUTION")
    assert "REVIEW_CONFIRMED" in text
    assert "SKIP_THIS_MONTH" in text
    assert "REQUEST_RERUN" in text
    assert "MANUAL_OVERRIDE" in text


def test_review_required_shows_all_four_options():
    text = build_review_decision_section("REVIEW_REQUIRED")
    assert "REVIEW_CONFIRMED" in text
    assert "MANUAL_OVERRIDE" in text


def test_no_trade_language_in_text():
    """Button text must not say '売買承認' or '承認'."""
    for status in ("PASS", "PASS_WITH_CAUTION", "REVIEW_REQUIRED", "FAIL"):
        text = build_review_decision_section(status)
        assert "売買承認" not in text
        assert "承認" not in text or "自動売買" in text  # warning about auto-trade is OK


def test_cli_hint_present():
    text = build_review_decision_section("PASS")
    assert "python main.py --record-decision" in text


def test_none_gate_status_does_not_crash():
    text = build_review_decision_section(None)
    assert "月次レビュー判断" in text


# ── build_review_decision_blocks (Block Kit) ──────────────────────────────────

def test_blocks_pass_includes_review_confirmed():
    blocks = build_review_decision_blocks("PASS")
    action_block = next((b for b in blocks if b["type"] == "actions"), None)
    assert action_block is not None
    values = [e["value"] for e in action_block["elements"]]
    assert "REVIEW_CONFIRMED" in values


def test_blocks_fail_excludes_review_confirmed():
    blocks = build_review_decision_blocks("FAIL", ["single_asset_limit_check"])
    action_block = next((b for b in blocks if b["type"] == "actions"), None)
    assert action_block is not None
    values = [e["value"] for e in action_block["elements"]]
    assert "REVIEW_CONFIRMED" not in values


def test_blocks_fail_includes_skip_rerun_override():
    blocks = build_review_decision_blocks("FAIL")
    action_block = next((b for b in blocks if b["type"] == "actions"), None)
    values = [e["value"] for e in action_block["elements"]]
    assert "SKIP_THIS_MONTH" in values
    assert "REQUEST_RERUN" in values
    assert "MANUAL_OVERRIDE" in values


def test_blocks_no_trade_approval_label():
    """Button labels must not say '売買承認'."""
    for status in ("PASS", "PASS_WITH_CAUTION", "REVIEW_REQUIRED", "FAIL"):
        blocks = build_review_decision_blocks(status)
        action_block = next((b for b in blocks if b["type"] == "actions"), None)
        if action_block:
            for elem in action_block["elements"]:
                label = elem["text"]["text"]
                assert "売買承認" not in label


def test_blocks_pass_with_caution_all_four():
    blocks = build_review_decision_blocks("PASS_WITH_CAUTION")
    action_block = next((b for b in blocks if b["type"] == "actions"), None)
    values = [e["value"] for e in action_block["elements"]]
    assert len(values) == 4


def test_blocks_contains_cli_hint():
    blocks = build_review_decision_blocks("PASS")
    context_block = next((b for b in blocks if b["type"] == "context"), None)
    assert context_block is not None
    text = context_block["elements"][0]["text"]
    assert "--record-decision" in text


# ── build_slack_summary integration ──────────────────────────────────────────

def test_slack_summary_contains_review_section():
    from src.slack_client import build_slack_summary
    from src.pre_trade_gate import PreTradeGateResult, GateCheckResult

    gate = PreTradeGateResult(overall_status="PASS", checks=[], enabled=True)
    msg = build_slack_summary(
        weights={"VOO": 0.60, "BND": 0.40},
        risk_off=False,
        turnover=0.10,
        report_path="outputs/report.md",
        pre_trade_gate=gate,
    )
    assert "月次レビュー判断" in msg
    assert "REVIEW_CONFIRMED" in msg


def test_slack_summary_fail_gate_review_section():
    from src.slack_client import build_slack_summary
    from src.pre_trade_gate import PreTradeGateResult, GateCheckResult

    chk = GateCheckResult(
        check_id="single_asset_limit_check",
        status="FAIL",
        severity="ERROR",
        message="BND exceeds limit",
        value=0.47,
        limit=0.45,
    )
    gate = PreTradeGateResult(overall_status="FAIL", checks=[chk], enabled=True)
    msg = build_slack_summary(
        weights={"BND": 0.47, "VOO": 0.53},
        risk_off=False,
        turnover=0.10,
        report_path="outputs/report.md",
        pre_trade_gate=gate,
    )
    assert "月次レビュー判断" in msg
    assert "REVIEW_CONFIRMED" not in msg
    assert "SKIP_THIS_MONTH" in msg
