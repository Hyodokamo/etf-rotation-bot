"""Tests for Phase 3.5: Candidate Review."""
import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.committee.candidate_review import (
    Candidate,
    CandidateAction,
    CandidateReviewResult,
    CandidateVerdict,
    build_candidate_markdown,
    build_candidate_slack_summary,
    filter_candidates,
    load_watchlist,
    map_candidate_verdict,
    review_candidate,
)
from src.committee.models import (
    CommitteeConfig,
    CommitteeMemberConfig,
    CommitteeVerdict,
)
from src.llm.base import BaseLlmClient

CV = CommitteeVerdict
_CORE = ["aqr_meb", "howard_marks", "rob_arnott", "core_ai_auditor"]
_SAT = ["buffett", "paul_tudor_jones", "druckenmiller"]

_CSV = (
    "symbol,name,asset_type,theme,candidate_action,intended_amount_jpy,account,reason,time_horizon,notes\n"
    "GRID,Smart Grid ETF,theme_etf,clean_energy_grid,NEW_BUY,300000,NISA,grid demand,long,note\n"
    "BOTZ,Robotics ETF,theme_etf,robotics_ai,NEW_BUY,200000,NISA,ai growth,long,overlap?\n"
)


def _write_csv(tmp_path, text=_CSV) -> Path:
    p = tmp_path / "wl.csv"
    p.write_text(text, encoding="utf-8")
    return p


def _cfg():
    return CommitteeConfig(
        enabled=True, satellite_activation="always",
        core_committee=[CommitteeMemberConfig(member_id=i, display_name=i, focus="f") for i in _CORE],
        satellite_committee=[CommitteeMemberConfig(member_id=i, display_name=i, focus="f") for i in _SAT],
    )


def _member_json(mid, verdict="PASS"):
    return {
        "member_id": mid, "verdict": verdict, "confidence": 0.7,
        "rationale": "r", "strongest_support": f"{mid} buy thesis",
        "strongest_objection": f"{mid} objection",
        "dissenting_view": f"{mid} dissent", "key_risks": [f"{mid}_risk"],
        "required_checks": [f"chk_{mid}"], "next_review_triggers": [f"trig_{mid}"],
        "action_implication": "advisory",
    }


class _Client(BaseLlmClient):
    def __init__(self, verdicts=None):
        self.verdicts = verdicts or {}

    def complete(self, system, user, max_tokens=4096):
        ids = [i for i in (_CORE + _SAT) if i in user] or (_CORE + _SAT)
        return json.dumps([_member_json(i, self.verdicts.get(i, "PASS")) for i in ids])


_TREND = {"last": 30.0, "above_200dma": True, "ret_3m": 0.08}


def _cand(symbol="GRID", action="NEW_BUY", amount=300000.0):
    return Candidate(symbol=symbol, name=f"{symbol} ETF", asset_type="theme_etf",
                     theme="robotics_ai", candidate_action=action,
                     intended_amount_jpy=amount, account="NISA", reason="growth",
                     time_horizon="long", notes="n")


_DEFAULT = object()


def _review(verdicts=None, trend=_TREND, holdings=None, cats=None, client=_DEFAULT):
    use_client = _Client(verdicts) if client is _DEFAULT else client
    return review_candidate(
        _cand(), _cfg(), use_client,
        portfolio_holdings=holdings or {"QQQM": 0.13},
        universe_categories=cats or {"QQQM": "growth_equity"},
        price_trend=trend, review_date="2026-06-05",
    )


# ── CSV ──────────────────────────────────────────────────────────────────────

def test_candidate_review_loads_watchlist_csv(tmp_path):
    cands = load_watchlist(_write_csv(tmp_path))
    assert [c.symbol for c in cands] == ["GRID", "BOTZ"]
    assert cands[0].candidate_action == CandidateAction.NEW_BUY
    assert cands[0].intended_amount_jpy == 300000.0


def test_candidate_review_validates_required_columns(tmp_path):
    bad = "symbol,name,candidate_action\nGRID,x,NEW_BUY\n"
    with pytest.raises(ValueError, match="missing required columns"):
        load_watchlist(_write_csv(tmp_path, bad))


def test_candidate_review_rejects_invalid_candidate_action(tmp_path):
    bad = _CSV.replace("NEW_BUY,300000", "FOO_BUY,300000")
    with pytest.raises(ValueError):
        load_watchlist(_write_csv(tmp_path, bad))


def test_candidate_review_filters_by_symbol(tmp_path):
    cands = load_watchlist(_write_csv(tmp_path))
    assert [c.symbol for c in filter_candidates(cands, "BOTZ")] == ["BOTZ"]
    assert [c.symbol for c in filter_candidates(cands, "botz")] == ["BOTZ"]
    assert len(filter_candidates(cands, None)) == 2


def test_candidate_review_symbol_filter_single_candidate(tmp_path):
    cands = load_watchlist(_write_csv(tmp_path))
    filtered = filter_candidates(cands, "GRID")
    assert len(filtered) == 1
    assert filtered[0].symbol == "GRID"


# ── verdict / result ─────────────────────────────────────────────────────────

