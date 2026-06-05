"""Tests for Phase 4.3: Slack action-block production wiring + confirmation."""
import json

from src.slack_action_router import route_action, route_note_submission
from src.slack_actions import build_action_value, parse_action_value
from src.slack_modals import build_note_private_metadata
from src.slack_publish import (
    bot_token_available,
    build_candidate_review_blocks,
    build_ephemeral_response,
    build_monthly_digest_blocks,
    post_committee_message,
)


def _value(**kw):
    base = dict(source_type="candidate_review", review_id="rev-1", candidate_symbol="GRID")
    base.update(kw)
    return build_action_value(**base)


def _monthly_value():
    return build_action_value(source_type="monthly_review", run_id="2026-06-05", channel_id="C1")


def _actions_block(blocks):
    return next((b for b in blocks if b["type"] == "actions"), None)


def _ids(blocks):
    a = _actions_block(blocks)
    return {e["action_id"] for e in a["elements"]} if a else set()


# ── monthly digest blocks ────────────────────────────────────────────────────

def test_slack_monthly_digest_includes_action_buttons_when_enabled():
    blocks = build_monthly_digest_blocks("digest text", _monthly_value(), interactive=True)
    ids = _ids(blocks)
    assert {"monthly_review_confirmed", "monthly_skip_this_month",
            "monthly_request_rerun", "monthly_add_note"} <= ids


def test_slack_monthly_digest_omits_action_buttons_when_disabled():
    blocks = build_monthly_digest_blocks("digest text", _monthly_value(), interactive=False)
    assert _actions_block(blocks) is None
    assert any(b["type"] == "section" for b in blocks)


# ── candidate review blocks ──────────────────────────────────────────────────

def test_slack_candidate_review_includes_action_buttons_when_enabled():
    blocks = build_candidate_review_blocks("GRID summary", _value(), "STABLE", "OK_FOR_WATCHLIST")
    ids = _ids(blocks)
    assert "candidate_watchlist" in ids and "candidate_small_test_candidate" in ids


def test_slack_candidate_review_applies_stability_gating():
    full = _ids(build_candidate_review_blocks("s", _value(), "STABLE", "OK_FOR_WATCHLIST"))
    gated = _ids(build_candidate_review_blocks("s", _value(), "UNSTABLE", "REVIEW_BEFORE_ACTION"))
    assert "candidate_small_test_candidate" in full
    assert "candidate_small_test_candidate" not in gated


def test_slack_candidate_review_omits_small_test_when_unstable():
    ids = _ids(build_candidate_review_blocks("s", _value(), "UNSTABLE", None))
    assert "candidate_small_test_candidate" not in ids


def test_slack_candidate_review_omits_watchlist_when_do_not_act_yet():
    ids = _ids(build_candidate_review_blocks("s", _value(), "STABLE", "DO_NOT_ACT_YET"))
    assert "candidate_watchlist" not in ids
    assert "candidate_small_test_candidate" not in ids
    assert {"candidate_wait", "candidate_reject", "candidate_re_review", "candidate_add_note"} <= ids


# ── confirmation messages ────────────────────────────────────────────────────

def test_slack_confirmation_message_monthly_confirmed(tmp_path):
    r = route_action("monthly_review_confirmed", _monthly_value(), "U1",
                     log_path=tmp_path / "l.jsonl")
    assert "確認済み" in r.message


def test_slack_confirmation_message_candidate_wait(tmp_path):
    r = route_action("candidate_wait", _value(), "U1", log_path=tmp_path / "l.jsonl")
    assert "様子見" in r.message
    assert "GRID" in r.message


def test_slack_confirmation_message_candidate_blocked_unstable(tmp_path):
    r = route_action("candidate_small_test_candidate", _value(candidate_stability="UNSTABLE"),
                     "U1", log_path=tmp_path / "l.jsonl")
    assert r.blocked
    assert "UNSTABLE" in r.message
    assert "再レビューまたは様子見" in r.message


def test_slack_confirmation_message_note_submission(tmp_path):
    pm = build_note_private_metadata(source_type="candidate_review", candidate_symbol="GRID",
                                     action_id="candidate_add_note")
    r = route_note_submission(pm, "重複懸念のメモ", "U1",
                              monthly_log_path=tmp_path / "m.jsonl",
                              candidate_log_path=tmp_path / "candidate_review_log.jsonl")
    assert "メモを記録しました" in r.message
    assert "GRID" in r.message


# ── safe value / metadata ────────────────────────────────────────────────────

def test_slack_button_value_contains_safe_target_metadata():
    v = build_action_value(source_type="candidate_review", review_id="rev-1",
                           candidate_symbol="GRID", channel_id="C123", message_ts="100.5")
    data = parse_action_value(v)
    assert data["channel_id"] == "C123"
    assert data["message_ts"] == "100.5"
    assert data["review_id"] == "rev-1"


def test_slack_button_value_no_secret():
    v = build_action_value(source_type="monthly_review", run_id="r", channel_id="C1", message_ts="1.2")
    for bad in ("api_key", "token", "secret", "prompt", "raw_response", "ANTHROPIC", "OPENAI"):
        assert bad not in v


# ── bot token fallback ───────────────────────────────────────────────────────

def test_slack_bot_token_missing_falls_back_to_no_buttons(monkeypatch):
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    captured = {}
    import src.slack_client as sc
    monkeypatch.setattr(sc, "post_to_slack", lambda msg: captured.setdefault("text", msg) or True)
    blocks = build_monthly_digest_blocks("digest", _monthly_value(), interactive=True)
    result = post_committee_message("digest", blocks, channel="C1")
    assert result["delivery"] == "webhook"
    assert result["buttons"] is False
    assert captured["text"] == "digest"
    assert bot_token_available() is False


# ── ephemeral payload safety ─────────────────────────────────────────────────

def test_slack_ephemeral_response_payload_safe():
    resp = build_ephemeral_response("記録しました: GRID を様子見として保存しました")
    assert resp["response_type"] == "ephemeral"
    assert resp["replace_original"] is False
    raw = json.dumps(resp, ensure_ascii=False)
    for bad in ("api_key", "token", "secret", "prompt", "raw_response"):
        assert bad not in raw


# ── allocation / order-quantity / auto-trade safety ──────────────────────────

def _all_blocks_raw():
    m = build_monthly_digest_blocks("digest", _monthly_value(), interactive=True)
    c = build_candidate_review_blocks("s", _value(), "STABLE", "OK_FOR_WATCHLIST")
    return json.dumps(m + c, ensure_ascii=False)


def test_slack_action_blocks_do_not_change_allocation():
    raw = _all_blocks_raw()
    for k in ("final_allocation", "weights", "allocation_override"):
        assert k not in raw


def test_slack_action_blocks_do_not_calculate_order_quantity():
    raw = _all_blocks_raw()
    for k in ("quantity", "shares", "order_amount", "order_quantity", "intended_amount_jpy"):
        assert k not in raw


def test_slack_action_blocks_do_not_trigger_auto_trade():
    raw = _all_blocks_raw()
    for k in ("auto_trade", "place_order", "execute_trade", "brokerage"):
        assert k not in raw
