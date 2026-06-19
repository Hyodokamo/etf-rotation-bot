"""Tests for Phase 5.0.6: AI Sleeve State Persistence / Existing Holdings Separation.

ai_sleeve_state.csv is the source of truth for the ¥1M sleeve. The snapshot's
existing ai_sleeve_invested rows (BOTZ/GRID) are read-only related holdings and
are NEVER auto-summed into the sleeve invested amount. Hermetic: all inputs are
written to tmp_path.
"""
import json

from src.committee.candidate_review import (
    Candidate,
    CandidateAction,
    build_candidate_markdown,
    review_candidate,
)
from src.committee.runner import load_committee_config
from src.decision_audit import build_audit_markdown, build_decision_audit
from src.portfolio_context import (
    build_portfolio_context,
    load_ai_sleeve_state,
    load_portfolio_context,
    load_portfolio_context_policy,
    to_committee_context,
)

_SNAP_HEADER = (
    "as_of_date,scope,symbol,name,asset_type,theme,"
    "market_value_jpy,account,management_policy,notes"
)
# Existing BOTZ/GRID held in NISA (ai_sleeve_invested), would sum to 369,451.
_SNAP_ROWS = [
    "2026-06-05,core_manual,FANG,FANG+投信,mutual_fund,mega_cap_growth,0,NISA,read_only,長期コア",
    "2026-06-05,ai_sleeve_invested,BOTZ,Global X Robotics,theme_etf,robotics_ai,185832,NISA,reference_only,既存NISA保有",
    "2026-06-05,ai_sleeve_invested,GRID,First Trust Smart Grid,theme_etf,smart_grid,183619,NISA,reference_only,既存NISA保有",
]
_POLICY_YAML = """
ai_sleeve_policy:
  total_budget_jpy: 1000000
  current_cash_jpy: 1000000
  current_invested_jpy: 0
  max_initial_deployment_jpy: 250000
overlap_policy:
  warn_if_ai_sleeve_adds_same_risk_as_core: true
  high_overlap_themes: [mega_cap_growth, nasdaq100]
"""
# Recommended schema (with the extra sleeve_name column the real file uses).
_STATE_CSV = (
    "as_of_date,sleeve_name,total_budget_jpy,cash_jpy,invested_jpy,default_account,notes\n"
    "2026-06-05,ai_sleeve,1000000,1000000,0,taxable,既存BOTZ/GRIDとは分離して0円開始\n"
)


def _write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def _snapshot(tmp_path):
    return _write(tmp_path, "snapshot.csv", _SNAP_HEADER + "\n" + "\n".join(_SNAP_ROWS) + "\n")


def _policy(tmp_path):
    return _write(tmp_path, "policy.yaml", _POLICY_YAML)


def _state(tmp_path):
    return _write(tmp_path, "ai_sleeve_state.csv", _STATE_CSV)


def _ctx_with_state(tmp_path):
    return load_portfolio_context(_snapshot(tmp_path), _policy(tmp_path), _state(tmp_path))


def _ctx_no_state(tmp_path):
    return load_portfolio_context(_snapshot(tmp_path), _policy(tmp_path),
                                  ai_sleeve_state_path=tmp_path / "missing.csv")


def _candidate(symbol="ITA", theme="defense"):
    return Candidate(symbol=symbol, name=symbol, asset_type="theme_etf", theme=theme,
                     candidate_action=CandidateAction.NEW_BUY)


def _review(ctx):
    return review_candidate(_candidate(), load_committee_config(), None, portfolio_context=ctx)


# ── state loading ──────────────────────────────────────────────────────────────


def test_ai_sleeve_state_loads_csv(tmp_path):
    state = load_ai_sleeve_state(_state(tmp_path))
    assert state["total_budget_jpy"] == 1_000_000
    assert state["current_cash_jpy"] == 1_000_000
    assert state["current_invested_jpy"] == 0
    assert state["default_account"] == "taxable"


def test_ai_sleeve_state_missing_file_fallback(tmp_path):
    assert load_ai_sleeve_state(tmp_path / "nope.csv") is None
    # Falls back to policy defaults (no crash).
    ctx = _ctx_no_state(tmp_path)
    assert ctx.ai_sleeve.current_cash_jpy == 1_000_000
    assert ctx.ai_sleeve.current_invested_jpy == 0


# ── precedence: state.csv over snapshot ────────────────────────────────────────


def test_ai_sleeve_state_prefers_state_csv_over_snapshot(tmp_path):
    ctx = _ctx_with_state(tmp_path)
    # snapshot BOTZ/GRID would sum to 369,451 — state.csv wins with 0.
    assert ctx.ai_sleeve.current_invested_jpy == 0
    assert ctx.ai_sleeve.current_cash_jpy == 1_000_000


