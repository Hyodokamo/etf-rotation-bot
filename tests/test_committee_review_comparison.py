"""Tests for Phase 3.3: Committee Review Comparison (deterministic diff)."""
import copy
import json
from pathlib import Path

from src.committee.review_comparison import (
    build_comparison,
    build_comparison_markdown,
    build_comparison_slack_summary,
    compare_latest_committee_runs,
)


# ── log entry fixtures ───────────────────────────────────────────────────────

def _entry(
    run_id="r1",
    date="2026-05-05",
    final="PASS",
    core="PASS",
    sat="PASS",
    members=None,
    allocation=None,
    dissent=None,
    triggers=None,
    recommended="現状維持",
    human_decision=None,
    allocation_override=False,
):
    return {
        "schema_version": "1.0",
        "run_id": run_id,
        "date": date,
        "final_committee_verdict": final,
        "core_committee_verdict": core,
        "satellite_committee_verdict": sat,
        "recommended_action": recommended,
        "allocation_override": allocation_override,
        "human_decision": human_decision,
        "member_outputs": members or [
            {"member_id": "aqr_meb", "verdict": "PASS"},
            {"member_id": "howard_marks", "verdict": "PASS"},
            {"member_id": "rob_arnott", "verdict": "PASS"},
            {"member_id": "core_ai_auditor", "verdict": "PASS"},
            {"member_id": "buffett", "verdict": "PASS"},
        ],
        "final_allocation": allocation or {"BND": 0.40, "VOO": 0.35, "VTV": 0.25},
        "dissenting_views": dissent or {},
        "next_review_triggers": triggers or [],
    }


