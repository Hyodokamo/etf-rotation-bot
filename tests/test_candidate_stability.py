"""Tests for Phase 3.7: Candidate Review Stability Check."""
import json
from pathlib import Path

from src.committee.candidate_stability import (
    RecommendedHandling,
    Stability,
    StabilitySeverity,
    VerdictDirection,
    build_stability,
    build_stability_markdown,
    build_stability_slack_summary,
    check_candidate_stability,
)


def _entry(symbol="GRID", verdict="WAIT_FOR_BETTER_ENTRY", confidence=0.8,
           review_id="r", rejection="rej thesis", buy="buy thesis",
           key_risks=None, human=None):
    return {
        "candidate_symbol": symbol, "review_id": review_id,
        "candidate_verdict": verdict, "confidence": confidence,
        "strongest_buy_thesis": buy, "strongest_rejection_thesis": rejection,
        "key_risks": key_risks if key_risks is not None else ["risk_a"],
        "human_decision": human,
    }


def _write_log(tmp_path, *entries) -> Path:
    p = tmp_path / "candidate_review_log.jsonl"
    with open(p, "w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    return p


# ── 1. requires two entries ──────────────────────────────────────────────────

def test_candidate_stability_requires_two_entries(tmp_path):
    p = _write_log(tmp_path, _entry("GRID", review_id="1"))
    results = check_candidate_stability(p)
    assert len(results) == 1
    assert results[0].stability == Stability.INSUFFICIENT_HISTORY
    assert results[0].candidate_symbol == "GRID"


# ── 2. detects verdict change ────────────────────────────────────────────────

def test_candidate_stability_detects_verdict_change():
    r = build_stability(
        _entry(verdict="APPROVE_FOR_WATCHLIST", review_id="1"),
        _entry(verdict="WAIT_FOR_BETTER_ENTRY", review_id="2"),
    )
    assert r.verdict_changed is True
    assert r.previous_verdict == "APPROVE_FOR_WATCHLIST"
    assert r.current_verdict == "WAIT_FOR_BETTER_ENTRY"
    assert r.verdict_direction == VerdictDirection.WORSENED


# ── 3. wait -> reject is unstable ────────────────────────────────────────────

def test_candidate_stability_detects_wait_to_reject_as_unstable():
    r = build_stability(
        _entry(verdict="WAIT_FOR_BETTER_ENTRY", review_id="1"),
        _entry(verdict="REJECT_FOR_NOW", review_id="2"),
    )
    assert r.stability == Stability.UNSTABLE
    assert r.severity in (StabilitySeverity.CAUTION, StabilitySeverity.MATERIAL)


# ── 4. reject -> reject is stable but do-not-act ─────────────────────────────

def test_candidate_stability_detects_reject_to_reject_as_stable_do_not_act():
    r = build_stability(
        _entry(verdict="REJECT_FOR_NOW", review_id="1"),
        _entry(verdict="REJECT_FOR_NOW", review_id="2"),
    )
    assert r.stability == Stability.STABLE
    assert r.recommended_handling == RecommendedHandling.DO_NOT_ACT_YET


# ── 5. confidence change ─────────────────────────────────────────────────────

def test_candidate_stability_detects_confidence_change():
    r = build_stability(
        _entry(verdict="WAIT_FOR_BETTER_ENTRY", confidence=0.9, review_id="1"),
        _entry(verdict="WAIT_FOR_BETTER_ENTRY", confidence=0.6, review_id="2"),
    )
    assert abs(r.confidence_change - (-0.3)) < 1e-9
    assert r.severity == StabilitySeverity.CAUTION  # >= 20pt swing


# ── 6/7. key risk diffs ──────────────────────────────────────────────────────

def test_candidate_stability_detects_new_key_risks():
    r = build_stability(
        _entry(key_risks=["a"], review_id="1"),
        _entry(key_risks=["a", "b", "c"], review_id="2"),
    )
    assert set(r.new_key_risks) == {"b", "c"}


def test_candidate_stability_detects_resolved_key_risks():
    r = build_stability(
        _entry(key_risks=["a", "b"], review_id="1"),
        _entry(key_risks=["a"], review_id="2"),
    )
    assert r.resolved_key_risks == ["b"]


# ── 8. human decision change ─────────────────────────────────────────────────

def test_candidate_stability_detects_human_decision_change():
    r = build_stability(
        _entry(verdict="WAIT_FOR_BETTER_ENTRY", review_id="1", human="WAIT"),
        _entry(verdict="WAIT_FOR_BETTER_ENTRY", review_id="2", human="RE_REVIEW"),
    )
    assert r.human_decision_changed is True
    assert r.previous_human_decision == "WAIT"
    assert r.current_human_decision == "RE_REVIEW"


# ── 9. human/verdict mismatch requires review ────────────────────────────────

def test_candidate_stability_human_decision_mismatch_requires_review():
    r = build_stability(
        _entry(verdict="REJECT_FOR_NOW", review_id="1"),
        _entry(verdict="REJECT_FOR_NOW", review_id="2", human="SMALL_TEST_BUY_CANDIDATE"),
    )
    assert r.recommended_handling == RecommendedHandling.HUMAN_REVIEW_REQUIRED
    assert r.severity == StabilitySeverity.MATERIAL


# ── 10. symbol filter ────────────────────────────────────────────────────────

def test_candidate_stability_symbol_filter(tmp_path):
    p = _write_log(
        tmp_path,
        _entry("GRID", review_id="g1"), _entry("GRID", verdict="REJECT_FOR_NOW", review_id="g2"),
        _entry("BOTZ", review_id="b1"), _entry("BOTZ", review_id="b2"),
    )
    grid = check_candidate_stability(p, symbol="grid")
    assert len(grid) == 1
    assert grid[0].candidate_symbol == "GRID"


# ── 11. skips corrupt lines ──────────────────────────────────────────────────

def test_candidate_stability_skips_corrupt_lines(tmp_path):
    p = tmp_path / "log.jsonl"
    with open(p, "w", encoding="utf-8") as f:
        f.write(json.dumps(_entry("GRID", verdict="WAIT_FOR_BETTER_ENTRY", review_id="1")) + "\n")
        f.write("{ corrupt not json\n")
        f.write(json.dumps(_entry("GRID", verdict="REJECT_FOR_NOW", review_id="2")) + "\n")
    results = check_candidate_stability(p, symbol="GRID")
    assert len(results) == 1
    assert results[0].previous_review_id == "1"
    assert results[0].current_review_id == "2"
    assert results[0].stability == Stability.UNSTABLE


# ── 12. markdown section ─────────────────────────────────────────────────────

def test_candidate_stability_markdown_section():
    r = build_stability(
        _entry(verdict="WAIT_FOR_BETTER_ENTRY", review_id="1"),
        _entry(verdict="REJECT_FOR_NOW", review_id="2"),
    )
    md = build_stability_markdown([r], "2026-06-05")
    assert "Candidate Stability Check" in md
    assert "GRID" in md
    assert "UNSTABLE" in md
    assert "推奨対応" in md
    assert "承認判断ではありません" in md
    assert "売買承認" not in md


# ── 13. slack summary ────────────────────────────────────────────────────────

def test_candidate_stability_slack_summary():
    r = build_stability(
        _entry(verdict="WAIT_FOR_BETTER_ENTRY", review_id="1"),
        _entry(verdict="REJECT_FOR_NOW", review_id="2"),
    )
    s = build_stability_slack_summary(r)
    assert "GRID" in s
    assert "UNSTABLE" in s
    assert "REJECT_FOR_NOW" in s
    assert "監査のみ" in s
    assert "売買承認" not in s


# ── 14. does not change final allocation ─────────────────────────────────────

def test_candidate_stability_does_not_change_final_allocation(tmp_path):
    e1 = _entry(verdict="WAIT_FOR_BETTER_ENTRY", review_id="1")
    e2 = _entry(verdict="REJECT_FOR_NOW", review_id="2")
    import copy
    snap1, snap2 = copy.deepcopy(e1), copy.deepcopy(e2)
    r = build_stability(e1, e2)
    # source entries untouched; result carries no allocation/override
    assert e1 == snap1 and e2 == snap2
    d = r.to_dict()
    assert "final_allocation" not in d and "weights" not in d and "allocation_override" not in d


# ── 15. does not calculate order quantity ────────────────────────────────────

def test_candidate_stability_does_not_calculate_order_quantity():
    r = build_stability(
        _entry(verdict="APPROVE_SMALL_TEST_BUY", review_id="1"),
        _entry(verdict="APPROVE_SMALL_TEST_BUY", review_id="2"),
    )
    d = r.to_dict()
    for k in ("quantity", "shares", "order_quantity", "intended_amount_jpy"):
        assert k not in d
