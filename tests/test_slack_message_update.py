"""Tests for Phase 4.4: Original message update & audit-trail surfacing."""
import json

from src.slack_action_router import route_action, route_note_submission
from src.slack_actions import build_action_value, read_slack_decision_log
from src.slack_message_updater import (
    STATUS_BLOCK_ID,
    apply_status_block,
    build_chat_update_payload,
    build_status_block,
    update_original_message,
)
from src.slack_modals import build_note_private_metadata

_TS = "2026-06-05T09:12:00+09:00"
_DIGEST = [{"type": "section", "text": {"type": "mrkdwn", "text": "digest body"}}]


def _capture():
    box = {}
    def updater(payload):
        box["payload"] = payload
    return box, updater


# ── status block builders ────────────────────────────────────────────────────

def test_slack_message_update_builds_monthly_status_block():
    b = build_status_block(source_type="monthly_review", human_decision="REVIEW_CONFIRMED",
                           user_id="U123", timestamp=_TS)
    assert b["block_id"] == STATUS_BLOCK_ID
    t = b["text"]["text"]
    assert "記録済み" in t and "月次レビュー" in t and "確認済み" in t
    assert "U123" in t and "2026-06-05 09:12" in t


def test_slack_message_update_builds_candidate_status_block():
    b = build_status_block(source_type="candidate_review", human_decision="WAIT",
                           user_id="U123", timestamp=_TS, candidate_symbol="GRID")
    t = b["text"]["text"]
    assert "GRID" in t and "様子見" in t and "記録済み" in t


def test_slack_message_update_marks_note_present():
    b = build_status_block(source_type="candidate_review", human_decision="WAIT",
                           user_id="U1", timestamp=_TS, candidate_symbol="GRID", note_present=True)
    assert "📝 メモあり" in b["text"]["text"]
    # ADD_NOTE renders the note icon directly
    n = build_status_block(source_type="candidate_review", human_decision="ADD_NOTE",
                           user_id="U1", timestamp=_TS, candidate_symbol="GRID")
    assert "📝" in n["text"]["text"] and "メモ追加" in n["text"]["text"]


# ── append / replace ─────────────────────────────────────────────────────────

def test_slack_message_update_appends_status_when_missing():
    status = build_status_block(source_type="monthly_review", human_decision="REVIEW_CONFIRMED",
                                user_id="U1", timestamp=_TS)
    blocks = apply_status_block(_DIGEST, status)
    assert len(blocks) == 2
    assert blocks[-1]["block_id"] == STATUS_BLOCK_ID


def test_slack_message_update_replaces_existing_status():
    s1 = build_status_block(source_type="monthly_review", human_decision="REVIEW_CONFIRMED",
                            user_id="U1", timestamp=_TS)
    s2 = build_status_block(source_type="monthly_review", human_decision="SKIP_THIS_MONTH",
                            user_id="U1", timestamp=_TS)
    blocks = apply_status_block(apply_status_block(_DIGEST, s1), s2)
    status_blocks = [b for b in blocks if b.get("block_id") == STATUS_BLOCK_ID]
    assert len(status_blocks) == 1
    assert "今月は見送り" in status_blocks[0]["text"]["text"]


def test_slack_message_update_preserves_original_digest_blocks():
    status = build_status_block(source_type="monthly_review", human_decision="REVIEW_CONFIRMED",
                                user_id="U1", timestamp=_TS)
    blocks = apply_status_block(_DIGEST, status)
    assert blocks[0]["text"]["text"] == "digest body"


# ── channel/ts requirements ──────────────────────────────────────────────────

def test_slack_message_update_requires_channel_and_ts():
    box, updater = _capture()
    status = build_status_block(source_type="monthly_review", human_decision="REVIEW_CONFIRMED",
                                user_id="U1", timestamp=_TS)
    res = update_original_message(channel_id="C1", message_ts="100.5", status_block=status,
                                  existing_blocks=_DIGEST, updater=updater)
    assert res["updated"] is True
    assert box["payload"]["channel"] == "C1" and box["payload"]["ts"] == "100.5"


def test_slack_message_update_noop_without_channel_or_ts():
    box, updater = _capture()
    status = build_status_block(source_type="monthly_review", human_decision="REVIEW_CONFIRMED",
                                user_id="U1", timestamp=_TS)
    r1 = update_original_message(channel_id=None, message_ts="1", status_block=status,
                                 existing_blocks=_DIGEST, updater=updater)
    r2 = update_original_message(channel_id="C1", message_ts=None, status_block=status,
                                 existing_blocks=_DIGEST, updater=updater)
    assert r1["updated"] is False and r2["updated"] is False
    assert "payload" not in box  # updater never called


