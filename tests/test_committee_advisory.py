"""Tests for Phase 3.4: Committee Advisory Mode."""
import copy

from src.committee.advisory import (
    AdvisoryCategory,
    AdvisoryPriority,
    CommitteeAdvisory,
    OverallStance,
    build_advisory,
    build_advisory_markdown,
    build_advisory_slack_summary,
)
from src.committee.models import (
    CommitteeResult,
    CommitteeTier,
    CommitteeVerdict,
    MemberOutput,
)
from src.committee.review_comparison import build_comparison

V = CommitteeVerdict

_CORE = ["aqr_meb", "howard_marks", "rob_arnott", "core_ai_auditor"]
_SAT = ["buffett", "paul_tudor_jones", "druckenmiller"]


def _member(mid, tier, verdict=V.PASS):
    return MemberOutput(
        member_id=mid, display_name=mid, tier=tier, verdict=verdict,
        confidence=0.7, rationale="r", strongest_support="s", strongest_objection="o",
        dissenting_view=f"{mid} dissent", key_risks=["k"], required_checks=["c"],
        next_review_triggers=[f"trig_{mid}"], action_implication="advisory",
    )


def _result(final=V.PASS_WITH_CAUTION, core=V.PASS_WITH_CAUTION, sat=V.PASS,
            member_verdicts=None, triggers=None):
    member_verdicts = member_verdicts or {}
    members = []
    for mid in _CORE:
        members.append(_member(mid, CommitteeTier.CORE, member_verdicts.get(mid, V.PASS)))
    for mid in _SAT:
        members.append(_member(mid, CommitteeTier.SATELLITE, member_verdicts.get(mid, V.PASS)))
    return CommitteeResult(
        core_committee_verdict=core,
        satellite_committee_verdict=sat,
        final_committee_verdict=final,
        recommended_action="現状維持（shadow）",
        allocation_override=False,
        summary="s",
        next_review_triggers=triggers or ["QQQM相対強度", "BND防御機能"],
        members=members,
        shadow_mode=True,
        llm_call_mode="batch",
        satellite_activated=True,
    )


_ALLOC = {"BND": 0.376, "VOO": 0.346, "VTV": 0.151, "QQQM": 0.127}


def _log(final, alloc, members=None, triggers=None, dissent=None, override=False):
    return {
        "run_id": "x", "date": "2026-05-05",
        "final_committee_verdict": final, "core_committee_verdict": final,
        "satellite_committee_verdict": "PASS", "recommended_action": "a",
        "allocation_override": override, "human_decision": None,
        "member_outputs": members or [{"member_id": m, "verdict": "PASS"} for m in _CORE + _SAT],
        "final_allocation": alloc, "dissenting_views": dissent or {},
        "next_review_triggers": triggers or [],
    }


# ── 1. generates from committee result ───────────────────────────────────────

def test_committee_advisory_generates_from_committee_result():
    adv = build_advisory(_result(), final_allocation=_ALLOC, risk_mode="risk_on")
    assert isinstance(adv, CommitteeAdvisory)
    assert adv.advisory_mode == "shadow_advisory"
    assert adv.overall_stance in set(OverallStance)
    assert isinstance(adv.action_items, list)
    assert adv.do_not_actions  # non-empty fixed list


# ── 2. uses review comparison ────────────────────────────────────────────────

def test_committee_advisory_uses_review_comparison():
    prev = _log("PASS", _ALLOC, triggers=["old"])
    curr = _log("PASS", _ALLOC, triggers=["old", "QQQM新トリガー"])
    cmp = build_comparison(prev, curr)
    adv = build_advisory(_result(final=V.PASS), comparison=cmp, final_allocation=_ALLOC)
    assert adv.generated_from["comparison_severity"] == cmp.severity
    # new trigger reflected somewhere
    joined = " ".join(it.review_trigger + it.message for it in adv.action_items)
    assert "QQQM新トリガー" in joined or "QQQM新トリガー" in " ".join(adv.next_review_focus)


# ── 3. high priority when severity CAUTION ───────────────────────────────────

