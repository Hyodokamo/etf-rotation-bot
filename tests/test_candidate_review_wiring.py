"""Tests for Phase 5.0 Wiring + Portfolio Context Integration.

Verifies the --candidate-review path loads the ETF master + read-only portfolio
context and threads them through review/formatters. Hermetic: all CSV/YAML inputs
are written to tmp_path; the committee runs with client=None (deterministic
INSUFFICIENT_DATA fallback) so enrichment is tested independently of any LLM.
"""
import json

from main import load_candidate_enrichment
from src.committee.candidate_review import (
    Candidate,
    CandidateAction,
    build_candidate_markdown,
    build_candidate_slack_summary,
    review_candidate,
    review_watchlist,
)
from src.committee.runner import load_committee_config
from src.etf_master import load_etf_master
from src.portfolio_context import load_portfolio_context

# ── ETF master fixture (full 44-col schema, 2 rows) ────────────────────────────

_MASTER_HEADER = (
    "symbol,name,issuer,asset_class,category,sector,theme,role_in_ai_sleeve,"
    "offense_defense,primary_use,universe_status,include_in_active_universe,"
    "agent_affinity,agent_concern,expense_ratio,expense_ratio_source,"
    "expense_ratio_as_of_date,liquidity_tier,aum_or_liquidity_note,trend_candidate,"
    "value_candidate,quality_candidate,macro_theme_candidate,defensive_candidate,"
    "cash_buffer_candidate,nisa_eligible_status,nisa_usage_policy,preferred_account,"
    "rakuten_available,sbi_available,monex_available,nomura_available,"
    "broker_availability_note,needs_order_screen_check,expected_overlap_with_core,"
    "core_overlap_reason,main_risks,required_checks,entry_checks,"
    "avoid_entry_conditions,invalidation_conditions,data_quality_status,source_urls,notes"
)
# ITA: active_core, needs_order_screen=true, data_quality=needs_broker_check
_MASTER_ITA = (
    "ITA,iShares US Aerospace & Defense,iShares,equity,Aerospace,industrials,defense,"
    "防衛・航空宇宙の高流動性本命,offense,structural_theme,active_core,true,"
    "druckenmiller;buffett;paul_tudor_jones,howard_marks,0.38%,issuer,2026-03-31,high,note,"
    "true,false,false,true,false,false,unknown,avoid_rotation,taxable,"
    "unknown,unknown,unknown,unknown,broker note,true,low,reason,risks,checks,entry,"
    "avoid,invalidate,needs_broker_check,https://example.com/ita,notes"
)
# GRID: research (not excluded), data_quality=needs_broker_check
_MASTER_GRID = (
    "GRID,First Trust Smart Grid,First Trust,equity,Power,utilities,smart_grid,"
    "電力網テーマ,offense,theme_complement,research,false,"
    "druckenmiller;core_ai_auditor,howard_marks,0.56%,issuer,2026-02-02,high,note,"
    "true,false,false,true,false,false,unknown,avoid_rotation,taxable,"
    "unknown,unknown,unknown,unknown,broker note,true,low,reason,risks,checks,entry,"
    "avoid,invalidate,needs_broker_check,https://example.com/grid,notes"
)


def _write_master(tmp_path):
    p = tmp_path / "etf_master.csv"
    p.write_text(_MASTER_HEADER + "\n" + _MASTER_ITA + "\n" + _MASTER_GRID + "\n", encoding="utf-8")
    return p


# ── portfolio snapshot / policy / sleeve-state fixtures ────────────────────────

