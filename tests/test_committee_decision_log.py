"""Tests for Phase 3.2: Committee Decision Log."""
import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from src.committee.decision_logger import (
    COMMITTEE_LOG_SCHEMA_VERSION,
    HumanCommitteeDecision,
    append_committee_decision_log,
    build_committee_log_entry,
    read_committee_decision_log,
)
from src.committee.models import (
    CommitteeResult,
    CommitteeTier,
    CommitteeVerdict,
    MemberOutput,
)

V = CommitteeVerdict


def _member(mid, tier=CommitteeTier.CORE, verdict=V.PASS_WITH_CAUTION):
    return MemberOutput(
        member_id=mid, display_name=mid.upper(), tier=tier, verdict=verdict,
        confidence=0.8, rationale=f"{mid} cites VOO/QQQM concretely",
        strongest_support="support", strongest_objection="objection",
        dissenting_view=f"{mid} disagrees with QQQM growth tilt",
        key_risks=["k"], required_checks=["c"], next_review_triggers=[f"trig_{mid}"],
        action_implication="advisory",
    )


def _result(final=V.WATCH):
    return CommitteeResult(
        core_committee_verdict=V.WATCH,
        satellite_committee_verdict=V.PASS_WITH_CAUTION,
        final_committee_verdict=final,
        recommended_action="現状配分を維持（shadow）。",
        allocation_override=False,
        summary="s",
        next_review_triggers=["trig_aqr_meb", "trig_buffett"],
        members=[
            _member("aqr_meb", CommitteeTier.CORE, V.WATCH),
            _member("buffett", CommitteeTier.SATELLITE, V.PASS_WITH_CAUTION),
        ],
        shadow_mode=True,
        llm_call_mode="batch",
        satellite_activated=True,
        satellite_activation_reason="always モード",
    )


_ALLOC = {"BND": 0.376, "VOO": 0.346, "VTV": 0.151, "QQQM": 0.127}


def _entry(**over):
    kw = dict(
        committee_result=_result(),
        run_date="2026-06-05",
        strategy_variant="cash_fallback_separated",
        risk_mode="risk_on",
        final_allocation=_ALLOC,
        ai_audit_status="PASS_WITH_CAUTION",
    )
    kw.update(over)
    return build_committee_log_entry(**kw)


# ── 1. append ────────────────────────────────────────────────────────────────

def test_committee_decision_log_append(tmp_path):
    p = tmp_path / "committee_decision_log.jsonl"
    append_committee_decision_log(_entry(), p)
    entries = read_committee_decision_log(p)
    assert len(entries) == 1
    assert entries[0]["final_committee_verdict"] == "WATCH"


# ── 2. schema version ────────────────────────────────────────────────────────

def test_committee_decision_log_schema_version(tmp_path):
    p = tmp_path / "log.jsonl"
    append_committee_decision_log(_entry(), p)
    e = read_committee_decision_log(p)[0]
    assert e["schema_version"] == COMMITTEE_LOG_SCHEMA_VERSION
    assert "run_id" in e and e["run_id"]
    assert "timestamp" in e


# ── 3. member_outputs ────────────────────────────────────────────────────────

def test_committee_decision_log_contains_member_outputs(tmp_path):
    p = tmp_path / "log.jsonl"
    append_committee_decision_log(_entry(), p)
    e = read_committee_decision_log(p)[0]
    assert len(e["member_outputs"]) == 2
    ids = {m["member_id"] for m in e["member_outputs"]}
    assert ids == {"aqr_meb", "buffett"}
    assert all("verdict" in m for m in e["member_outputs"])


# ── 4. dissenting_views ──────────────────────────────────────────────────────

def test_committee_decision_log_contains_dissenting_views(tmp_path):
    p = tmp_path / "log.jsonl"
    append_committee_decision_log(_entry(), p)
    e = read_committee_decision_log(p)[0]
    assert "aqr_meb" in e["dissenting_views"]
    assert "disagrees" in e["dissenting_views"]["aqr_meb"]


# ── 5. human decision optional (default null) ────────────────────────────────

def test_committee_decision_log_human_decision_optional(tmp_path):
    p = tmp_path / "log.jsonl"
    append_committee_decision_log(_entry(), p)
    e = read_committee_decision_log(p)[0]
    assert e["human_decision"] is None
    assert e["human_note"] is None


