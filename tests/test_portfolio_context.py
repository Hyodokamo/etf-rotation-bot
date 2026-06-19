"""Tests for Phase 5.0.5: Core Portfolio Read-Only Context / AI Sleeve Separation.

Hermetic: snapshot CSV and policy YAML are written to tmp_path, so these never
touch the user's real (gitignored) personal portfolio data.
"""
import json

import pytest

from src.committee.candidate_review import Candidate, CandidateAction, build_candidate_context
from src.committee.models import CommitteeMemberConfig
from src.committee.prompts import build_batch_system_prompt, build_batch_user_prompt
from src.decision_audit import build_audit_markdown, build_decision_audit
from src.portfolio_context import (
    CORE_READ_ONLY_PREMISE,
    AiSleeveState,
    PortfolioContext,
    build_portfolio_context,
    detect_core_overlap,
    load_portfolio_context,
    load_portfolio_context_policy,
    load_portfolio_snapshot,
    to_committee_context,
)

SNAPSHOT_HEADER = (
    "as_of_date,scope,symbol,name,asset_type,theme,"
    "market_value_jpy,account,management_policy,notes"
)

_SAMPLE_ROWS = [
    "2026-06-05,core_manual,SP500,S&P500投信,mutual_fund,us_large_core,0,NISA,read_only,長期コア。Botは売却提案しない",
    "2026-06-05,core_manual,FANG,FANG+投信,mutual_fund,mega_cap_growth,0,NISA,read_only,長期コア。高ボラ前提",
    "2026-06-05,ai_sleeve_cash,CASH,AI検証枠現金,cash,cash,1000000,taxable,managed_by_bot,100万円AI検証枠。現在未投入",
]

_POLICY_YAML = """
total_portfolio_policy:
  enabled: true
  do_not_optimize_total_portfolio: true
  core_assets_are_read_only: true
  purpose: "全資産はリスク把握のために参照する。売却・是正提案の主対象にしない。"
core_manual_policy:
  accepted_concentration: [sp500, fang_plus, nasdaq100, us_large_growth]
  review_frequency: quarterly
  review_purpose: [concentration_risk, drawdown_tolerance, thesis_break_conditions, overlap_with_ai_sleeve]
ai_sleeve_policy:
  enabled: true
  total_budget_jpy: 1000000
  current_cash_jpy: 1000000
  current_invested_jpy: 0
  max_initial_deployment_jpy: 250000
  max_monthly_deployment_jpy: 250000
  default_account: taxable
  nisa_use_policy: "NISAは長期確信がある場合のみ。"
  purpose: "既存コア資産と重複しすぎない形で、攻めの候補を検証する。"
overlap_policy:
  warn_if_ai_sleeve_adds_same_risk_as_core: true
  high_overlap_themes: [nasdaq100, fang_plus, mega_cap_growth, ai_software, semiconductor]
  preferred_diversifying_themes: [smart_grid, infrastructure, energy, uranium, quality, value, managed_futures, cash_buffer]
"""


def _write_snapshot(tmp_path, rows=_SAMPLE_ROWS):
    p = tmp_path / "snapshot.csv"
    p.write_text(SNAPSHOT_HEADER + "\n" + "\n".join(rows) + "\n", encoding="utf-8")
    return p


def _write_policy(tmp_path):
    p = tmp_path / "policy.yaml"
    p.write_text(_POLICY_YAML, encoding="utf-8")
    return p


def _context(tmp_path, rows=_SAMPLE_ROWS) -> PortfolioContext:
    # Explicit non-existent sleeve-state path keeps this hermetic (no real CSV).
    return load_portfolio_context(_write_snapshot(tmp_path, rows), _write_policy(tmp_path),
                                  ai_sleeve_state_path=tmp_path / "no_state.csv")


def _candidate(symbol="GRID", theme="smart_grid", asset_type="theme_etf"):
    return Candidate(symbol=symbol, name=symbol, asset_type=asset_type, theme=theme,
                     candidate_action=CandidateAction.NEW_BUY)


# ── loading ──────────────────────────────────────────────────────────────────


def test_portfolio_context_loads_snapshot_csv(tmp_path):
    holdings = load_portfolio_snapshot(_write_snapshot(tmp_path))
    assert len(holdings) == 3
    assert {h.symbol for h in holdings} == {"SP500", "FANG", "CASH"}


