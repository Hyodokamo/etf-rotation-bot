"""Tests for Phase 4.2: Slack Note Modal."""
import json

import pytest

from src.slack_action_router import route_note_submission
from src.slack_actions import build_action_value, read_slack_decision_log
from src.committee.candidate_decision_logger import read_candidate_decision_log
from src.slack_interaction_handler import handle_payload, handle_view_submission
from src.slack_modals import (
    MAX_NOTE_LEN,
    NOTE_ACTION_ID,
    NOTE_BLOCK_ID,
    NOTE_MODAL_CALLBACK_ID,
    build_note_modal_from_action_value,
    build_note_modal_view,
    build_note_private_metadata,
    extract_human_note,
    parse_note_private_metadata,
    validate_note,
)


def _monthly_pm(run_id="run-1"):
    return build_note_private_metadata(source_type="monthly_review", run_id=run_id,
                                       action_id="monthly_add_note")


def _candidate_pm(symbol="GRID", review_id="rev-1"):
    return build_note_private_metadata(source_type="candidate_review", review_id=review_id,
                                       candidate_symbol=symbol, action_id="candidate_add_note")


def _view(metadata, note):
    return {
        "private_metadata": metadata,
        "state": {"values": {NOTE_BLOCK_ID: {NOTE_ACTION_ID: {"value": note}}}},
    }


# ── modal builders ───────────────────────────────────────────────────────────

def test_slack_note_modal_builds_monthly_view():
    v = build_note_modal_view(source_type="monthly_review", run_id="r1", action_id="monthly_add_note")
    assert v["type"] == "modal"
    assert v["callback_id"] == NOTE_MODAL_CALLBACK_ID
    assert v["title"]["text"] == "判断メモ"
    assert v["submit"]["text"] == "記録"
    assert v["close"]["text"] == "キャンセル"
    # no trade wording in the modal
    raw = json.dumps(v, ensure_ascii=False)
    for bad in ("買う", "売る", "注文", "購入実行"):
        assert bad not in raw


def test_slack_note_modal_builds_candidate_view():
    v = build_note_modal_view(source_type="candidate_review", candidate_symbol="GRID",
                              action_id="candidate_add_note")
    assert v["type"] == "modal"
    meta = parse_note_private_metadata(v["private_metadata"])
    assert meta["candidate_symbol"] == "GRID"


# ── private_metadata safety ──────────────────────────────────────────────────

def test_slack_note_modal_private_metadata_safe():
    pm = _candidate_pm()
    data = parse_note_private_metadata(pm)
    assert data["source_type"] == "candidate_review"
    for bad in ("api_key", "token", "secret", "prompt", "raw_response"):
        assert bad not in pm


def test_slack_note_modal_rejects_secret_in_metadata():
    bad = json.dumps({"source_type": "monthly_review", "api_key": "sk-x"})
    with pytest.raises(ValueError):
        parse_note_private_metadata(bad)
    with pytest.raises(ValueError):
        parse_note_private_metadata("{not json")


# ── required fields ──────────────────────────────────────────────────────────

def test_slack_note_modal_requires_source_type():
    with pytest.raises(ValueError):
        build_note_modal_view(source_type="")


def test_slack_note_modal_requires_run_id_for_monthly():
    with pytest.raises(ValueError):
        build_note_modal_view(source_type="monthly_review")


def test_slack_note_modal_requires_candidate_symbol_or_review_id():
    with pytest.raises(ValueError):
        build_note_modal_view(source_type="candidate_review")
    # one of them is enough
    assert build_note_modal_view(source_type="candidate_review", review_id="rev-1")


# ── note extraction / validation ─────────────────────────────────────────────

def test_slack_note_modal_extracts_human_note():
    view = _view(_monthly_pm(), "  防御資産比率を確認したい  ")
    assert extract_human_note(view) == "防御資産比率を確認したい"


def test_slack_note_modal_rejects_empty_note():
    with pytest.raises(ValueError):
        validate_note("    ")
    # via router
    r = route_note_submission(_monthly_pm(), "   ", "U1",
                              monthly_log_path="x", candidate_log_path="y")
    assert not r.ok and "メモが空" in r.message


def test_slack_note_modal_truncates_or_rejects_long_note():
    assert len(validate_note("あ" * 1000)) == MAX_NOTE_LEN


# ── submissions append-only ──────────────────────────────────────────────────

def test_slack_note_modal_submission_appends_monthly_log(tmp_path):
    mlog = tmp_path / "slack_decision_log.jsonl"
    r = route_note_submission(_monthly_pm(), "見送り理由のメモ", "U1",
                              monthly_log_path=mlog, candidate_log_path=tmp_path / "c.jsonl")
    assert r.recorded
    entries = read_slack_decision_log(mlog)
    assert len(entries) == 1
    assert entries[0]["entry_type"] == "note"
    assert entries[0]["source_type"] == "monthly_review"
    assert entries[0]["human_note"] == "見送り理由のメモ"