def test_committee_advisory_high_priority_when_severity_caution():
    # construct a comparison with CAUTION severity via 2 key members worsening
    prev = _log("PASS_WITH_CAUTION", _ALLOC, members=[
        {"member_id": "howard_marks", "verdict": "PASS"},
        {"member_id": "rob_arnott", "verdict": "PASS"},
    ])
    curr = _log("PASS_WITH_CAUTION", _ALLOC, members=[
        {"member_id": "howard_marks", "verdict": "WATCH"},
        {"member_id": "rob_arnott", "verdict": "WATCH"},
    ])
    cmp = build_comparison(prev, curr)
    assert cmp.severity == "CAUTION"
    adv = build_advisory(
        _result(final=V.PASS_WITH_CAUTION,
                member_verdicts={"howard_marks": V.WATCH, "rob_arnott": V.WATCH}),
        comparison=cmp, final_allocation=_ALLOC,
    )
    assert any(it.priority == AdvisoryPriority.HIGH for it in adv.action_items)


# ── 4. WATCH -> WAIT_FOR_REVIEW ──────────────────────────────────────────────

def test_committee_advisory_watch_maps_to_wait_for_review():
    adv = build_advisory(_result(final=V.WATCH, core=V.WATCH), final_allocation=_ALLOC)
    assert adv.overall_stance == OverallStance.WAIT_FOR_REVIEW


# ── 5. AI REJECT prioritizes data quality ────────────────────────────────────

def test_committee_advisory_ai_reject_prioritizes_data_quality():
    adv = build_advisory(_result(final=V.PASS), final_allocation=_ALLOC, ai_audit_status="REJECT")
    assert adv.action_items
    assert adv.action_items[0].category in (
        AdvisoryCategory.DATA_QUALITY, AdvisoryCategory.HUMAN_DECISION_REQUIRED
    )
    cats = {it.category for it in adv.action_items}
    assert AdvisoryCategory.HUMAN_DECISION_REQUIRED in cats


# ── 6/7/8. member-driven categories ──────────────────────────────────────────

def test_committee_advisory_rob_arnott_watch_adds_buy_discipline():
    adv = build_advisory(_result(member_verdicts={"rob_arnott": V.WATCH}), final_allocation=_ALLOC)
    assert any(it.category == AdvisoryCategory.BUY_DISCIPLINE for it in adv.action_items)


def test_committee_advisory_howard_marks_watch_adds_risk_control():
    adv = build_advisory(_result(member_verdicts={"howard_marks": V.WATCH}), final_allocation=_ALLOC)
    assert any(it.category == AdvisoryCategory.RISK_CONTROL for it in adv.action_items)


def test_committee_advisory_ptj_watch_adds_review_trigger():
    adv = build_advisory(_result(member_verdicts={"paul_tudor_jones": V.WATCH}), final_allocation=_ALLOC)
    assert any(it.category == AdvisoryCategory.REVIEW_TRIGGER for it in adv.action_items)


def test_druckenmiller_watch_adds_candidate_review():
    adv = build_advisory(_result(member_verdicts={"druckenmiller": V.WATCH}), final_allocation=_ALLOC)
    assert any(it.category == AdvisoryCategory.CANDIDATE_REVIEW for it in adv.action_items)


# ── 9. next_review_triggers reflected ────────────────────────────────────────

def test_committee_advisory_next_review_triggers_reflected():
    prev = _log("PASS", _ALLOC, triggers=["a"])
    curr = _log("PASS", _ALLOC, triggers=["a", "b", "c", "d"])
    cmp = build_comparison(prev, curr)
    adv = build_advisory(_result(final=V.PASS), comparison=cmp, final_allocation=_ALLOC)
    assert any(it.category == AdvisoryCategory.REVIEW_TRIGGER for it in adv.action_items)


# ── 10. dissenting views reflected ───────────────────────────────────────────