def test_portfolio_context_loads_policy_yaml(tmp_path):
    policy = load_portfolio_context_policy(_write_policy(tmp_path))
    assert policy.total_portfolio_policy.do_not_optimize_total_portfolio is True
    assert policy.ai_sleeve_policy.total_budget_jpy == 1_000_000
    assert "mega_cap_growth" in policy.overlap_policy.high_overlap_themes


def test_portfolio_context_separates_core_manual_and_ai_sleeve(tmp_path):
    ctx = _context(tmp_path)
    assert {h.symbol for h in ctx.core_manual} == {"SP500", "FANG"}
    assert {h.symbol for h in ctx.ai_sleeve_cash} == {"CASH"}
    assert ctx.ai_sleeve_invested == []


def test_portfolio_context_core_manual_is_read_only(tmp_path):
    ctx = _context(tmp_path)
    assert ctx.core_is_read_only is True
    assert all(h.is_read_only and not h.is_ai_sleeve for h in ctx.core_manual)
    cc = to_committee_context(ctx)
    assert cc["core_manual"]["read_only"] is True
    assert cc["core_manual"]["is_sell_or_rebalance_target"] is False


def test_portfolio_context_ai_sleeve_budget_calculation(tmp_path):
    ctx = _context(tmp_path)
    s = ctx.ai_sleeve
    assert isinstance(s, AiSleeveState)
    assert s.total_budget_jpy == 1_000_000
    assert s.current_cash_jpy == 1_000_000
    assert s.current_invested_jpy == 0
    assert s.cash_ratio == 1.0
    assert s.invested_ratio == 0.0
    assert s.max_initial_deployment_jpy == 250_000


def test_portfolio_context_cash_starts_at_1000000(tmp_path):
    ctx = _context(tmp_path)
    assert ctx.ai_sleeve.current_cash_jpy == 1_000_000


def test_portfolio_context_does_not_optimize_total_portfolio(tmp_path):
    ctx = _context(tmp_path)
    assert ctx.policy.total_portfolio_policy.do_not_optimize_total_portfolio is True
    assert ctx.optimizes_total_portfolio is False
    # No optimization/rebalance API exists on the context.
    assert not hasattr(ctx, "optimize")
    assert not hasattr(ctx, "rebalance")


def test_portfolio_context_detects_growth_overlap(tmp_path):
    ctx = _context(tmp_path)
    # A NASDAQ100/mega-cap growth candidate overlaps the user's core concentration.
    warning = detect_core_overlap("nasdaq100", ctx, "QQQM")
    assert warning is not None
    assert warning.overlaps_with  # non-empty
    assert warning.diversifying is False
    # A diversifying theme should NOT be flagged as an overlap.
    div = detect_core_overlap("uranium", ctx, "URA")
    assert div is None or div.overlaps_with == []


def test_portfolio_context_candidate_review_receives_context(tmp_path):
    ctx = _context(tmp_path)
    cand = _candidate(symbol="QQQM", theme="mega_cap_growth", asset_type="theme_etf")
    context = build_candidate_context(cand, portfolio_context=ctx)
    assert "core_portfolio_context" in context
    assert context["core_portfolio_context"]["ai_sleeve"]["total_budget_jpy"] == 1_000_000
    # Growth candidate overlapping the core is surfaced.
    assert "core_overlap_warning" in context
    assert context["core_overlap_warning"]["overlaps_with"]


def test_portfolio_context_committee_prompt_includes_read_only_instruction(tmp_path):
    members = [CommitteeMemberConfig(member_id="aqr_meb", display_name="AQR", focus="quant")]
    system = build_batch_system_prompt(members)
    assert CORE_READ_ONLY_PREMISE in system
    # And via the candidate context path too.
    ctx = _context(tmp_path)
    context = build_candidate_context(_candidate(), portfolio_context=ctx)
    user = build_batch_user_prompt(context, members)
    assert "read_only" in user or "AI検証枠" in user


def test_portfolio_context_slack_digest_mentions_ai_sleeve_scope(tmp_path):
    from src.committee.slack_digest import build_executive_digest
    ctx = _context(tmp_path)
    digest = build_executive_digest(
        weights={"VOO": 0.6, "BND": 0.4}, risk_off=False, turnover=None,
        report_path="reports/x.md", portfolio_context=ctx,
    )
    assert "AI検証枠" in digest
    assert "read-only" in digest
    assert "売却・是正提案ではない" in digest


