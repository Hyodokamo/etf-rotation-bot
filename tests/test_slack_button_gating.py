"""Tests for Phase 4.1.1: Slack Button Visibility Gating."""
import json

from src.slack_action_router import route_action
from src.slack_actions import build_action_value, parse_action_value
from src.slack_blocks import build_candidate_action_blocks


def _value(symbol="GRID", stability="STABLE", handling="OK_FOR_WATCHLIST", review_id="rev-1"):
    return build_action_value(
        source_type="candidate_review", review_id=review_id, candidate_symbol=symbol,
        candidate_stability=stability, recommended_handling=handling,
        generated_at="2026-06-05T00:00:00+09:00",
    )


def _action_ids(blocks) -> set[str]:
    actions = next(b for b in blocks if b["type"] == "actions")
    return {e["action_id"] for e in actions["elements"]}


def _note_text(blocks) -> str:
    ctx = next(b for b in blocks if b["type"] == "context")
    return ctx["elements"][0]["text"]


# ── hide small_test when blocked ─────────────────────────────────────────────

def test_slack_candidate_buttons_hide_small_test_when_unstable():
    blocks = build_candidate_action_blocks(_value(stability="UNSTABLE"),
                                           candidate_stability="UNSTABLE")
    assert "candidate_small_test_candidate" not in _action_ids(blocks)


def test_slack_candidate_buttons_hide_small_test_when_human_review_required():
    blocks = build_candidate_action_blocks(
        _value(stability="MINOR_CHANGE", handling="HUMAN_REVIEW_REQUIRED"),
        candidate_stability="MINOR_CHANGE", recommended_handling="HUMAN_REVIEW_REQUIRED")
    assert "candidate_small_test_candidate" not in _action_ids(blocks)


def test_slack_candidate_buttons_hide_small_test_when_do_not_act_yet():
    blocks = build_candidate_action_blocks(
        _value(stability="STABLE", handling="DO_NOT_ACT_YET"),
        candidate_stability="STABLE", recommended_handling="DO_NOT_ACT_YET")
    assert "candidate_small_test_candidate" not in _action_ids(blocks)


# ── show small_test when stable / ok ─────────────────────────────────────────

def test_slack_candidate_buttons_show_small_test_when_stable_ok_for_watchlist():
    blocks = build_candidate_action_blocks(
        _value(stability="STABLE", handling="OK_FOR_WATCHLIST"),
        candidate_stability="STABLE", recommended_handling="OK_FOR_WATCHLIST")
    ids = _action_ids(blocks)
    assert "candidate_small_test_candidate" in ids
    assert "candidate_watchlist" in ids


# ── warning when unstable ────────────────────────────────────────────────────

def test_slack_candidate_buttons_include_warning_when_unstable():
    blocks = build_candidate_action_blocks(_value(stability="UNSTABLE"),
                                           candidate_stability="UNSTABLE")
    note = _note_text(blocks)
    assert "小額検討候補にはできません" in note
    assert "再レビューまたは様子見" in note


# ── keep wait/reject/re_review/note when unstable ────────────────────────────

def test_slack_candidate_buttons_keep_wait_reject_rereview_note_when_unstable():
    blocks = build_candidate_action_blocks(_value(stability="UNSTABLE"),
                                           candidate_stability="UNSTABLE")
    ids = _action_ids(blocks)
    assert {"candidate_wait", "candidate_reject", "candidate_re_review", "candidate_add_note"} <= ids
    # blocked also hides watchlist
    assert "candidate_watchlist" not in ids


# ── press-time block still enforced ──────────────────────────────────────────

def test_slack_candidate_buttons_still_block_small_test_on_action_router(tmp_path):
    # even if a forged client sends the hidden button, the router rejects it
    v = _value(stability="UNSTABLE")
    r = route_action("candidate_small_test_candidate", v, "U1",
                     log_path=tmp_path / "slack_decision_log.jsonl")
    assert r.blocked and not r.recorded


# ── safe value / no secret ───────────────────────────────────────────────────

def test_slack_candidate_buttons_safe_value_no_secret():
    blocks = build_candidate_action_blocks(_value(symbol="BOTZ"))
    actions = next(b for b in blocks if b["type"] == "actions")
    for e in actions["elements"]:
        data = parse_action_value(e["value"])
        assert data["source_type"] == "candidate_review"
    raw = json.dumps(blocks, ensure_ascii=False)
    for bad in ("api_key", "token", "secret", "prompt", "raw_response"):
        assert bad not in raw


# ── allocation / order-quantity safety ───────────────────────────────────────

def test_slack_candidate_buttons_do_not_change_allocation():
    blocks = build_candidate_action_blocks(_value())
    raw = json.dumps(blocks, ensure_ascii=False)
    assert "final_allocation" not in raw
    assert "allocation_override" not in raw  # buttons carry no override flag
    assert "weights" not in raw


def test_slack_candidate_buttons_do_not_calculate_order_quantity():
    blocks = build_candidate_action_blocks(_value())
    raw = json.dumps(blocks, ensure_ascii=False)
    for k in ("quantity", "shares", "order_amount", "order_quantity", "intended_amount_jpy"):
        assert k not in raw
