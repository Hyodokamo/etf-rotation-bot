"""Tests for Phase 3.1: committee models and aggregation."""
import pytest

from src.committee.models import (
    CommitteeResult,
    CommitteeTier,
    CommitteeVerdict,
    MemberOutput,
    aggregate_verdict,
)

V = CommitteeVerdict


# ── verdict enum ────────────────────────────────────────────────────────────

def test_five_verdict_types():
    assert {v.value for v in CommitteeVerdict} == {
        "PASS", "PASS_WITH_CAUTION", "WATCH", "REJECT", "INSUFFICIENT_DATA"
    }


# ── MemberOutput ─────────────────────────────────────────────────────────────

def _member(verdict=V.PASS, tier=CommitteeTier.CORE, confidence=0.5):
    return MemberOutput(
        member_id="aqr_faber",
        display_name="AQR / Meb Faber 型",
        tier=tier,
        verdict=verdict,
        confidence=confidence,
        rationale="r",
        strongest_support="s",
        strongest_objection="o",
    )


def test_member_output_fields():
    m = _member()
    assert m.member_id == "aqr_faber"
    assert m.tier == CommitteeTier.CORE
    assert m.strongest_support == "s"
    assert m.strongest_objection == "o"
    assert m.key_risks == []


def test_confidence_clamped_high():
    assert _member(confidence=5.0).confidence == 1.0


def test_confidence_clamped_low():
    assert _member(confidence=-3.0).confidence == 0.0


def test_confidence_non_numeric_defaults_zero():
    m = MemberOutput(
        member_id="x", tier=CommitteeTier.CORE, verdict=V.PASS, confidence="bad"
    )
    assert m.confidence == 0.0


# ── aggregate_verdict: severity-based, NOT majority ──────────────────────────

def test_one_reject_among_pass_is_reject():
    # 3 PASS + 1 REJECT — majority is PASS, but severity dominates -> REJECT
    assert aggregate_verdict([V.PASS, V.PASS, V.PASS, V.REJECT]) == V.REJECT


def test_watch_dominates_caution_and_pass():
    assert aggregate_verdict([V.PASS, V.PASS_WITH_CAUTION, V.WATCH]) == V.WATCH


def test_caution_dominates_pass():
    assert aggregate_verdict([V.PASS, V.PASS, V.PASS_WITH_CAUTION]) == V.PASS_WITH_CAUTION


def test_all_pass_is_pass():
    assert aggregate_verdict([V.PASS, V.PASS]) == V.PASS


def test_empty_is_insufficient():
    assert aggregate_verdict([]) == V.INSUFFICIENT_DATA


def test_all_insufficient_is_insufficient():
    assert aggregate_verdict([V.INSUFFICIENT_DATA, V.INSUFFICIENT_DATA]) == V.INSUFFICIENT_DATA


def test_mixed_pass_and_insufficient_is_caution():
    assert aggregate_verdict([V.PASS, V.INSUFFICIENT_DATA]) == V.PASS_WITH_CAUTION


def test_reject_beats_insufficient():
    assert aggregate_verdict([V.INSUFFICIENT_DATA, V.REJECT]) == V.REJECT


# ── CommitteeResult: allocation_override hard-locked False ───────────────────

def _result(override):
    return CommitteeResult(
        core_committee_verdict=V.PASS,
        satellite_committee_verdict=V.PASS,
        final_committee_verdict=V.PASS,
        recommended_action="hold",
        allocation_override=override,
        summary="s",
    )


def test_allocation_override_forced_false_when_true():
    assert _result(True).allocation_override is False


def test_allocation_override_false_stays_false():
    assert _result(False).allocation_override is False


def test_to_dict_roundtrip():
    d = _result(True).to_dict()
    assert d["allocation_override"] is False
    assert d["final_committee_verdict"] == "PASS"
