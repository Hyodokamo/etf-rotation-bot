"""Tests for Phase 3.6: Candidate Review Decision Log."""
import copy
import json
from pathlib import Path

import pytest

from src.committee.candidate_decision_logger import (
    CANDIDATE_LOG_SCHEMA_VERSION,
    HumanCandidateDecision,
    append_candidate_decision_log,
    build_candidate_log_entry,
    read_candidate_decision_log,
)
from src.committee.candidate_review import CandidateReviewResult, CandidateVerdict


def _result(symbol="GRID", verdict="WAIT_FOR_BETTER_ENTRY", amount=300000.0):
    return CandidateReviewResult(
        review_id=f"rid_{symbol}",
        review_date="2026-06-05",
        candidate={
            "symbol": symbol, "name": f"{symbol} ETF", "asset_type": "theme_etf",
            "theme": "clean_energy_grid", "candidate_action": "NEW_BUY",
            "intended_amount_jpy_consideration_only": amount, "account": "NISA",
            "reason": "growth", "time_horizon": "long", "notes": "n",
        },
        candidate_verdict=verdict,
        confidence=0.76,
        committee_summary="summary",
        member_outputs=[
            {"member_id": "aqr_meb", "tier": "core", "verdict": "WATCH",
             "confidence": 0.8, "dissenting_view": "GRID dissent", "key_risks": ["k"]},
            {"member_id": "buffett", "tier": "satellite", "verdict": "PASS_WITH_CAUTION",
             "confidence": 0.7, "dissenting_view": "buffett dissent"},
        ],
        strongest_buy_thesis="aqr_meb: structural grid demand",
        strongest_rejection_thesis="rob_arnott: valuation unknown",
        key_risks=["既存ポートフォリオとの重複: QQQM", "テーマ過熱"],
        required_checks=["既存ポートフォリオとの重複", "価格トレンド（200日線・相対強度）"],
        entry_conditions=["GRID が200日移動平均線を回復・維持していること"],
        invalidation_conditions=["GRID が200日移動平均線を明確に割り込む"],
        sizing_note="検討額 300,000円 はユーザーの検討額であり、注文数量・株数には変換しません。",
        final_advisory="より良いエントリーを待つことを助言。",
        allocation_override=False,
    )


# ── 1. append ────────────────────────────────────────────────────────────────

def test_candidate_decision_log_append(tmp_path):
    p = tmp_path / "candidate_review_log.jsonl"
    append_candidate_decision_log(build_candidate_log_entry(_result()), p)
    entries = read_candidate_decision_log(p)
    assert len(entries) == 1
    assert entries[0]["candidate_symbol"] == "GRID"


# ── 2. schema version ────────────────────────────────────────────────────────

def test_candidate_decision_log_schema_version(tmp_path):
    p = tmp_path / "log.jsonl"
    append_candidate_decision_log(build_candidate_log_entry(_result()), p)
    e = read_candidate_decision_log(p)[0]
    assert e["schema_version"] == CANDIDATE_LOG_SCHEMA_VERSION
    assert e["review_id"] == "rid_GRID"
    assert "timestamp" in e


# ── 3. one line per candidate ────────────────────────────────────────────────

def test_candidate_decision_log_one_line_per_candidate(tmp_path):
    p = tmp_path / "log.jsonl"
    for r in (_result("GRID"), _result("BOTZ"), _result("ARKQ")):
        append_candidate_decision_log(build_candidate_log_entry(r), p)
    entries = read_candidate_decision_log(p)
    assert len(entries) == 3
    assert [e["candidate_symbol"] for e in entries] == ["GRID", "BOTZ", "ARKQ"]


# ── 4. candidate_verdict ─────────────────────────────────────────────────────

def test_candidate_decision_log_contains_candidate_verdict(tmp_path):
    p = tmp_path / "log.jsonl"
    append_candidate_decision_log(build_candidate_log_entry(_result(verdict="REJECT_FOR_NOW")), p)
    e = read_candidate_decision_log(p)[0]
    assert e["candidate_verdict"] == "REJECT_FOR_NOW"
    assert e["confidence"] == 0.76


# ── 5. theses and risks ──────────────────────────────────────────────────────

def test_candidate_decision_log_contains_theses_and_risks(tmp_path):
    p = tmp_path / "log.jsonl"
    append_candidate_decision_log(build_candidate_log_entry(_result()), p)
    e = read_candidate_decision_log(p)[0]
    assert "structural grid demand" in e["strongest_buy_thesis"]
    assert "valuation unknown" in e["strongest_rejection_thesis"]
    assert any("重複" in r for r in e["key_risks"])
    assert e["entry_conditions"] and e["invalidation_conditions"]


# ── 6. member_outputs ────────────────────────────────────────────────────────

def test_candidate_decision_log_contains_member_outputs(tmp_path):
    p = tmp_path / "log.jsonl"
    append_candidate_decision_log(build_candidate_log_entry(_result()), p)
    e = read_candidate_decision_log(p)[0]
    ids = {m["member_id"] for m in e["member_outputs"]}
    assert {"aqr_meb", "buffett"} <= ids


