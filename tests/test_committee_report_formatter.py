"""Tests for Phase 3.1: committee report/Slack formatting (display-only)."""
from src.committee.models import (
    CommitteeResult,
    CommitteeTier,
    CommitteeVerdict,
    MemberOutput,
)
from src.committee.report_formatter import (
    build_committee_markdown,
    build_committee_slack_summary,
)

V = CommitteeVerdict


def _member(mid, tier, verdict=V.PASS):
    return MemberOutput(
        member_id=mid,
        display_name=mid.upper(),
        tier=tier,
        verdict=verdict,
        confidence=0.6,
        rationale="r",
        strongest_support=f"{mid}-support",
        strongest_objection=f"{mid}-objection",
        key_risks=["risk_a"],
        required_checks=["check_x"],
        action_implication="advisory",
    )


def _result(final=V.PASS_WITH_CAUTION):
    return CommitteeResult(
        core_committee_verdict=V.PASS_WITH_CAUTION,
        satellite_committee_verdict=V.PASS,
        final_committee_verdict=final,
        recommended_action="現状配分を維持しつつ監視（shadow: 配分への影響なし）。",
        allocation_override=False,
        summary="Core=PASS_WITH_CAUTION / Satellite=PASS → 最終=PASS_WITH_CAUTION。",
        next_review_triggers=["check_x"],
        members=[
            _member("aqr_faber", CommitteeTier.CORE, V.PASS_WITH_CAUTION),
            _member("buffett", CommitteeTier.SATELLITE, V.PASS),
        ],
        shadow_mode=True,
        llm_call_mode="batch",
    )


# ── markdown ──────────────────────────────────────────────────────────────────

def test_markdown_contains_all_three_verdicts():
    md = build_committee_markdown(_result())
    assert "PASS_WITH_CAUTION" in md   # final + core
    assert "Core Committee" in md
    assert "Satellite Committee" in md


def test_markdown_has_shadow_banner():
    md = build_committee_markdown(_result())
    assert "shadow mode" in md
    assert "最終配分に一切影響しません" in md


def test_markdown_shows_support_and_objection():
    md = build_committee_markdown(_result())
    assert "aqr_faber-support" in md
    assert "aqr_faber-objection" in md


def test_markdown_no_trade_or_order_language():
    md = build_committee_markdown(_result())
    assert "売買承認" not in md
    assert "注文" not in md
    # the only auto-trade mention allowed is the negation in the banner
    assert "自動売買は行いません" in md


def test_markdown_review_triggers_listed():
    md = build_committee_markdown(_result())
    assert "次回レビュー" in md
    assert "check_x" in md


def test_markdown_override_shown_false():
    md = build_committee_markdown(_result())
    assert "allocation_override: `false`" in md


# ── slack ─────────────────────────────────────────────────────────────────────

def test_slack_summary_concise_with_final_verdict():
    s = build_committee_slack_summary(_result(final=V.WATCH))
    assert "Investment Committee" in s
    assert "WATCH" in s
    assert "Core" in s and "Satellite" in s


def test_slack_summary_shadow_notice():
    s = build_committee_slack_summary(_result())
    assert "shadow" in s
    assert "売買承認" not in s


# ── build_slack_summary integration ──────────────────────────────────────────

def test_build_slack_summary_includes_committee():
    from src.slack_client import build_slack_summary
    msg = build_slack_summary(
        weights={"VOO": 0.6, "BND": 0.4},
        risk_off=False,
        turnover=0.1,
        report_path="outputs/report.md",
        committee_result=_result(final=V.WATCH),
    )
    assert "Investment Committee" in msg
    assert "WATCH" in msg


def test_build_report_includes_committee():
    import pandas as pd
    from datetime import date
    from src.report_builder import build_report
    from src.config_loader import load_config
    from src.risk_gate import evaluate_risk_gate

    cfg = load_config("config.yaml")
    # minimal price frame for two tickers
    idx = pd.date_range("2025-01-01", periods=300, freq="B")
    prices = pd.DataFrame({"VOO": range(1, 301), "BND": range(1, 301)}, index=idx).astype(float)
    indicators = pd.DataFrame(
        {"mom_1m": [0.1, 0.05], "mom_3m": [0.1, 0.05], "mom_6m": [0.1, 0.05],
         "mom_12m": [0.1, 0.05], "vol_20d": [0.1, 0.05]},
        index=["VOO", "BND"],
    )
    scores = pd.Series({"VOO": 1.0, "BND": 0.5})
    rg = evaluate_risk_gate(prices, cfg.risk)
    text = build_report(
        cfg=cfg, weights={"VOO": 0.6, "BND": 0.4}, scores=scores,
        indicators=indicators, prices=prices, risk_gate=rg,
        prev_weights=None, turnover=None, run_date=date(2026, 6, 5),
        committee_result=_result(final=V.WATCH),
    )
    assert "Investment Committee" in text
    assert "最終配分に一切影響しません" in text
