"""Tests for Phase 3.6.1: Slack Executive Digest."""
import copy

from src.committee.advisory import (
    ActionItem, AdvisoryCategory, AdvisoryPriority, CommitteeAdvisory, OverallStance,
)
from src.committee.models import (
    CommitteeResult, CommitteeTier, CommitteeVerdict, MemberOutput,
)
from src.committee.slack_digest import (
    build_advisory_top,
    build_agent_one_liners,
    build_committee_debate_highlights,
    build_executive_digest,
    select_top_dissenting_views,
    select_top_review_triggers,
)

V = CommitteeVerdict
_CORE = ["aqr_meb", "howard_marks", "rob_arnott", "core_ai_auditor"]
_SAT = ["buffett", "paul_tudor_jones", "druckenmiller"]
_ALL = _CORE + _SAT


def _m(mid, tier, verdict=V.PASS, support="", objection="", dissent="", rationale="",
       risks=None, triggers=None):
    return MemberOutput(
        member_id=mid, display_name=mid, tier=tier, verdict=verdict, confidence=0.8,
        rationale=rationale, strongest_support=support, strongest_objection=objection,
        dissenting_view=dissent, key_risks=risks or [], required_checks=["c"],
        next_review_triggers=triggers or [], action_implication="advisory",
    )


def _committee(verdicts=None, dissents=None, triggers=None):
    verdicts = verdicts or {}
    dissents = dissents or {}
    members = []
    for mid in _CORE:
        members.append(_m(mid, CommitteeTier.CORE, verdicts.get(mid, V.PASS),
                          support=f"{mid} は VOO 中心で許容",
                          objection=f"{mid} objection",
                          dissent=dissents.get(mid, ""),
                          rationale=f"{mid} rationale"))
    for mid in _SAT:
        members.append(_m(mid, CommitteeTier.SATELLITE, verdicts.get(mid, V.PASS),
                          support=f"{mid} は VTV 中心で長期保有可", dissent=dissents.get(mid, "")))
    return CommitteeResult(
        core_committee_verdict=V.WATCH, satellite_committee_verdict=V.PASS_WITH_CAUTION,
        final_committee_verdict=V.WATCH, recommended_action="現状維持（shadow）",
        allocation_override=False, summary="s",
        next_review_triggers=triggers or ["QQQMの3mモメンタムが0割れ", "S&P500 60日リターン失速", "BNDが30%割れ", "汎用トリガー"],
        members=members, shadow_mode=True, llm_call_mode="batch", satellite_activated=True,
    )


def _advisory():
    return CommitteeAdvisory(
        overall_stance=OverallStance.WAIT_FOR_REVIEW,
        action_items=[
            ActionItem(category=AdvisoryCategory.BUY_DISCIPLINE, priority=AdvisoryPriority.HIGH,
                       message="QQQMの追加購入は今月控える", reason="r", review_trigger="t"),
            ActionItem(category=AdvisoryCategory.RISK_CONTROL, priority=AdvisoryPriority.MEDIUM,
                       message="BNDは30%以上維持", reason="r", review_trigger="t"),
            ActionItem(category=AdvisoryCategory.REVIEW_TRIGGER, priority=AdvisoryPriority.MEDIUM,
                       message="VTVの相対モメンタムを監視", reason="r", review_trigger="t"),
            ActionItem(category=AdvisoryCategory.HOLD_DISCIPLINE, priority=AdvisoryPriority.LOW,
                       message="低優先の項目", reason="r", review_trigger="t"),
        ],
        next_review_focus=["QQQM相対強度"],
        generated_from={}, allocation_override=False,
    )


_WEIGHTS = {"BND": 0.376, "VOO": 0.346, "VTV": 0.151, "QQQM": 0.127}


def _digest(committee=None, advisory=None, **kw):
    return build_executive_digest(
        weights=_WEIGHTS, risk_off=False, turnover=0.20, report_path="outputs/report.md",
        strategy_variant="cash_fallback_separated",
        committee_result=committee, committee_advisory=advisory, **kw,
    )


# ── executive summary ────────────────────────────────────────────────────────

def test_slack_digest_contains_executive_summary():
    d = _digest(_committee(), _advisory())
    assert "今月の結論" in d
    assert "Investment Committee Digest" in d
    assert "Top配分" in d


def test_slack_digest_contains_committee_debate_highlights():
    d = _digest(_committee({"rob_arnott": V.WATCH}), _advisory())
    assert "Committee 論点" in d
    assert "Rob Arnott" in d


# ── top-3 limits ─────────────────────────────────────────────────────────────

def test_slack_digest_limits_dissenting_views_to_top3():
    dissents = {mid: f"{mid} は QQQM 18% の高バリュエーションに反対" for mid in _ALL}
    views = select_top_dissenting_views(_committee(dissents=dissents), top=3)
    assert len(views) == 3


def test_slack_digest_limits_review_triggers_to_top3():
    trigs = select_top_review_triggers(_committee(), top=3)
    assert len(trigs) <= 3


# ── prioritization ───────────────────────────────────────────────────────────

def test_slack_digest_prioritizes_watch_members():
    dissents = {
        "buffett": "buffett 一般的な懸念",
        "rob_arnott": "rob_arnott QQQM 18% の高バリュエーションに強く反対",
    }
    views = select_top_dissenting_views(
        _committee(verdicts={"rob_arnott": V.WATCH}, dissents=dissents), top=3)
    # WATCH member's concrete dissent should rank first
    assert "Rob Arnott" in views[0]