_SNAP_HEADER = (
    "as_of_date,scope,symbol,name,asset_type,theme,"
    "market_value_jpy,account,management_policy,notes"
)
_SNAP_ROWS = [
    "2026-06-05,core_manual,SP500,S&P500投信,mutual_fund,us_large_core,0,NISA,read_only,長期コア",
    "2026-06-05,core_manual,FANG,FANG+投信,mutual_fund,mega_cap_growth,0,NISA,read_only,長期コア",
    "2026-06-05,ai_sleeve_cash,CASH,AI検証枠現金,cash,cash,1000000,taxable,managed_by_bot,未投入",
]
_POLICY_YAML = """
total_portfolio_policy:
  do_not_optimize_total_portfolio: true
  core_assets_are_read_only: true
core_manual_policy:
  accepted_concentration: [sp500, fang_plus, nasdaq100, us_large_growth]
ai_sleeve_policy:
  total_budget_jpy: 1000000
  current_cash_jpy: 1000000
  current_invested_jpy: 0
  max_initial_deployment_jpy: 250000
  max_monthly_deployment_jpy: 250000
overlap_policy:
  warn_if_ai_sleeve_adds_same_risk_as_core: true
  high_overlap_themes: [nasdaq100, fang_plus, mega_cap_growth, ai_software, semiconductor]
  preferred_diversifying_themes: [smart_grid, infrastructure, energy, uranium, quality, value]
"""


def _write_snapshot(tmp_path):
    p = tmp_path / "snapshot.csv"
    p.write_text(_SNAP_HEADER + "\n" + "\n".join(_SNAP_ROWS) + "\n", encoding="utf-8")
    return p


def _write_policy(tmp_path):
    p = tmp_path / "policy.yaml"
    p.write_text(_POLICY_YAML, encoding="utf-8")
    return p


def _candidate(symbol="ITA", theme="defense", asset_type="theme_etf"):
    return Candidate(symbol=symbol, name=symbol, asset_type=asset_type, theme=theme,
                     candidate_action=CandidateAction.NEW_BUY)


def _ctx(tmp_path):
    return load_portfolio_context(_write_snapshot(tmp_path), _write_policy(tmp_path),
                                  ai_sleeve_state_path=tmp_path / "no_state.csv")


def _master(tmp_path):
    return load_etf_master(_write_master(tmp_path))


def _review(tmp_path, candidate=None, with_master=True, with_ctx=True):
    cfg = load_committee_config()
    return review_candidate(
        candidate or _candidate(), cfg, None,
        etf_master=_master(tmp_path) if with_master else None,
        portfolio_context=_ctx(tmp_path) if with_ctx else None,
    )


# ── main-path loading ──────────────────────────────────────────────────────────


def test_candidate_review_loads_etf_master_in_main_path(tmp_path):
    master, _ = load_candidate_enrichment(
        master_path=str(_write_master(tmp_path)),
        snapshot_path=str(tmp_path / "missing.csv"),
    )
    assert master is not None
    assert {"ITA", "GRID"} <= set(master)


def test_candidate_review_works_without_etf_master(tmp_path):
    master, ctx = load_candidate_enrichment(
        master_path=str(tmp_path / "nope.csv"),
        snapshot_path=str(tmp_path / "missing.csv"),
    )
    assert master is None and ctx is None
    # Review still works with no enrichment at all.
    cfg = load_committee_config()
    r = review_candidate(_candidate(), cfg, None, etf_master=None, portfolio_context=None)
    assert r.universe_status == "" and r.etf_master_known is True


def test_candidate_review_loads_portfolio_context(tmp_path):
    _, ctx = load_candidate_enrichment(
        master_path=str(tmp_path / "nope.csv"),
        snapshot_path=str(_write_snapshot(tmp_path)),
        policy_path=str(_write_policy(tmp_path)),
        sleeve_state_path=str(tmp_path / "no_state.csv"),
    )
    assert ctx is not None
    assert ctx.ai_sleeve.current_cash_jpy == 1_000_000
    assert ctx.ai_sleeve.current_invested_jpy == 0


# ── enrichment on the result ───────────────────────────────────────────────────


def test_candidate_review_enriches_with_etf_metadata(tmp_path):
    r = _review(tmp_path)
    assert r.universe_status == "active_core"
    assert r.role_in_ai_sleeve
    assert "paul_tudor_jones" in r.agent_affinity
    assert r.agent_concern == ["howard_marks"]
    assert r.preferred_account == "taxable"


def test_candidate_review_unknown_symbol_does_not_crash(tmp_path):
    r = _review(tmp_path, candidate=_candidate("UNKNOWN_TEST", theme="unknown_theme"))
    assert r.etf_master_known is False
    assert r.universe_status == "unknown"
    assert r.candidate["symbol"] == "UNKNOWN_TEST"


