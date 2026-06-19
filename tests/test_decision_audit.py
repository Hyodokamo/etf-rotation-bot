"""Tests for Phase 5: Integrated Decision Audit Summary."""
import json
from pathlib import Path

from src.decision_audit import (
    build_audit_markdown,
    build_decision_audit,
    classify_alignment,
    save_audit_report,
)


def _committee_entry(month="2026-06", final="WATCH", human=None, note=None, triggers=None):
    return {
        "schema_version": "1.0", "timestamp": f"{month}-05T09:00:00+09:00",
        "date": f"{month}-05", "final_committee_verdict": final,
        "core_committee_verdict": final, "satellite_committee_verdict": "PASS_WITH_CAUTION",
        "human_decision": human, "human_note": note,
        "next_review_triggers": triggers or ["QQQMの3mモメンタムが0割れ"],
        "run_id": f"{month}-05",
    }


def _candidate_entry(symbol="GRID", month="2026-06", verdict="REJECT_FOR_NOW", human=None,
                     entry_type=None, note=None, checks=None):
    e = {
        "schema_version": "1.0", "timestamp": f"{month}-06T09:00:00+09:00",
        "review_date": f"{month}-06", "candidate_symbol": symbol,
        "candidate_verdict": verdict, "human_decision": human,
        "required_checks": checks or ["既存ポートフォリオとの重複"],
        "member_outputs": [{"member_id": "rob_arnott", "next_review_triggers": ["QQQM相対強度"]}],
    }
    if entry_type:
        e["entry_type"] = entry_type
    if note:
        e["human_note"] = note
    return e


def _slack_entry(month="2026-06", source="candidate_review", action="candidate_wait",
                 human="WAIT", symbol="GRID", entry_type=None, note=None, user="U1"):
    e = {
        "schema_version": "1.0", "timestamp": f"{month}-07T09:00:00+09:00",
        "source_type": source, "action_id": action, "human_decision": human,
        "user_id": user, "candidate_symbol": symbol,
    }
    if entry_type:
        e["entry_type"] = entry_type
    if note:
        e["human_note"] = note
    return e