def test_committee_advisory_dissenting_views_reflected():
    prev = _log("PASS", _ALLOC, dissent={})
    curr = _log("PASS", _ALLOC, dissent={"rob_arnott": "QQQM割高に強く反対"})
    cmp = build_comparison(prev, curr)
    adv = build_advisory(_result(final=V.PASS), comparison=cmp, final_allocation=_ALLOC)
    joined = " ".join(it.reason for it in adv.action_items)
    assert "rob_arnott" in joined or any(
        it.category == AdvisoryCategory.RISK_CONTROL for it in adv.action_items
    )


# ── 11. action items limited to five ─────────────────────────────────────────

def test_committee_advisory_action_items_limited_to_five():
    # all members worsen + comparison new triggers -> many candidate items
    prev = _log("PASS", _ALLOC, triggers=["a"])
    curr = _log("WATCH", _ALLOC, triggers=["a", "n1", "n2", "n3"],
                dissent={"buffett": "new concern"})
    cmp = build_comparison(prev, curr)
    adv = build_advisory(
        _result(final=V.WATCH, member_verdicts={m: V.WATCH for m in _CORE + _SAT}),
        comparison=cmp, final_allocation=_ALLOC, ai_audit_status="REJECT",
    )
    assert len(adv.action_items) <= 5


# ── 12. does not change allocation ───────────────────────────────────────────

def test_committee_advisory_does_not_change_allocation():
    alloc = dict(_ALLOC)
    snap = copy.deepcopy(alloc)
    build_advisory(_result(member_verdicts={"rob_arnott": V.WATCH}), final_allocation=alloc)
    assert alloc == snap


# ── 13. allocation_override always false ─────────────────────────────────────

def test_committee_advisory_allocation_override_false():
    adv = build_advisory(_result(), final_allocation=_ALLOC)
    assert adv.allocation_override is False
    # even if forced True via constructor
    adv2 = CommitteeAdvisory(overall_stance=OverallStance.ACCEPT, allocation_override=True)
    assert adv2.allocation_override is False


# ── 14/15. formatters ────────────────────────────────────────────────────────

def test_committee_advisory_slack_section():
    adv = build_advisory(_result(member_verdicts={"rob_arnott": V.WATCH}), final_allocation=_ALLOC)
    s = build_advisory_slack_summary(adv)
    assert "Committee Advisory" in s
    assert "shadow" in s
    assert "売買承認" not in s


def test_committee_advisory_markdown_section():
    adv = build_advisory(_result(member_verdicts={"rob_arnott": V.WATCH}), final_allocation=_ALLOC)
    md = build_advisory_markdown(adv)
    assert "## Committee Advisory" in md
    assert "Do NOT" in md
    assert "最終配分は変更しません" in md
    assert "売買承認" not in md
    # concrete advice, not a generic platitude
    assert "QQQM" in md


# ── 16. no display when disabled (report-level) ──────────────────────────────

def test_committee_advisory_no_display_when_disabled():
    import pandas as pd
    from datetime import date
    from src.report_builder import build_report
    from src.config_loader import load_config
    from src.risk_gate import evaluate_risk_gate

    cfg = load_config("config.yaml")
    idx = pd.date_range("2025-01-01", periods=300, freq="B")
    prices = pd.DataFrame({"VOO": range(1, 301), "BND": range(1, 301)}, index=idx).astype(float)
    indicators = pd.DataFrame(
        {"mom_1m": [0.1, 0.05], "mom_3m": [0.1, 0.05], "mom_6m": [0.1, 0.05],
         "mom_12m": [0.1, 0.05], "vol_20d": [0.1, 0.05]}, index=["VOO", "BND"])
    scores = pd.Series({"VOO": 1.0, "BND": 0.5})
    rg = evaluate_risk_gate(prices, cfg.risk)
    # committee_advisory not passed -> no advisory section
    text = build_report(
        cfg=cfg, weights={"VOO": 0.6, "BND": 0.4}, scores=scores, indicators=indicators,
        prices=prices, risk_gate=rg, prev_weights=None, turnover=None, run_date=date(2026, 6, 5),
    )
    assert "## Committee Advisory" not in text