def test_ai_sleeve_state_starts_from_zero_when_configured(tmp_path):
    ctx = _ctx_with_state(tmp_path)
    assert ctx.ai_sleeve.current_invested_jpy == 0
    assert ctx.ai_sleeve.cash_ratio == 1.0
    assert ctx.ai_sleeve.invested_ratio == 0.0


def test_ai_sleeve_state_does_not_auto_include_existing_botz_grid(tmp_path):
    # Even WITHOUT a state file, existing ai_sleeve_invested rows are NOT summed.
    ctx = _ctx_no_state(tmp_path)
    assert ctx.ai_sleeve.current_invested_jpy == 0
    # but the existing holdings are still tracked separately.
    assert {h.symbol for h in ctx.existing_related_holdings} == {"BOTZ", "GRID"}


def test_ai_sleeve_state_tracks_existing_related_holdings_separately(tmp_path):
    ctx = _ctx_with_state(tmp_path)
    syms = {h.symbol for h in ctx.existing_related_holdings}
    assert syms == {"BOTZ", "GRID"}
    block = to_committee_context(ctx)["existing_related_holdings"]
    assert block["read_only"] is True
    assert block["is_sell_or_rebalance_target"] is False
    assert block["not_summed_into_ai_sleeve"] is True
    assert set(block["symbols"]) == {"BOTZ", "GRID"}


# ── candidate review display ───────────────────────────────────────────────────


def test_candidate_review_shows_ai_sleeve_cash_1000000_invested_0(tmp_path):
    md = build_candidate_markdown([_review(_ctx_with_state(tmp_path))],
                                  portfolio_context=_ctx_with_state(tmp_path))
    assert "現金 1,000,000円" in md
    assert "投資済み 0円" in md


def test_candidate_review_mentions_existing_related_holdings_read_only(tmp_path):
    md = build_candidate_markdown([_review(_ctx_with_state(tmp_path))],
                                  portfolio_context=_ctx_with_state(tmp_path))
    assert "既存関連保有" in md
    assert "read-only" in md
    assert "BOTZ" in md and "GRID" in md


def test_candidate_review_does_not_suggest_selling_existing_holdings(tmp_path):
    md = build_candidate_markdown([_review(_ctx_with_state(tmp_path))],
                                  portfolio_context=_ctx_with_state(tmp_path))
    assert "売却・是正提案の主対象にしません" in md
    for bad in ("BOTZを売却", "GRIDを売却", "売れ", "既存保有を売却"):
        assert bad not in md


# ── audit summary uses state, not snapshot ─────────────────────────────────────


def test_audit_summary_uses_ai_sleeve_state_not_snapshot_invested(tmp_path):
    ctx = _ctx_with_state(tmp_path)
    audit = build_decision_audit(
        "2099-01", committee_log_path=tmp_path / "c.jsonl",
        candidate_log_path=tmp_path / "cd.jsonl", slack_log_path=tmp_path / "s.jsonl",
    )
    md = build_audit_markdown(audit, portfolio_context=ctx)
    assert "投資済み0円" in md       # from ai_sleeve_state.csv
    assert "369,451" not in md      # snapshot BOTZ/GRID NOT summed
    assert "ai_sleeve_state.csv" in md


# ── safety invariants ──────────────────────────────────────────────────────────


def test_ai_sleeve_state_does_not_change_final_allocation(tmp_path):
    ctx = _ctx_with_state(tmp_path)
    blob = json.dumps(to_committee_context(ctx), ensure_ascii=False)
    for k in ("final_allocation", "weights", "allocation_override"):
        assert k not in blob


def test_ai_sleeve_state_does_not_calculate_order_quantity(tmp_path):
    state = load_ai_sleeve_state(_state(tmp_path))
    for k in ("quantity", "shares", "order_amount", "order_quantity", "num_shares"):
        assert k not in state
    blob = json.dumps(to_committee_context(_ctx_with_state(tmp_path)), ensure_ascii=False)
    for k in ("quantity", "shares", "order_amount", "order_quantity"):
        assert k not in blob


def test_ai_sleeve_state_does_not_trigger_auto_trade(tmp_path):
    ctx = _ctx_with_state(tmp_path)
    md = build_candidate_markdown([_review(ctx)], portfolio_context=ctx)
    for k in ("auto_trade", "place_order", "execute_trade", "brokerage", "買え", "売れ"):
        assert k not in md