def _write_log(tmp_path, *entries) -> Path:
    p = tmp_path / "committee_decision_log.jsonl"
    with open(p, "w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    return p


# ── 1. requires two entries ──────────────────────────────────────────────────

def test_committee_review_comparison_requires_two_entries(tmp_path):
    p = _write_log(tmp_path, _entry())
    assert compare_latest_committee_runs(p) is None
    p2 = tmp_path / "empty.jsonl"
    assert compare_latest_committee_runs(p2) is None


# ── 2. verdict change ────────────────────────────────────────────────────────

def test_committee_review_comparison_verdict_change():
    prev = _entry(final="PASS", core="PASS")
    curr = _entry(final="WATCH", core="WATCH")
    cmp = build_comparison(prev, curr)
    assert cmp.final_committee_verdict_change.previous == "PASS"
    assert cmp.final_committee_verdict_change.current == "WATCH"
    assert cmp.final_committee_verdict_change.changed is True
    assert cmp.final_committee_verdict_change.direction == "WORSENED"
    assert cmp.core_committee_verdict_change.direction == "WORSENED"


def test_recommended_action_change_detected():
    prev = _entry(recommended="維持")
    curr = _entry(recommended="監視を継続")
    cmp = build_comparison(prev, curr)
    assert cmp.recommended_action_change["changed"] is True


# ── 3. member verdict changes ────────────────────────────────────────────────

def test_committee_review_comparison_member_verdict_changes():
    prev = _entry(members=[{"member_id": "aqr_meb", "verdict": "PASS"},
                           {"member_id": "howard_marks", "verdict": "PASS"}])
    curr = _entry(members=[{"member_id": "aqr_meb", "verdict": "WATCH"},
                           {"member_id": "howard_marks", "verdict": "PASS"}])
    cmp = build_comparison(prev, curr)
    by_id = {m.member_id: m for m in cmp.member_verdict_changes}
    assert by_id["aqr_meb"].changed is True
    assert by_id["aqr_meb"].direction == "WORSENED"
    assert by_id["howard_marks"].changed is False


# ── 4. allocation changes ────────────────────────────────────────────────────

def test_committee_review_comparison_allocation_changes():
    prev = _entry(allocation={"BND": 0.40, "VOO": 0.35, "VTV": 0.25})
    curr = _entry(allocation={"BND": 0.30, "VOO": 0.45, "VTV": 0.25})
    cmp = build_comparison(prev, curr)
    by_t = {a.ticker: a for a in cmp.allocation_changes}
    assert by_t["BND"].direction == "DECREASED"
    assert abs(by_t["BND"].diff_pct_point - (-10.0)) < 1e-6
    assert by_t["VOO"].direction == "INCREASED"
    assert by_t["VTV"].direction == "UNCHANGED"


# ── 5. added / removed assets ────────────────────────────────────────────────

def test_committee_review_comparison_added_removed_assets():
    prev = _entry(allocation={"BND": 0.50, "VOO": 0.50})
    curr = _entry(allocation={"VOO": 0.50, "QQQM": 0.50})
    cmp = build_comparison(prev, curr)
    by_t = {a.ticker: a for a in cmp.allocation_changes}
    assert by_t["QQQM"].direction == "ADDED"
    assert by_t["BND"].direction == "REMOVED"


# ── 6 & 7. trigger diffs ─────────────────────────────────────────────────────

def test_committee_review_comparison_new_next_review_triggers():
    prev = _entry(triggers=["VOO 200日線維持"])
    curr = _entry(triggers=["VOO 200日線維持", "QQQM相対強度", "BND 30%維持"])
    cmp = build_comparison(prev, curr)
    assert "QQQM相対強度" in cmp.new_next_review_triggers
    assert "BND 30%維持" in cmp.new_next_review_triggers
    assert "VOO 200日線維持" not in cmp.new_next_review_triggers


def test_committee_review_comparison_resolved_next_review_triggers():
    prev = _entry(triggers=["A", "B", "C"])
    curr = _entry(triggers=["A"])
    cmp = build_comparison(prev, curr)
    assert set(cmp.resolved_next_review_triggers) == {"B", "C"}


# ── 8. dissent diffs ─────────────────────────────────────────────────────────

def test_committee_review_comparison_new_dissenting_views():
    prev = _entry(dissent={"aqr_meb": "trend ok"})
    curr = _entry(dissent={"aqr_meb": "trend ok", "rob_arnott": "QQQM overvalued"})
    cmp = build_comparison(prev, curr)
    assert "rob_arnott" in cmp.new_dissenting_views
    assert "aqr_meb" not in cmp.new_dissenting_views  # unchanged text


def test_resolved_dissenting_views():
    prev = _entry(dissent={"buffett": "theme risk"})
    curr = _entry(dissent={})
    cmp = build_comparison(prev, curr)
    assert "buffett" in cmp.resolved_dissenting_views


# ── 9/10/11. severity ────────────────────────────────────────────────────────

def test_committee_review_comparison_severity_none():
    cmp = build_comparison(_entry(), _entry())
    assert cmp.severity == "NONE"


def test_committee_review_comparison_severity_caution():
    # two members worsen to WATCH (incl. key members) but final stays PASS-ish,
    # no single-asset >=10pt move
    prev = _entry(
        final="PASS_WITH_CAUTION", core="PASS_WITH_CAUTION",
        members=[{"member_id": "howard_marks", "verdict": "PASS"},
                 {"member_id": "rob_arnott", "verdict": "PASS"},
                 {"member_id": "aqr_meb", "verdict": "PASS_WITH_CAUTION"}],
        allocation={"BND": 0.40, "VOO": 0.60},
    )
    curr = _entry(
        final="PASS_WITH_CAUTION", core="PASS_WITH_CAUTION",
        members=[{"member_id": "howard_marks", "verdict": "WATCH"},
                 {"member_id": "rob_arnott", "verdict": "WATCH"},
                 {"member_id": "aqr_meb", "verdict": "PASS_WITH_CAUTION"}],
        allocation={"BND": 0.40, "VOO": 0.60},
    )
    cmp = build_comparison(prev, curr)
    assert cmp.severity == "CAUTION"


def test_committee_review_comparison_severity_material():
    # final verdict worsens into WATCH -> MATERIAL
    prev = _entry(final="PASS", core="PASS")
    curr = _entry(final="WATCH", core="WATCH")
    cmp = build_comparison(prev, curr)
    assert cmp.severity == "MATERIAL"


def test_severity_material_on_big_allocation_move():
    prev = _entry(allocation={"BND": 0.40, "VOO": 0.60})
    curr = _entry(allocation={"BND": 0.25, "VOO": 0.75})  # 15pt move
    cmp = build_comparison(prev, curr)
    assert cmp.severity == "MATERIAL"


def test_severity_material_on_override_true_audit():
    prev = _entry()
    curr = _entry(allocation_override=True)
    cmp = build_comparison(prev, curr)
    assert cmp.severity == "MATERIAL"


def test_severity_info_on_minor_verdict_change():
    prev = _entry(final="PASS", core="PASS")
    curr = _entry(final="PASS_WITH_CAUTION", core="PASS_WITH_CAUTION")
    cmp = build_comparison(prev, curr)
    assert cmp.severity == "INFO"


# ── 12. skips corrupt log lines ──────────────────────────────────────────────

def test_committee_review_comparison_skips_corrupt_log_lines(tmp_path):
    p = tmp_path / "log.jsonl"
    with open(p, "w", encoding="utf-8") as f:
        f.write(json.dumps(_entry(run_id="r1", final="PASS")) + "\n")
        f.write("{ corrupt line not json\n")
        f.write(json.dumps(_entry(run_id="r2", final="WATCH")) + "\n")
    cmp = compare_latest_committee_runs(p)
    assert cmp is not None
    assert cmp.previous_run_id == "r1"
    assert cmp.current_run_id == "r2"
    assert cmp.final_committee_verdict_change.direction == "WORSENED"


# ── 13. does not change allocation ───────────────────────────────────────────

def test_committee_review_comparison_does_not_change_allocation():
    prev_alloc = {"BND": 0.40, "VOO": 0.60}
    curr_alloc = {"BND": 0.30, "VOO": 0.70}
    prev = _entry(allocation=prev_alloc)
    curr = _entry(allocation=curr_alloc)
    prev_snap, curr_snap = copy.deepcopy(prev_alloc), copy.deepcopy(curr_alloc)
    build_comparison(prev, curr)
    # source allocation dicts untouched
    assert prev_alloc == prev_snap
    assert curr_alloc == curr_snap


# ── 14. slack summary ────────────────────────────────────────────────────────

def test_committee_review_comparison_slack_summary():
    cmp = build_comparison(_entry(final="PASS"), _entry(final="WATCH"))
    s = build_comparison_slack_summary(cmp)
    assert "Committee 変化サマリー" in s
    assert "WATCH" in s
    assert "shadow" in s
    assert s.count("\n") <= 4  # concise: <= 5 lines


# ── 15. markdown section ─────────────────────────────────────────────────────

def test_committee_review_comparison_markdown_section():
    prev = _entry(final="PASS", allocation={"BND": 0.40, "VOO": 0.60})
    curr = _entry(final="WATCH", allocation={"BND": 0.25, "VOO": 0.75})
    md = build_comparison_markdown(build_comparison(prev, curr))
    assert "Committee Review Comparison" in md
    assert "MATERIAL" in md
    assert "判定の変化" in md
    assert "配分の変化" in md
    assert "最終配分には影響しません" in md
