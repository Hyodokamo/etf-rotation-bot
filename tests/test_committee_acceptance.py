"""Phase 3.1 acceptance tests (explicitly named per spec Step 4).

The most important guarantee is test_committee_shadow_mode_does_not_change_allocation:
the committee must NEVER influence the final allocation while in shadow mode.
"""
import copy
import json

import pytest
from pydantic import ValidationError

from src.committee.models import (
    CommitteeConfig,
    CommitteeMemberConfig,
    CommitteeTier,
    CommitteeVerdict,
    MemberOutput,
)
from src.committee.report_formatter import build_committee_markdown
from src.committee.runner import run_committee
from src.llm.base import BaseLlmClient

V = CommitteeVerdict

_CORE_IDS = ["aqr_meb", "howard_marks", "rob_arnott", "core_ai_auditor"]
_SAT_IDS = ["buffett", "paul_tudor_jones", "druckenmiller"]


def _full_cfg(mode="batch", satellite=True) -> CommitteeConfig:
    return CommitteeConfig(
        enabled=True,
        shadow_mode=True,
        llm_call_mode=mode,
        core_committee=[
            CommitteeMemberConfig(member_id=i, display_name=i, focus="f") for i in _CORE_IDS
        ],
        satellite_committee=[
            CommitteeMemberConfig(member_id=i, display_name=i, focus="f") for i in _SAT_IDS
        ] if satellite else [],
    )


def _member_json(mid, verdict="PASS", **extra):
    d = {
        "member_id": mid, "verdict": verdict, "confidence": 0.7,
        "rationale": f"{mid} cites VOO/BND concretely",
        "strongest_support": "support", "strongest_objection": "objection",
        "key_risks": [f"{mid}_risk"], "required_checks": [f"{mid}_check"],
        "action_implication": "advisory only",
    }
    d.update(extra)
    return d


class _BatchClient(BaseLlmClient):
    def __init__(self, ids=None, verdicts=None, raw=None):
        self.ids = ids or (_CORE_IDS + _SAT_IDS)
        self.verdicts = verdicts or {}
        self.raw = raw

    def complete(self, system, user, max_tokens=4096):
        if self.raw is not None:
            return self.raw
        return json.dumps([_member_json(i, self.verdicts.get(i, "PASS")) for i in self.ids])


class _OverrideClient(BaseLlmClient):
    """Adversarial: tries to force allocation_override and weight changes."""

    def complete(self, system, user, max_tokens=4096):
        return json.dumps([
            _member_json(i, "REJECT", allocation_override=True, new_weights={"VOO": 1.0})
            for i in (_CORE_IDS + _SAT_IDS)
        ])


# ── 1. THE critical shadow-mode guarantee ────────────────────────────────────

def test_committee_shadow_mode_does_not_change_allocation():
    final_weights = {"BND": 0.376, "VOO": 0.346, "VTV": 0.151, "QQQM": 0.127}
    snapshot = copy.deepcopy(final_weights)

    context = {"allocation": [{"ticker": t, "weight": w} for t, w in final_weights.items()]}
    res = run_committee(context, _full_cfg(), _OverrideClient())

    # allocation untouched, override locked off, no weights in result
    assert final_weights == snapshot
    assert res.allocation_override is False
    d = res.to_dict()
    assert "new_weights" not in d and "weights" not in d and "final_allocation" not in d
    assert res.shadow_mode is True


# ── 2. verdict enum validation ───────────────────────────────────────────────

def test_committee_verdict_enum_validation():
    # all five valid verdicts accepted
    for v in ("PASS", "PASS_WITH_CAUTION", "WATCH", "REJECT", "INSUFFICIENT_DATA"):
        m = MemberOutput(member_id="x", tier=CommitteeTier.CORE, verdict=v)
        assert m.verdict.value == v
    # invalid verdict rejected
    with pytest.raises(ValidationError):
        MemberOutput(member_id="x", tier=CommitteeTier.CORE, verdict="MAYBE")


# ── 3. core committee runs with all required members ─────────────────────────

def test_core_committee_runs_with_required_members():
    res = run_committee({}, _full_cfg(), _BatchClient())
    core_ids = {m.member_id for m in res.members if m.tier == CommitteeTier.CORE}
    assert core_ids == set(_CORE_IDS)
    # core verdict aggregated independently from satellite
    assert res.core_committee_verdict in set(CommitteeVerdict)


# ── 4. satellite committee runs conditionally (on configuration) ─────────────

def test_satellite_committee_runs_conditionally():
    # configured -> satellite members present and evaluated
    with_sat = run_committee({}, _full_cfg(satellite=True), _BatchClient())
    sat_ids = {m.member_id for m in with_sat.members if m.tier == CommitteeTier.SATELLITE}
    assert sat_ids == set(_SAT_IDS)

    # not configured -> no satellite members, satellite verdict degrades cleanly
    no_sat = run_committee({}, _full_cfg(satellite=False), _BatchClient(ids=_CORE_IDS))
    assert not any(m.tier == CommitteeTier.SATELLITE for m in no_sat.members)
    assert no_sat.satellite_committee_verdict == CommitteeVerdict.INSUFFICIENT_DATA


# ── 5. committee summary added to slack output ───────────────────────────────

def test_committee_summary_added_to_slack_blocks():
    from src.slack_client import build_slack_summary
    res = run_committee({}, _full_cfg(), _BatchClient(verdicts={"rob_arnott": "WATCH"}))
    msg = build_slack_summary(
        weights={"VOO": 0.6, "BND": 0.4},
        risk_off=False,
        turnover=0.1,
        report_path="outputs/report.md",
        committee_result=res,
    )
    assert "Investment Committee" in msg
    assert res.final_committee_verdict.value in msg
    # without committee_result, no committee section leaks in
    plain = build_slack_summary(
        weights={"VOO": 0.6, "BND": 0.4}, risk_off=False, turnover=0.1,
        report_path="outputs/report.md",
    )
    assert "Investment Committee" not in plain


# ── 6. report contains final verdict ─────────────────────────────────────────

def test_committee_report_contains_final_verdict():
    res = run_committee({}, _full_cfg(), _BatchClient(verdicts={"howard_marks": "REJECT"}))
    md = build_committee_markdown(res)
    assert f"`{res.final_committee_verdict.value}`" in md
    assert "最終判定" in md


# ── 7. insufficient data handling ────────────────────────────────────────────

def test_committee_handles_insufficient_data():
    # no client -> graceful all-INSUFFICIENT_DATA, never raises
    res = run_committee({}, _full_cfg(), None)
    assert res.final_committee_verdict == CommitteeVerdict.INSUFFICIENT_DATA
    assert all(m.verdict == CommitteeVerdict.INSUFFICIENT_DATA for m in res.members)
    assert res.allocation_override is False
    # malformed LLM output -> also INSUFFICIENT_DATA, never raises
    res2 = run_committee({}, _full_cfg(), _BatchClient(raw="garbage not json"))
    assert res2.final_committee_verdict == CommitteeVerdict.INSUFFICIENT_DATA


# ── extra: each member returns at least one concrete risk or check ───────────

def test_each_member_returns_at_least_one_risk_or_check():
    # even if the LLM omits both, the runner guarantees a non-empty check
    sparse = json.dumps([
        _member_json(i, "PASS", key_risks=[], required_checks=[])
        for i in (_CORE_IDS + _SAT_IDS)
    ])
    res = run_committee({}, _full_cfg(), _BatchClient(raw=sparse))
    for m in res.members:
        assert m.key_risks or m.required_checks