def test_slack_digest_includes_ai_auditor_when_available():
    highlights = build_committee_debate_highlights(
        _committee({"rob_arnott": V.WATCH, "howard_marks": V.WATCH}), max_points=4)
    assert any("AI Auditor" in h for h in highlights)


# ── advisory top3 ────────────────────────────────────────────────────────────

def test_slack_digest_includes_advisory_top3():
    d = _digest(_committee(), _advisory())
    assert "Committee Advisory" in d
    assert "QQQMの追加購入は今月控える" in d
    top = build_advisory_top(_advisory(), top=3)
    assert len(top) == 3
    assert top[0].startswith("[HIGH]")  # HIGH first


# ── no full outputs ──────────────────────────────────────────────────────────

def test_slack_digest_does_not_include_full_member_outputs():
    marker = "X" * 200  # long, no concrete tokens
    c = _committee()
    c.members[0].rationale = marker
    c.members[0].strongest_support = marker
    d = _digest(c, _advisory())
    assert marker not in d  # truncated


# ── allocation safety ────────────────────────────────────────────────────────

def test_slack_digest_does_not_change_allocation():
    w = dict(_WEIGHTS)
    snap = copy.deepcopy(w)
    build_executive_digest(weights=w, risk_off=False, turnover=0.2, report_path="r",
                           committee_result=_committee(), committee_advisory=_advisory())
    assert w == snap


# ── missing committee / advisory ─────────────────────────────────────────────

def test_slack_digest_handles_missing_committee():
    d = _digest(committee=None, advisory=None)
    assert "今月の結論" in d
    assert "Committee 論点" not in d  # no committee section
    assert "Top配分" in d


def test_slack_digest_handles_missing_advisory():
    d = _digest(_committee(), advisory=None)
    assert "Committee Advisory" not in d
    assert "今月の結論" in d


# ── candidate-ready structure ────────────────────────────────────────────────

def test_slack_digest_candidate_ready_structure():
    # selectors must accept a plain list of member dicts (candidate review path)
    member_dicts = [
        {"member_id": "aqr_meb", "verdict": "WATCH", "confidence": 0.8,
         "dissenting_view": "QQQM 18% に反対", "strongest_support": "",
         "rationale": "r", "key_risks": ["k"], "next_review_triggers": ["QQQM相対強度"]},
        {"member_id": "core_ai_auditor", "verdict": "PASS_WITH_CAUTION", "confidence": 0.9,
         "dissenting_view": "データ不足", "rationale": "監査", "key_risks": [],
         "next_review_triggers": []},
    ]
    one_liners = build_agent_one_liners(member_dicts)
    assert {o["member_id"] for o in one_liners} == {"aqr_meb", "core_ai_auditor"}
    assert select_top_review_triggers(member_dicts, top=3)  # gathered from members
    assert build_committee_debate_highlights(member_dicts)


# ── per-agent one-liners ─────────────────────────────────────────────────────

def test_slack_digest_contains_each_agent_one_liner():
    d = _digest(_committee(), _advisory())
    assert "各エージェントの一言" in d


def test_slack_digest_agent_one_liners_include_all_members():
    one_liners = build_agent_one_liners(_committee())
    ids = {o["member_id"] for o in one_liners}
    assert ids == set(_ALL)  # all 7 members


def test_slack_digest_agent_one_liners_are_limited_length():
    long_dissent = {mid: "とても長い反対意見 " * 30 for mid in _ALL}
    one_liners = build_agent_one_liners(_committee(verdicts={m: V.WATCH for m in _ALL}, dissents=long_dissent))
    for o in one_liners:
        assert len(o["text"]) <= 80


def test_slack_digest_agent_one_liners_prioritize_concrete_terms():
    # member with both a generic and a concrete field -> concrete chosen
    c = _committee()
    aqr = next(m for m in c.members if m.member_id == "aqr_meb")
    aqr.verdict = V.WATCH
    aqr.strongest_objection = "一般的な懸念"
    aqr.dissenting_view = "QQQM 18% の高バリュエーションに反対"
    one_liners = {o["member_id"]: o for o in build_agent_one_liners(c)}
    assert "QQQM" in one_liners["aqr_meb"]["text"]


def test_slack_digest_agent_one_liners_do_not_include_full_outputs():
    marker = "Z" * 200
    c = _committee(verdicts={"buffett": V.WATCH})
    bf = next(m for m in c.members if m.member_id == "buffett")
    bf.dissenting_view = marker
    one_liners = {o["member_id"]: o for o in build_agent_one_liners(c)}
    assert marker not in one_liners["buffett"]["text"]


def test_slack_digest_ai_auditor_one_liner_quality_focus():
    c = _committee()
    auditor = next(m for m in c.members if m.member_id == "core_ai_auditor")
    auditor.rationale = "全資産は上限内"  # no quality term
    auditor.dissenting_view = ""
    one_liners = {o["member_id"]: o for o in build_agent_one_liners(c)}
    text = one_liners["core_ai_auditor"]["text"]
    assert any(k in text for k in ("品質", "データ", "ルール", "過剰最適化", "根拠"))