def test_slack_note_modal_submission_appends_candidate_log(tmp_path):
    clog = tmp_path / "candidate_review_log.jsonl"
    r = route_note_submission(_candidate_pm(symbol="GRID"), "GRIDは重複懸念", "U1",
                              monthly_log_path=tmp_path / "m.jsonl", candidate_log_path=clog)
    assert r.recorded
    entries = read_candidate_decision_log(clog)
    assert len(entries) == 1
    assert entries[0]["entry_type"] == "note"
    assert entries[0]["candidate_symbol"] == "GRID"
    assert entries[0]["human_note"] == "GRIDは重複懸念"


# ── idempotency / authorization ──────────────────────────────────────────────

def test_slack_note_modal_submission_idempotency(tmp_path):
    mlog = tmp_path / "log.jsonl"
    pm = _monthly_pm(run_id="run-X")
    r1 = route_note_submission(pm, "同じメモ", "U1", monthly_log_path=mlog, candidate_log_path=tmp_path / "c.jsonl")
    r2 = route_note_submission(pm, "同じメモ", "U1", monthly_log_path=mlog, candidate_log_path=tmp_path / "c.jsonl")
    assert r1.recorded and not r2.recorded and r2.duplicate
    assert len(read_slack_decision_log(mlog)) == 1


def test_slack_note_modal_submission_allowed_user(tmp_path):
    r = route_note_submission(_monthly_pm(), "メモ", "U1", allowed_users=["U1"],
                              monthly_log_path=tmp_path / "log.jsonl", candidate_log_path=tmp_path / "c.jsonl")
    assert r.ok and r.recorded


def test_slack_note_modal_submission_rejects_unallowed_user(tmp_path):
    mlog = tmp_path / "log.jsonl"
    r = route_note_submission(_monthly_pm(), "メモ", "U_EVIL", allowed_users=["U1"],
                              monthly_log_path=mlog, candidate_log_path=tmp_path / "c.jsonl")
    assert not r.ok
    assert "許可されていません" in r.message
    assert read_slack_decision_log(mlog) == []


# ── safety invariants ────────────────────────────────────────────────────────

def test_slack_note_modal_does_not_change_allocation(tmp_path):
    mlog = tmp_path / "log.jsonl"
    route_note_submission(_monthly_pm(), "メモ", "U1", monthly_log_path=mlog, candidate_log_path=tmp_path / "c.jsonl")
    e = read_slack_decision_log(mlog)[0]
    assert e["allocation_override"] is False
    assert "final_allocation" not in e and "weights" not in e


def test_slack_note_modal_does_not_calculate_order_quantity(tmp_path):
    clog = tmp_path / "candidate_review_log.jsonl"
    route_note_submission(_candidate_pm(), "メモ", "U1", monthly_log_path=tmp_path / "m.jsonl", candidate_log_path=clog)
    e = read_candidate_decision_log(clog)[0]
    for k in ("quantity", "shares", "order_amount", "order_quantity", "intended_amount_jpy"):
        assert k not in e


def test_slack_note_modal_does_not_trigger_auto_trade(tmp_path):
    mlog = tmp_path / "log.jsonl"
    route_note_submission(_monthly_pm(), "メモ", "U1", monthly_log_path=mlog, candidate_log_path=tmp_path / "c.jsonl")
    e = read_slack_decision_log(mlog)[0]
    assert e["auto_trade"] is False and e["order_generated"] is False


# ── add_note button -> modal payload ─────────────────────────────────────────

def test_slack_add_note_button_opens_modal_payload():
    value = build_action_value(source_type="monthly_review", run_id="run-1")
    view = build_note_modal_from_action_value(value, "monthly_add_note", "U1")
    assert view["type"] == "modal"
    assert view["callback_id"] == NOTE_MODAL_CALLBACK_ID
    meta = parse_note_private_metadata(view["private_metadata"])
    assert meta["run_id"] == "run-1"
    assert meta["source_type"] == "monthly_review"


def test_view_submission_handler_records(tmp_path):
    # handle_view_submission wires extract + route together (explicit tmp log paths)
    view = _view(_candidate_pm(symbol="BOTZ"), "BOTZメモ")
    res = handle_view_submission(
        view, "U1",
        monthly_log_path=str(tmp_path / "m.jsonl"),
        candidate_log_path=str(tmp_path / "candidate_review_log.jsonl"),
    )
    assert res["recorded"] is True
    assert res["source_type"] == "candidate_review"
    assert len(read_candidate_decision_log(tmp_path / "candidate_review_log.jsonl")) == 1