# ── failure handling ─────────────────────────────────────────────────────────

def test_slack_message_update_failure_does_not_rollback_log(tmp_path):
    def boom(payload):
        raise RuntimeError("slack down")
    v = build_action_value(source_type="monthly_review", run_id="r1",
                           channel_id="C1", message_ts="100.5")
    log = tmp_path / "slack_decision_log.jsonl"
    r = route_action("monthly_review_confirmed", v, "U1", log_path=log,
                     original_blocks=_DIGEST, message_updater=boom)
    assert r.ok and r.recorded
    assert len(read_slack_decision_log(log)) == 1


# ── payload safety ───────────────────────────────────────────────────────────

def test_slack_message_update_payload_no_secret():
    status = build_status_block(source_type="candidate_review", human_decision="WAIT",
                                user_id="U1", timestamp=_TS, candidate_symbol="GRID")
    payload = build_chat_update_payload("C1", "100.5", apply_status_block(_DIGEST, status))
    raw = json.dumps(payload, ensure_ascii=False)
    for bad in ("api_key", "token", "secret", "prompt", "raw_response"):
        assert bad not in raw


def _payload_raw():
    status = build_status_block(source_type="candidate_review", human_decision="WAIT",
                                user_id="U1", timestamp=_TS, candidate_symbol="GRID", note_present=True)
    payload = build_chat_update_payload("C1", "100.5", apply_status_block(_DIGEST, status))
    return json.dumps(payload, ensure_ascii=False)


def test_slack_message_update_does_not_change_allocation():
    raw = _payload_raw()
    for k in ("final_allocation", "weights", "allocation_override"):
        assert k not in raw


def test_slack_message_update_does_not_calculate_order_quantity():
    raw = _payload_raw()
    for k in ("quantity", "shares", "order_amount", "order_quantity", "intended_amount_jpy"):
        assert k not in raw


def test_slack_message_update_does_not_trigger_auto_trade():
    raw = _payload_raw()
    for k in ("auto_trade", "place_order", "execute_trade", "brokerage", "買う", "売る", "注文"):
        assert k not in raw


# ── router / note integration ────────────────────────────────────────────────

def test_slack_action_router_calls_message_update_when_metadata_available(tmp_path):
    box, updater = _capture()
    v = build_action_value(source_type="monthly_review", run_id="r1",
                           channel_id="C1", message_ts="100.5")
    r = route_action("monthly_review_confirmed", v, "U1",
                     log_path=tmp_path / "l.jsonl", original_blocks=_DIGEST, message_updater=updater)
    assert r.recorded
    assert box["payload"]["channel"] == "C1"
    assert any(b.get("block_id") == STATUS_BLOCK_ID for b in box["payload"]["blocks"])
    # original digest preserved
    assert box["payload"]["blocks"][0]["text"]["text"] == "digest body"


def test_slack_note_submission_updates_note_status_when_metadata_available(tmp_path):
    box, updater = _capture()
    pm = build_note_private_metadata(source_type="candidate_review", candidate_symbol="GRID",
                                     action_id="candidate_add_note", channel_id="C1", message_ts="200.7")
    r = route_note_submission(pm, "重複懸念のメモ", "U1",
                              monthly_log_path=tmp_path / "m.jsonl",
                              candidate_log_path=tmp_path / "candidate_review_log.jsonl",
                              original_blocks=_DIGEST, message_updater=updater)
    assert r.recorded
    status = [b for b in box["payload"]["blocks"] if b.get("block_id") == STATUS_BLOCK_ID]
    assert status and "メモ追加" in status[0]["text"]["text"]


def test_note_submission_noop_update_without_original_blocks(tmp_path):
    box, updater = _capture()
    pm = build_note_private_metadata(source_type="candidate_review", candidate_symbol="GRID",
                                     action_id="candidate_add_note", channel_id="C1", message_ts="200.7")
    r = route_note_submission(pm, "メモ", "U1",
                              monthly_log_path=tmp_path / "m.jsonl",
                              candidate_log_path=tmp_path / "c.jsonl",
                              original_blocks=None, message_updater=updater)
    assert r.recorded  # log still recorded
    assert "payload" not in box  # no clobbering update without original blocks