def test_candidate_review_generates_review_result():
    r = _review()
    assert isinstance(r, CandidateReviewResult)
    assert r.candidate["symbol"] == "GRID"
    assert r.candidate_verdict == CandidateVerdict.APPROVE_SMALL_TEST_BUY  # all PASS + trend
    assert r.strongest_buy_thesis
    assert r.final_advisory


def test_candidate_review_verdict_enum_validation():
    for v in CandidateVerdict:
        res = CandidateReviewResult(
            review_id="x", review_date="2026-06-05", candidate={"symbol": "GRID"},
            candidate_verdict=v.value,
        )
        assert res.candidate_verdict == v
    with pytest.raises(ValidationError):
        CandidateReviewResult(review_id="x", review_date="d", candidate={}, candidate_verdict="MAYBE")


def test_candidate_review_allocation_override_false():
    r = _review()
    assert r.allocation_override is False
    forced = CandidateReviewResult(
        review_id="x", review_date="d", candidate={}, candidate_verdict="REJECT_FOR_NOW",
        allocation_override=True,
    )
    assert forced.allocation_override is False


def test_candidate_review_does_not_change_final_allocation():
    holdings = {"QQQM": 0.13, "VOO": 0.30}
    snap = copy.deepcopy(holdings)
    r = _review(holdings=holdings)
    # source holdings untouched; result carries no allocation/weights to apply
    assert holdings == snap
    d = r.to_dict()
    assert "final_allocation" not in d and "weights" not in d and "new_weights" not in d


def test_candidate_review_does_not_calculate_order_quantity():
    r = _review()
    assert "注文数量" in r.sizing_note
    # explicitly states the consideration amount is NOT converted to shares
    assert "株数には変換しません" in r.sizing_note
    # intended amount preserved as consideration only, not converted to shares
    assert r.candidate["intended_amount_jpy_consideration_only"] == 300000.0


def test_candidate_review_requires_dissenting_view():
    r = _review()
    assert r.member_outputs
    assert all(m.get("dissenting_view") for m in r.member_outputs)
    assert r.strongest_rejection_thesis


def test_candidate_review_requires_next_review_triggers():
    r = _review()
    assert all(m.get("next_review_triggers") for m in r.member_outputs)


def test_candidate_review_handles_insufficient_data():
    r = _review(client=None)  # no LLM client -> committee INSUFFICIENT_DATA
    assert r.candidate_verdict == CandidateVerdict.INSUFFICIENT_DATA
    assert r.allocation_override is False


def test_candidate_review_runs_core_and_satellite():
    r = _review()
    tiers = {m["tier"] for m in r.member_outputs}
    ids = {m["member_id"] for m in r.member_outputs}
    assert "core" in tiers and "satellite" in tiers
    assert set(_CORE).issubset(ids) and set(_SAT).issubset(ids)


# ── theme checks ──────────────────────────────────────────────────────────────

def test_candidate_review_theme_candidate_checks_overlap():
    # overlapping growth-equity holding -> overlap suspected
    r = _review(holdings={"QQQM": 0.13}, cats={"QQQM": "growth_equity"})
    assert any("重複" in c for c in r.required_checks)
    assert any("重複" in risk for risk in r.key_risks)


def test_candidate_review_theme_candidate_checks_invalidation_conditions():
    r = _review()
    assert len(r.invalidation_conditions) >= 1
    assert any("200日" in c for c in r.invalidation_conditions)


# ── verdict mapping unit ─────────────────────────────────────────────────────

def test_map_candidate_verdict_strict():
    assert map_candidate_verdict(CV.PASS, CV.PASS, True) == CandidateVerdict.APPROVE_SMALL_TEST_BUY
    assert map_candidate_verdict(CV.PASS, CV.PASS, False) == CandidateVerdict.APPROVE_FOR_WATCHLIST
    assert map_candidate_verdict(CV.PASS_WITH_CAUTION, CV.PASS, True) == CandidateVerdict.APPROVE_FOR_WATCHLIST
    assert map_candidate_verdict(CV.WATCH, CV.PASS, True) == CandidateVerdict.WAIT_FOR_BETTER_ENTRY
    assert map_candidate_verdict(CV.REJECT, CV.PASS, True) == CandidateVerdict.REJECT_FOR_NOW
    assert map_candidate_verdict(CV.PASS, CV.REJECT, True) == CandidateVerdict.INSUFFICIENT_DATA
    # auditor WATCH downgrades one notch
    assert map_candidate_verdict(CV.PASS, CV.WATCH, True) == CandidateVerdict.APPROVE_FOR_WATCHLIST


# ── formatters ────────────────────────────────────────────────────────────────

def test_candidate_review_markdown_report():
    r = _review()
    md = build_candidate_markdown([r], "2026-06-05")
    assert "Candidate Review" in md
    assert "GRID" in md
    assert r.candidate_verdict.value in md
    assert "最も強い買い根拠" in md
    assert "反証・無効化条件" in md
    assert "sizing_note" in md
    assert "売買承認" not in md
    assert "自動売買・証券口座連携は行いません" in md


def test_candidate_review_slack_summary():
    r = _review()
    s = build_candidate_slack_summary(r)
    assert "GRID" in s
    assert r.candidate_verdict.value in s
    assert "shadow" in s
    assert "売買承認" not in s
