"""Tests for Phase 4.1: Slack Interactivity Foundation."""
import json

import pytest

from src.slack_action_router import route_action
from src.slack_actions import (
    build_action_value,
    parse_action_value,
    read_slack_decision_log,
)
from src.slack_blocks import build_candidate_action_blocks, build_monthly_action_blocks


def _monthly_value(run_id="run-1"):
    return build_action_value(source_type="monthly_review", run_id=run_id, generated_at="2026-06-05T00:00:00+09:00")


def _candidate_value(symbol="GRID", stability="STABLE", handling="REVIEW_BEFORE_ACTION", review_id="rev-1"):
    return build_action_value(
        source_type="candidate_review", review_id=review_id, candidate_symbol=symbol,
        candidate_stability=stability, recommended_handling=handling,
        generated_at="2026-06-05T00:00:00+09:00",
    )


def _log(tmp_path):
    return tmp_path / "slack_decision_log.jsonl"


# ── monthly routing ──────────────────────────────────────────────────────────

def test_slack_action_router_monthly_confirmed(tmp_path):
    r = route_action("monthly_review_confirmed", _monthly_value(), "U1", log_path=_log(tmp_path))
    assert r.ok and r.recorded
    assert r.human_decision == "REVIEW_CONFIRMED"
    assert "確認済み" in r.message


def test_slack_action_router_monthly_skip(tmp_path):
    r = route_action("monthly_skip_this_month", _monthly_value(), "U1", log_path=_log(tmp_path))
    assert r.human_decision == "SKIP_THIS_MONTH"
    assert r.recorded


def test_slack_action_router_monthly_rerun(tmp_path):
    r = route_action("monthly_request_rerun", _monthly_value(), "U1", log_path=_log(tmp_path))
    assert r.human_decision == "REQUEST_RERUN"
    assert r.recorded


# ── candidate routing ────────────────────────────────────────────────────────

def test_slack_action_router_candidate_watchlist(tmp_path):
    r = route_action("candidate_watchlist", _candidate_value(), "U1", log_path=_log(tmp_path))
    assert r.human_decision == "WATCHLIST"
    assert r.candidate_symbol == "GRID"
    assert r.recorded


def test_slack_action_router_candidate_wait(tmp_path):
    r = route_action("candidate_wait", _candidate_value(), "U1", log_path=_log(tmp_path))
    assert r.human_decision == "WAIT"
    assert r.recorded


def test_slack_action_router_candidate_reject(tmp_path):
    r = route_action("candidate_reject", _candidate_value(), "U1", log_path=_log(tmp_path))
    assert r.human_decision == "REJECT"
    assert r.recorded


# ── small_test_candidate stability gating ────────────────────────────────────

def test_slack_action_router_candidate_small_test_candidate_allowed_when_stable(tmp_path):
    v = _candidate_value(stability="STABLE", handling="OK_FOR_WATCHLIST")
    r = route_action("candidate_small_test_candidate", v, "U1", log_path=_log(tmp_path))
    assert r.recorded and not r.blocked
    assert r.human_decision == "SMALL_TEST_BUY_CANDIDATE"


def test_slack_action_router_candidate_small_test_candidate_blocked_when_unstable(tmp_path):
    v = _candidate_value(stability="UNSTABLE")
    r = route_action("candidate_small_test_candidate", v, "U1", log_path=_log(tmp_path))
    assert r.blocked and not r.recorded
    assert "小額検討候補にはできません" in r.message


def test_slack_action_blocks_small_test_candidate_when_human_review_required(tmp_path):
    v = _candidate_value(stability="MINOR_CHANGE", handling="HUMAN_REVIEW_REQUIRED")
    r = route_action("candidate_small_test_candidate", v, "U1", log_path=_log(tmp_path))
    assert r.blocked and not r.recorded


def test_slack_action_blocks_small_test_candidate_when_do_not_act_yet(tmp_path):
    v = _candidate_value(stability="STABLE", handling="DO_NOT_ACT_YET")
    r = route_action("candidate_small_test_candidate", v, "U1", log_path=_log(tmp_path))
    assert r.blocked and not r.recorded


# ── validation ───────────────────────────────────────────────────────────────

def test_slack_action_router_unknown_action_rejected(tmp_path):
    r = route_action("definitely_not_a_real_action", _monthly_value(), "U1", log_path=_log(tmp_path))
    assert not r.ok
    assert "不明" in r.message


def test_slack_action_value_parsing():
    v = _candidate_value(symbol="BOTZ")
    data = parse_action_value(v)
    assert data["source_type"] == "candidate_review"
    assert data["candidate_symbol"] == "BOTZ"
    assert "generated_at" in data


def test_slack_action_value_rejects_malformed_json():
    with pytest.raises(ValueError):
        parse_action_value("{not valid json")
    with pytest.raises(ValueError):
        parse_action_value("[1,2,3]")  # not an object


def test_slack_action_no_secret_in_value():
    v = _candidate_value()
    for bad in ("api_key", "token", "secret", "prompt", "raw_response", "ANTHROPIC", "OPENAI"):
        assert bad not in v