def _write(path, *entries):
    with open(path, "w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    return path


def _audit(tmp_path, committee=(), candidate=(), slack=(), month="2026-06"):
    cl = _write(tmp_path / "committee.jsonl", *committee)
    cal = _write(tmp_path / "candidate.jsonl", *candidate)
    sl = _write(tmp_path / "slack.jsonl", *slack)
    return build_decision_audit(month, committee_log_path=cl, candidate_log_path=cal, slack_log_path=sl)


# ── loading ──────────────────────────────────────────────────────────────────

def test_decision_audit_loads_committee_logs(tmp_path):
    a = _audit(tmp_path, committee=[_committee_entry(final="WATCH", human="REVIEW_CONFIRMED")])
    assert a.monthly.committee_verdict == "WATCH"
    assert any(h.decision == "REVIEW_CONFIRMED" for h in a.monthly.human_decisions)


def test_decision_audit_loads_candidate_logs(tmp_path):
    a = _audit(tmp_path, candidate=[_candidate_entry("GRID", verdict="REJECT_FOR_NOW")])
    assert any(c.symbol == "GRID" and c.ai_verdict == "REJECT_FOR_NOW" for c in a.candidates)


def test_decision_audit_handles_missing_logs():
    a = build_decision_audit("2099-01", committee_log_path="no1.jsonl",
                             candidate_log_path="no2.jsonl", slack_log_path="no3.jsonl")
    assert a.candidates == [] and a.monthly.committee_verdict is None


def test_decision_audit_skips_corrupt_lines(tmp_path):
    cal = tmp_path / "candidate.jsonl"
    with open(cal, "w", encoding="utf-8") as f:
        f.write(json.dumps(_candidate_entry("GRID")) + "\n")
        f.write("{ corrupt\n")
        f.write(json.dumps(_candidate_entry("BOTZ")) + "\n")
    a = build_decision_audit("2026-06", committee_log_path=tmp_path / "x.jsonl",
                             candidate_log_path=cal, slack_log_path=tmp_path / "y.jsonl")
    syms = {c.symbol for c in a.candidates}
    assert {"GRID", "BOTZ"} <= syms


def test_decision_audit_filters_by_month(tmp_path):
    a = _audit(tmp_path,
               candidate=[_candidate_entry("GRID", month="2026-06"),
                          _candidate_entry("OLD", month="2026-05")],
               month="2026-06")
    syms = {c.symbol for c in a.candidates}
    assert "GRID" in syms and "OLD" not in syms


# ── summaries ────────────────────────────────────────────────────────────────

def test_decision_audit_summarizes_monthly_decision(tmp_path):
    a = _audit(tmp_path, committee=[_committee_entry(final="PASS_WITH_CAUTION", human="SKIP_THIS_MONTH")])
    assert a.monthly.committee_verdict == "PASS_WITH_CAUTION"
    assert a.monthly.human_decisions[0].decision == "SKIP_THIS_MONTH"


def test_decision_audit_summarizes_candidate_decisions(tmp_path):
    a = _audit(tmp_path,
               candidate=[_candidate_entry("GRID", verdict="REJECT_FOR_NOW")],
               slack=[_slack_entry(action="candidate_wait", human="WAIT", symbol="GRID")])
    grid = next(c for c in a.candidates if c.symbol == "GRID")
    assert grid.ai_verdict == "REJECT_FOR_NOW"
    assert grid.human_decision == "WAIT"


# ── alignment / divergence ───────────────────────────────────────────────────

def test_decision_audit_detects_ai_human_alignment(tmp_path):
    a = _audit(tmp_path,
               candidate=[_candidate_entry("GRID", verdict="REJECT_FOR_NOW", human="REJECT")])
    grid = next(c for c in a.candidates if c.symbol == "GRID")
    assert grid.alignment == "aligned"
    assert grid not in a.divergences


def test_decision_audit_detects_ai_human_divergence(tmp_path):
    a = _audit(tmp_path,
               candidate=[_candidate_entry("GRID", verdict="REJECT_FOR_NOW")],
               slack=[_slack_entry(action="candidate_wait", human="WAIT", symbol="GRID")])
    grid = next(c for c in a.candidates if c.symbol == "GRID")
    assert grid.alignment == "divergence"
    assert any(c.symbol == "GRID" for c in a.divergences)


def test_classify_alignment_rules():
    assert classify_alignment("REJECT_FOR_NOW", "REJECT") == "aligned"
    assert classify_alignment("REJECT_FOR_NOW", "WAIT") == "divergence"
    assert classify_alignment("WAIT_FOR_BETTER_ENTRY", "WATCHLIST") == "mild_divergence"
    assert classify_alignment("APPROVE_SMALL_TEST_BUY", "REJECT") == "divergence"
    assert classify_alignment("REJECT_FOR_NOW", None) == "no_human_decision"


# ── unstable ─────────────────────────────────────────────────────────────────

def test_decision_audit_detects_unstable_candidates(tmp_path):
    # two GRID reviews WAIT -> REJECT => UNSTABLE via stability check
    a = _audit(tmp_path, candidate=[
        _candidate_entry("GRID", verdict="WAIT_FOR_BETTER_ENTRY"),
        _candidate_entry("GRID", verdict="REJECT_FOR_NOW"),
    ])
    assert any(c.symbol == "GRID" for c in a.unstable_candidates)


# ── notes / next review ──────────────────────────────────────────────────────

def test_decision_audit_includes_human_notes(tmp_path):
    a = _audit(tmp_path,
               candidate=[_candidate_entry("GRID")],
               slack=[_slack_entry(entry_type="note", action="candidate_add_note",
                                   human="ADD_NOTE", note="重複懸念のメモ", symbol="GRID")])
    assert any("重複懸念" in n.note for n in a.human_notes)


def test_decision_audit_extracts_next_review_items(tmp_path):
    a = _audit(tmp_path,
               committee=[_committee_entry(triggers=["VOOが200日線を割る"])],
               candidate=[_candidate_entry("GRID", checks=["価格トレンド確認"])])
    joined = " ".join(a.next_review_items)
    assert "200日線" in joined or "価格トレンド" in joined
    assert a.next_review_items


# ── markdown / cli ───────────────────────────────────────────────────────────

def test_decision_audit_markdown_report(tmp_path):
    a = _audit(tmp_path,
               committee=[_committee_entry(human="REVIEW_CONFIRMED")],
               candidate=[_candidate_entry("GRID")],
               slack=[_slack_entry(action="candidate_wait", human="WAIT", symbol="GRID")])
    md = build_audit_markdown(a)
    for sec in ("今月の結論", "月次レビュー判断", "Candidate Review判断", "AI委員会 vs 人間判断",
                "判断が割れた項目", "Stabilityが不安定な候補", "人間メモ一覧", "次回確認事項", "安全注記"):
        assert sec in md
    assert "GRID" in md


def test_decision_audit_cli_generates_report(tmp_path, monkeypatch):
    # build via the same path the CLI uses, with explicit output path
    a = _audit(tmp_path, candidate=[_candidate_entry("GRID")])
    out = tmp_path / "decision_audit_202606.md"
    path = save_audit_report(build_audit_markdown(a), "2026-06", output_path=out)
    assert Path(path).exists()
    assert "Integrated Decision Audit" in Path(path).read_text(encoding="utf-8")


# ── safety ───────────────────────────────────────────────────────────────────

def test_decision_audit_no_secret_in_report(tmp_path):
    bad = _candidate_entry("GRID")
    bad["api_key"] = "sk-secret"
    bad["raw_response"] = "LEAK"
    a = _audit(tmp_path, candidate=[bad])
    md = build_audit_markdown(a)
    for s in ("sk-secret", "api_key", "raw_response", "LEAK"):
        assert s not in md


def _safety_md(tmp_path):
    a = _audit(tmp_path,
               committee=[_committee_entry(human="REVIEW_CONFIRMED")],
               candidate=[_candidate_entry("GRID")])
    return build_audit_markdown(a)


def test_decision_audit_does_not_change_allocation(tmp_path):
    md = _safety_md(tmp_path)
    for k in ("final_allocation", "weights", "allocation_override"):
        assert k not in md


def test_decision_audit_does_not_calculate_order_quantity(tmp_path):
    md = _safety_md(tmp_path)
    for k in ("quantity", "shares", "order_amount", "order_quantity", "intended_amount_jpy"):
        assert k not in md


def test_decision_audit_does_not_trigger_auto_trade(tmp_path):
    md = _safety_md(tmp_path)
    for k in ("auto_trade", "place_order", "execute_trade", "brokerage", "買え", "売れ"):
        assert k not in md
