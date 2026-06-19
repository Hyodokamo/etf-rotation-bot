"""Tests for Phase 3.1 enhancements: satellite conditional activation,
dissenting_view, and member-level next_review_triggers."""
import json

from src.committee.models import (
    CommitteeConfig,
    CommitteeMemberConfig,
    CommitteeTier,
    CommitteeVerdict,
    MemberOutput,
)
from src.committee.report_formatter import build_committee_markdown
from src.committee.runner import evaluate_satellite_activation, run_committee
from src.llm.base import BaseLlmClient

V = CommitteeVerdict
_CORE = ["aqr_meb", "howard_marks", "rob_arnott", "core_ai_auditor"]
_SAT = ["buffett", "paul_tudor_jones", "druckenmiller"]


def _cfg(satellite_activation="always"):
    return CommitteeConfig(
        enabled=True,
        satellite_activation=satellite_activation,
        core_committee=[CommitteeMemberConfig(member_id=i, display_name=i, focus="f") for i in _CORE],
        satellite_committee=[CommitteeMemberConfig(member_id=i, display_name=i, focus="f") for i in _SAT],
    )


def _member_json(mid, verdict="PASS"):
    return {
        "member_id": mid, "verdict": verdict, "confidence": 0.7,
        "rationale": "r", "strongest_support": "s", "strongest_objection": "o",
        "dissenting_view": f"{mid} strongly disagrees with QQQM 12.7% growth tilt",
        "key_risks": ["k"], "required_checks": [f"chk_{mid}"],
        "next_review_triggers": [f"trig_{mid}"], "action_implication": "advisory",
    }


class _Client(BaseLlmClient):
    def complete(self, system, user, max_tokens=4096):
        # return only the member_ids actually requested (present in prompt)
        ids = [i for i in (_CORE + _SAT) if i in user] or (_CORE + _SAT)
        return json.dumps([_member_json(i) for i in ids])


# ── evaluate_satellite_activation (pure) ─────────────────────────────────────

def test_activation_triggered_by_theme_exposure():
    ctx = {"allocation": [{"ticker": "SMH", "category": "theme_equity", "weight": 0.1}]}
    ok, reason = evaluate_satellite_activation(ctx, ai_audit_status=None)
    assert ok is True
    assert "テーマ" in reason


def test_activation_triggered_by_growth():
    ctx = {"category_summary": {"growth_equity": 0.13}, "allocation": []}
    ok, reason = evaluate_satellite_activation(ctx, ai_audit_status=None)
    assert ok is True
    assert "成長株" in reason


def test_activation_triggered_by_turnover():
    ctx = {"allocation": [], "turnover": 0.2}
    ok, reason = evaluate_satellite_activation(ctx, ai_audit_status=None)
    assert ok is True


def test_activation_triggered_by_ai_audit_caution():
    ctx = {"allocation": []}
    ok, reason = evaluate_satellite_activation(ctx, ai_audit_status="PASS_WITH_CAUTION")
    assert ok is True
    assert "AI監査" in reason


def test_activation_not_triggered_when_quiet():
    ctx = {"allocation": [{"ticker": "VOO", "category": "core_equity", "weight": 0.5}],
           "category_summary": {"core_equity": 0.5}, "turnover": 0.0}
    ok, reason = evaluate_satellite_activation(ctx, ai_audit_status="PASS")
    assert ok is False


# ── run_committee with conditional satellite ─────────────────────────────────

def test_conditional_satellite_skipped_when_quiet():
    ctx = {"allocation": [{"ticker": "VOO", "category": "core_equity", "weight": 1.0}],
           "category_summary": {"core_equity": 1.0}, "turnover": 0.0}
    res = run_committee(ctx, _cfg("conditional"), _Client(), ai_audit_status="PASS")
    assert res.satellite_activated is False
    assert not any(m.tier == CommitteeTier.SATELLITE for m in res.members)
    assert res.satellite_committee_verdict == V.INSUFFICIENT_DATA
    # core still runs
    assert len([m for m in res.members if m.tier == CommitteeTier.CORE]) == 4


def test_conditional_satellite_runs_when_triggered():
    ctx = {"allocation": [{"ticker": "QQQM", "category": "growth_equity", "weight": 0.13}],
           "category_summary": {"growth_equity": 0.13}, "turnover": 0.2}
    res = run_committee(ctx, _cfg("conditional"), _Client(), ai_audit_status="PASS_WITH_CAUTION")
    assert res.satellite_activated is True
    assert len([m for m in res.members if m.tier == CommitteeTier.SATELLITE]) == 3


def test_always_mode_runs_satellite_regardless():
    ctx = {"allocation": [], "turnover": 0.0}
    res = run_committee(ctx, _cfg("always"), _Client(), ai_audit_status="PASS")
    assert res.satellite_activated is True
    assert len([m for m in res.members if m.tier == CommitteeTier.SATELLITE]) == 3


# ── dissenting_view + per-member next_review_triggers ────────────────────────

def test_dissenting_view_parsed_and_rendered():
    res = run_committee({}, _cfg("always"), _Client())
    assert all(m.dissenting_view for m in res.members)
    md = build_committee_markdown(res)
    assert "最も強く反対する点" in md
    assert "strongly disagrees" in md


def test_member_next_review_triggers_aggregated():
    res = run_committee({}, _cfg("always"), _Client())
    # member-level next_review_triggers (trig_*) feed the committee-level list
    assert any(t.startswith("trig_") for t in res.next_review_triggers)


def test_member_output_has_new_fields():
    m = MemberOutput(member_id="x", tier=CommitteeTier.CORE, verdict=V.PASS,
                     dissenting_view="d", next_review_triggers=["t"])
    assert m.dissenting_view == "d"
    assert m.next_review_triggers == ["t"]


def test_skipped_satellite_shown_in_markdown():
    ctx = {"allocation": [{"ticker": "VOO", "category": "core_equity", "weight": 1.0}],
           "turnover": 0.0}
    res = run_committee(ctx, _cfg("conditional"), _Client(), ai_audit_status="PASS")
    md = build_committee_markdown(res)
    assert "未起動" in md