def test_slack_action_value_rejects_sensitive_keys():
    payload = json.dumps({"source_type": "monthly_review", "api_key": "sk-x"})
    with pytest.raises(ValueError):
        parse_action_value(payload)


# ── idempotency ──────────────────────────────────────────────────────────────

def test_slack_action_idempotency(tmp_path):
    p = _log(tmp_path)
    v = _monthly_value(run_id="run-X")
    r1 = route_action("monthly_review_confirmed", v, "U1", log_path=p)
    r2 = route_action("monthly_review_confirmed", v, "U1", log_path=p)
    assert r1.recorded is True
    assert r2.recorded is False and r2.duplicate is True
    assert len(read_slack_decision_log(p)) == 1


# ── authorization ────────────────────────────────────────────────────────────

def test_slack_action_allowed_user(tmp_path):
    r = route_action("monthly_review_confirmed", _monthly_value(), "U1",
                     allowed_users=["U1", "U2"], log_path=_log(tmp_path))
    assert r.ok and r.recorded


def test_slack_action_rejects_unallowed_user(tmp_path):
    r = route_action("monthly_review_confirmed", _monthly_value(), "U_EVIL",
                     allowed_users=["U1"], log_path=_log(tmp_path))
    assert not r.ok
    assert "許可されていません" in r.message
    assert len(read_slack_decision_log(_log(tmp_path))) == 0


# ── append-only logging ──────────────────────────────────────────────────────

def test_slack_action_appends_candidate_log(tmp_path):
    p = _log(tmp_path)
    route_action("candidate_wait", _candidate_value(symbol="GRID"), "U1", log_path=p)
    entries = read_slack_decision_log(p)
    assert len(entries) == 1
    assert entries[0]["source_type"] == "candidate_review"
    assert entries[0]["candidate_symbol"] == "GRID"
    assert entries[0]["human_decision"] == "WAIT"


def test_slack_action_appends_monthly_decision_log(tmp_path):
    p = _log(tmp_path)
    route_action("monthly_skip_this_month", _monthly_value(), "U1", log_path=p)
    entries = read_slack_decision_log(p)
    assert len(entries) == 1
    assert entries[0]["source_type"] == "monthly_review"
    assert entries[0]["human_decision"] == "SKIP_THIS_MONTH"


# ── safety invariants ────────────────────────────────────────────────────────

def test_slack_action_does_not_change_allocation(tmp_path):
    p = _log(tmp_path)
    route_action("candidate_watchlist", _candidate_value(), "U1", log_path=p)
    e = read_slack_decision_log(p)[0]
    assert e["allocation_override"] is False
    assert "final_allocation" not in e and "weights" not in e


def test_slack_action_does_not_calculate_order_quantity(tmp_path):
    p = _log(tmp_path)
    route_action("candidate_small_test_candidate",
                 _candidate_value(stability="STABLE", handling="OK_FOR_WATCHLIST"), "U1", log_path=p)
    e = read_slack_decision_log(p)[0]
    for k in ("quantity", "shares", "order_amount", "order_quantity", "intended_amount_jpy"):
        assert k not in e


def test_slack_action_does_not_trigger_auto_trade(tmp_path):
    p = _log(tmp_path)
    route_action("monthly_review_confirmed", _monthly_value(), "U1", log_path=p)
    e = read_slack_decision_log(p)[0]
    assert e["auto_trade"] is False
    assert e["order_generated"] is False


# ── block kit buttons ────────────────────────────────────────────────────────

def test_slack_button_blocks_include_action_id():
    blocks = build_monthly_action_blocks(_monthly_value())
    actions = next(b for b in blocks if b["type"] == "actions")
    ids = {e["action_id"] for e in actions["elements"]}
    assert "monthly_review_confirmed" in ids
    assert "monthly_request_rerun" in ids
    # candidate blocks too
    cblocks = build_candidate_action_blocks(_candidate_value())
    cactions = next(b for b in cblocks if b["type"] == "actions")
    cids = {e["action_id"] for e in cactions["elements"]}
    assert "candidate_watchlist" in cids


def test_slack_button_blocks_include_safe_value():
    blocks = build_candidate_action_blocks(_candidate_value(symbol="BOTZ"))
    actions = next(b for b in blocks if b["type"] == "actions")
    for e in actions["elements"]:
        data = parse_action_value(e["value"])  # parses & passes secret check
        assert data["source_type"] == "candidate_review"


def test_candidate_blocks_omit_small_test_when_unstable():
    blocks = build_candidate_action_blocks(_candidate_value(stability="UNSTABLE"),
                                           candidate_stability="UNSTABLE")
    actions = next(b for b in blocks if b["type"] == "actions")
    ids = {e["action_id"] for e in actions["elements"]}
    assert "candidate_small_test_candidate" not in ids
    assert "candidate_re_review" in ids


# ── enablement ───────────────────────────────────────────────────────────────

def test_slack_interactivity_disabled_by_default_unless_tokens_present(monkeypatch):
    from src.slack_interaction_handler import interactivity_enabled
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    monkeypatch.delenv("SLACK_APP_TOKEN", raising=False)
    assert interactivity_enabled() is False
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setenv("SLACK_APP_TOKEN", "xapp-test")
    assert interactivity_enabled() is True