# ── 7. human decision optional ───────────────────────────────────────────────

def test_candidate_decision_log_human_decision_optional(tmp_path):
    p = tmp_path / "log.jsonl"
    append_candidate_decision_log(build_candidate_log_entry(_result()), p)
    e = read_candidate_decision_log(p)[0]
    assert e["human_decision"] is None
    assert e["human_note"] is None


# ── 8. human decision via CLI-style flags ────────────────────────────────────

def test_candidate_decision_log_human_decision_cli(tmp_path):
    p = tmp_path / "log.jsonl"
    record_human = True
    entry = build_candidate_log_entry(
        _result(),
        human_decision="WAIT" if record_human else None,
        human_note="判定揺れ確認のため様子見" if record_human else None,
    )
    append_candidate_decision_log(entry, p)
    e = read_candidate_decision_log(p)[0]
    assert e["human_decision"] == HumanCandidateDecision.WAIT.value
    assert e["human_note"] == "判定揺れ確認のため様子見"


# ── 9. invalid human decision rejected ───────────────────────────────────────

def test_candidate_decision_log_rejects_invalid_human_decision():
    with pytest.raises(ValueError):
        build_candidate_log_entry(_result(), human_decision="MAYBE_BUY")


# ── 10. redacts sensitive fields ─────────────────────────────────────────────

def test_candidate_decision_log_redacts_sensitive_fields(tmp_path):
    entry = build_candidate_log_entry(_result())
    entry["api_key"] = "sk-secret"
    entry["system_prompt"] = "FULL PROMPT"
    entry["member_outputs"][0]["raw_response"] = "leaked"
    entry["nested"] = {"openai_api_key": "x", "ok": 1}
    p = tmp_path / "log.jsonl"
    append_candidate_decision_log(entry, p)
    text = p.read_text(encoding="utf-8")
    for bad in ("api_key", "sk-secret", "system_prompt", "FULL PROMPT", "raw_response", "openai_api_key"):
        assert bad not in text
    assert read_candidate_decision_log(p)[0]["nested"] == {"ok": 1}


# ── 11. missing logs directory ───────────────────────────────────────────────

def test_candidate_decision_log_handles_missing_logs_directory(tmp_path):
    nested = tmp_path / "no" / "dir" / "yet" / "log.jsonl"
    assert not nested.parent.exists()
    append_candidate_decision_log(build_candidate_log_entry(_result()), nested)
    assert nested.exists()
    assert len(read_candidate_decision_log(nested)) == 1


# ── 12. skips corrupt lines ──────────────────────────────────────────────────

def test_candidate_decision_log_skips_corrupt_lines(tmp_path):
    p = tmp_path / "log.jsonl"
    append_candidate_decision_log(build_candidate_log_entry(_result("GRID")), p)
    with open(p, "a", encoding="utf-8") as f:
        f.write("{ corrupt not json\n")
    append_candidate_decision_log(build_candidate_log_entry(_result("BOTZ")), p)
    entries = read_candidate_decision_log(p)
    assert len(entries) == 2
    assert [e["candidate_symbol"] for e in entries] == ["GRID", "BOTZ"]


# ── 13. append-only ──────────────────────────────────────────────────────────

def test_candidate_decision_log_append_only(tmp_path):
    p = tmp_path / "log.jsonl"
    append_candidate_decision_log(build_candidate_log_entry(_result("GRID")), p)
    first = p.read_text(encoding="utf-8").splitlines()[0]
    append_candidate_decision_log(build_candidate_log_entry(_result("BOTZ")), p)
    lines = p.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert lines[0] == first  # original line preserved verbatim


# ── 14. allocation_override always false ─────────────────────────────────────

def test_candidate_decision_log_allocation_override_false(tmp_path):
    p = tmp_path / "log.jsonl"
    entry = build_candidate_log_entry(_result())
    assert entry["allocation_override"] is False
    append_candidate_decision_log(entry, p)
    assert read_candidate_decision_log(p)[0]["allocation_override"] is False


# ── 15. does not change final allocation ─────────────────────────────────────

def test_candidate_decision_log_does_not_change_final_allocation(tmp_path):
    r = _result()
    cand_snapshot = copy.deepcopy(r.candidate)
    entry = build_candidate_log_entry(r)
    append_candidate_decision_log(entry, tmp_path / "log.jsonl")
    # source candidate untouched; entry carries no allocation/weights
    assert r.candidate == cand_snapshot
    assert "final_allocation" not in entry and "weights" not in entry


# ── 16. does not calculate order quantity ────────────────────────────────────

def test_candidate_decision_log_does_not_calculate_order_quantity(tmp_path):
    p = tmp_path / "log.jsonl"
    append_candidate_decision_log(build_candidate_log_entry(_result(amount=300000.0)), p)
    e = read_candidate_decision_log(p)[0]
    # intended amount stored as consideration only, not converted to shares
    assert e["intended_amount_jpy"] == 300000.0
    assert "株数には変換しません" in e["sizing_note"]
    assert "quantity" not in e and "shares" not in e