def test_candidate_review_core_manual_is_read_only(tmp_path):
    ctx = _ctx(tmp_path)
    assert ctx.core_is_read_only is True
    md = build_candidate_markdown([_review(tmp_path)], portfolio_context=ctx)
    assert "read-only" in md
    assert "売却・是正提案の主対象にしません" in md


def test_candidate_review_detects_core_overlap_from_context(tmp_path):
    # mega_cap_growth candidate overlaps the user's core (FANG = mega_cap_growth).
    r = _review(tmp_path, candidate=_candidate("QQQM", theme="mega_cap_growth"))
    assert any("コア資産との重複" in x for x in r.key_risks)


def test_candidate_review_does_not_suggest_selling_core_manual(tmp_path):
    md = build_candidate_markdown([_review(tmp_path)], portfolio_context=_ctx(tmp_path))
    # read-only stance present; no instruction to sell the core.
    assert "売却・是正提案の主対象にしません" in md
    for bad in ("コア資産を売却", "コアを売却", "売れ"):
        assert bad not in md


# ── markdown rendering ─────────────────────────────────────────────────────────


def test_candidate_review_shows_universe_status(tmp_path):
    md = build_candidate_markdown([_review(tmp_path)])
    assert "active_core" in md


def test_candidate_review_shows_order_screen_check_warning(tmp_path):
    md = build_candidate_markdown([_review(tmp_path)])
    assert "発注前確認要" in md
    assert "注文画面" in md


def test_candidate_review_shows_data_quality_warning(tmp_path):
    md = build_candidate_markdown([_review(tmp_path)])
    assert "データ品質警告" in md
    assert "needs_broker_check" in md


def test_candidate_review_shows_ai_sleeve_scope(tmp_path):
    md = build_candidate_markdown([_review(tmp_path)], portfolio_context=_ctx(tmp_path))
    assert "AI検証枠" in md
    assert "1,000,000" in md
    assert "初回投入上限" in md
    assert "250,000" in md


def test_markdown_candidate_review_includes_etf_master_metadata(tmp_path):
    md = build_candidate_markdown([_review(tmp_path)])
    for token in ("AI検証枠での役割", "既存コアとの重複想定", "druckenmiller",
                  "howard_marks", "avoid_rotation", "taxable"):
        assert token in md


# ── slack rendering ────────────────────────────────────────────────────────────


def test_slack_candidate_review_includes_etf_master_metadata(tmp_path):
    slack = build_candidate_slack_summary(_review(tmp_path), portfolio_context=_ctx(tmp_path))
    assert "active_core" in slack
    assert "発注前に証券会社注文画面で確認" in slack
    assert "data_quality_status=needs_broker_check" in slack
    assert "AI検証枠" in slack  # from portfolio_context scope lines


# ── safety invariants ──────────────────────────────────────────────────────────


def test_candidate_review_does_not_change_final_allocation(tmp_path):
    r = _review(tmp_path)
    assert r.allocation_override is False
    md = build_candidate_markdown([r], portfolio_context=_ctx(tmp_path))
    for k in ("final_allocation", "weights", "allocation_override: `true`"):
        assert k not in md
    # result serializes without any weight mutation field set true
    blob = json.dumps(r.to_dict(), ensure_ascii=False)
    assert '"allocation_override": false' in blob


def test_candidate_review_does_not_calculate_order_quantity(tmp_path):
    md = build_candidate_markdown([_review(tmp_path)], portfolio_context=_ctx(tmp_path))
    slack = build_candidate_slack_summary(_review(tmp_path), portfolio_context=_ctx(tmp_path))
    for k in ("quantity", "shares", "order_amount", "order_quantity", "株数を計算"):
        assert k not in md
        assert k not in slack


def test_candidate_review_does_not_trigger_auto_trade(tmp_path):
    md = build_candidate_markdown([_review(tmp_path)], portfolio_context=_ctx(tmp_path))
    slack = build_candidate_slack_summary(_review(tmp_path), portfolio_context=_ctx(tmp_path))
    for k in ("auto_trade", "place_order", "execute_trade", "brokerage", "買え", "売れ"):
        assert k not in md
        assert k not in slack