def test_portfolio_context_audit_summary_mentions_read_only_core(tmp_path):
    audit = build_decision_audit(
        "2099-01", committee_log_path=tmp_path / "c.jsonl",
        candidate_log_path=tmp_path / "cand.jsonl", slack_log_path=tmp_path / "s.jsonl",
    )
    md = build_audit_markdown(audit)
    assert "core_manual" in md
    assert "read-only" in md
    assert "ai_sleeve" in md


# ── safety invariants ──────────────────────────────────────────────────────────


def test_portfolio_context_does_not_change_final_allocation(tmp_path):
    ctx = _context(tmp_path)
    cc = to_committee_context(ctx)
    blob = json.dumps(cc, ensure_ascii=False)
    for k in ("final_allocation", "weights", "allocation_override"):
        assert k not in blob
    assert not hasattr(ctx, "final_allocation")
    assert not hasattr(ctx.ai_sleeve, "weights")


def test_portfolio_context_does_not_calculate_order_quantity(tmp_path):
    ctx = _context(tmp_path)
    # The sleeve state is budget-only: no quantity/shares fields.
    state_fields = set(AiSleeveState.model_fields)
    for k in ("quantity", "shares", "order_amount", "order_quantity", "num_shares"):
        assert k not in state_fields
    blob = json.dumps(to_committee_context(ctx), ensure_ascii=False)
    for k in ("quantity", "shares", "order_amount", "order_quantity"):
        assert k not in blob


def test_portfolio_context_does_not_trigger_auto_trade(tmp_path):
    ctx = _context(tmp_path)
    blob = json.dumps(to_committee_context(ctx), ensure_ascii=False)
    for k in ("auto_trade", "place_order", "execute_trade", "brokerage", "買え", "売れ"):
        assert k not in blob
    # No execution-style methods on the context.
    for attr in ("place_order", "execute", "trade", "buy", "sell"):
        assert not hasattr(ctx, attr)


def test_portfolio_context_handles_missing_snapshot_gracefully(tmp_path):
    missing = tmp_path / "nope.csv"
    holdings = load_portfolio_snapshot(missing)
    assert holdings == []
    # Building a context with no snapshot falls back to policy budget.
    ctx = build_portfolio_context([], load_portfolio_context_policy(_write_policy(tmp_path)))
    assert ctx.core_manual == []
    assert ctx.ai_sleeve.current_cash_jpy == 1_000_000
    assert ctx.core_is_read_only is False  # no core rows -> vacuously not "all read-only"


def test_portfolio_context_handles_zero_market_value_rows(tmp_path):
    # SP500 / FANG have market_value 0 (placeholder); must load without error.
    ctx = _context(tmp_path)
    core = {h.symbol: h for h in ctx.core_manual}
    assert core["SP500"].market_value_jpy == 0.0
    assert core["FANG"].market_value_jpy == 0.0
    assert ctx.core_total_jpy == 0.0
    # Empty / malformed market value also coerces to 0 rather than raising.
    rows = _SAMPLE_ROWS + [
        "2026-06-05,unknown,BLANK,空値,etf,unknown,,taxable,reference_only,空のmarket_value",
    ]
    ctx2 = _context(tmp_path, rows)
    blank = next(h for h in ctx2.holdings if h.symbol == "BLANK")
    assert blank.market_value_jpy == 0.0


def test_portfolio_context_no_secret_in_reports(tmp_path):
    # A snapshot row carrying secret-looking extra data must never reach reports.
    rows = _SAMPLE_ROWS + [
        "2026-06-05,core_manual,LEAKY,sk-secret-token,etf,us_large_core,5,NISA,read_only,api_key=sk-leak",
    ]
    ctx = _context(tmp_path, rows)
    cc_blob = json.dumps(to_committee_context(ctx), ensure_ascii=False)
    from src.portfolio_context import build_audit_context_notes, build_slack_context_line
    slack_blob = "\n".join(build_slack_context_line(ctx))
    audit_blob = "\n".join(build_audit_context_notes(ctx))
    for blob in (cc_blob, slack_blob, audit_blob):
        for s in ("sk-secret-token", "api_key", "sk-leak"):
            assert s not in blob