def test_committee_decision_log_human_decision_filled(tmp_path):
    p = tmp_path / "log.jsonl"
    append_committee_decision_log(_entry(human_decision="HOLD", human_note="ok"), p)
    e = read_committee_decision_log(p)[0]
    assert e["human_decision"] == "HOLD"
    assert e["human_note"] == "ok"


def test_human_decision_enum_rejects_invalid():
    with pytest.raises(ValueError):
        _entry(human_decision="MAYBE")


# ── 6. human decision via CLI ────────────────────────────────────────────────

def test_committee_decision_log_human_decision_cli(tmp_path):
    """Drive the logger the way main.py does: record-flag gates human fields."""
    # simulates: --record-committee-decision --human-decision HOLD --human-note "..."
    record_human = True
    entry = _entry(
        human_decision="HOLD" if record_human else None,
        human_note="Shadow mode validation" if record_human else None,
    )
    p = tmp_path / "log.jsonl"
    append_committee_decision_log(entry, p)
    e = read_committee_decision_log(p)[0]
    assert e["human_decision"] == HumanCommitteeDecision.HOLD.value
    assert e["human_note"] == "Shadow mode validation"


# ── 7. does not change allocation ────────────────────────────────────────────

def test_committee_decision_log_does_not_change_allocation(tmp_path):
    alloc = dict(_ALLOC)
    snapshot = copy.deepcopy(alloc)
    entry = build_committee_log_entry(
        committee_result=_result(),
        run_date="2026-06-05",
        strategy_variant="cash_fallback_separated",
        risk_mode="risk_on",
        final_allocation=alloc,
        ai_audit_status="PASS",
    )
    append_committee_decision_log(entry, tmp_path / "log.jsonl")
    # original allocation dict untouched; override stays false
    assert alloc == snapshot
    assert entry["allocation_override"] is False
    assert entry["final_allocation"] == {t: round(w, 4) for t, w in snapshot.items()}


# ── 8. redacts sensitive fields ──────────────────────────────────────────────

def test_committee_decision_log_redacts_sensitive_fields(tmp_path):
    entry = _entry()
    # inject sensitive material that must never be persisted
    entry["api_key"] = "sk-secret"
    entry["system_prompt"] = "FULL PROMPT TEXT"
    entry["member_outputs"][0]["raw_response"] = "leaked"
    entry["nested"] = {"openai_api_key": "x", "ok": 1}
    p = tmp_path / "log.jsonl"
    append_committee_decision_log(entry, p)
    text = p.read_text(encoding="utf-8")
    for bad in ("api_key", "sk-secret", "system_prompt", "FULL PROMPT TEXT", "raw_response", "openai_api_key"):
        assert bad not in text
    e = read_committee_decision_log(p)[0]
    assert e["nested"] == {"ok": 1}


# ── 9. handles missing logs directory ────────────────────────────────────────

def test_committee_decision_log_handles_missing_logs_directory(tmp_path):
    nested = tmp_path / "does" / "not" / "exist" / "log.jsonl"
    assert not nested.parent.exists()
    append_committee_decision_log(_entry(), nested)
    assert nested.exists()
    assert len(read_committee_decision_log(nested)) == 1


# ── 10. append-only ──────────────────────────────────────────────────────────

def test_committee_decision_log_append_only(tmp_path):
    p = tmp_path / "log.jsonl"
    append_committee_decision_log(_entry(human_note="first"), p)
    first_line = p.read_text(encoding="utf-8").splitlines()[0]
    append_committee_decision_log(_entry(human_note="second"), p)
    lines = p.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    # the original first line is preserved verbatim (append, never rewrite)
    assert lines[0] == first_line


# ── extra: corrupt line tolerance ────────────────────────────────────────────

def test_read_skips_corrupt_lines(tmp_path):
    p = tmp_path / "log.jsonl"
    append_committee_decision_log(_entry(), p)
    with open(p, "a", encoding="utf-8") as f:
        f.write("{ this is not valid json\n")
    append_committee_decision_log(_entry(), p)
    entries = read_committee_decision_log(p)
    # 2 valid entries returned, corrupt middle line skipped
    assert len(entries) == 2
