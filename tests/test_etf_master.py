"""Tests for Phase 5.0: ETF Master Loader / Candidate Enrichment.

Hermetic: a small ETF-master CSV is written to tmp_path. The committee runs with
client=None (deterministic INSUFFICIENT_DATA fallback), so enrichment is tested
independently of any LLM.
"""
import json

from src.committee.candidate_review import (
    Candidate,
    CandidateAction,
    build_candidate_context,
    build_candidate_markdown,
    build_candidate_slack_summary,
    review_candidate,
)
from src.committee.runner import load_committee_config
from src.etf_master import (
    EtfMasterEntry,
    build_enrichment,
    get_entry,
    load_etf_master,
    select_active_core,
    select_by_status,
    unknown_entry,
)

# A compact subset of the real schema (header + a few representative rows).
_HEADER = (
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

# ITA: active_core, needs_order_screen_check=true, data_quality=needs_broker_check
_ITA = (
    "ITA,iShares US Aerospace & Defense,iShares,equity,Aerospace,industrials,defense,"
    "防衛本命,offense,structural_theme,active_core,true,"
    "druckenmiller;buffett;paul_tudor_jones,howard_marks,0.38%,issuer,2026-03-31,high,note,"
    "true,false,false,true,false,false,unknown,avoid_rotation,taxable,"
    "unknown,unknown,unknown,unknown,broker note,true,low,reason,risks,checks,entry,"
    "avoid,invalidate,needs_broker_check,https://example.com/ita,notes"
)
# XLE: active_core, needs_order_screen_check=true, data_quality=sufficient
_XLE = (
    "XLE,Energy Select Sector SPDR,SPDR,equity,Energy,energy,oil_gas,"
    "energy,offense,sector_tilt,active_core,true,"
    "druckenmiller;rob_arnott,howard_marks,0.08%,issuer,2026-06-04,very_high,note,"
    "true,true,false,true,false,false,unknown,avoid_rotation,taxable,"
    "unknown,unknown,unknown,unknown,broker note,true,low,reason,risks,checks,entry,"
    "avoid,invalidate,sufficient,https://example.com/xle,notes"
)
# BOTZ: research (NOT excluded, limited role), data_quality=needs_broker_check
_BOTZ = (
    "BOTZ,Global X Robotics & AI,Global X,equity,AI,robotics_ai,robotics_automation,"
    "robo,offense,theme_idea,research,false,"
    "druckenmiller,core_ai_auditor;howard_marks,0.68%,issuer,2026-06-04,medium,note,"
    "true,false,false,true,false,false,unknown,avoid_rotation,taxable,"
    "unknown,unknown,unknown,unknown,broker note,true,moderate,reason,risks,checks,entry,"
    "avoid,invalidate,needs_broker_check,https://example.com/botz,notes"
)
# QQQ: support, agent_affinity uses display form "Paul Tudor Jones" -> normalized
_QQQ = (
    "QQQ,Invesco QQQ Trust,Invesco,equity,Benchmark,large_cap_growth,nasdaq100,"
    "bench,offense,benchmark,support,false,"
    "Paul Tudor Jones;druckenmiller,howard_marks;rob_arnott,0.18%,issuer,2026-03-31,very_high,note,"
    "true,false,false,true,false,false,confirmed,core_long_term_only,either,"
    "confirmed,confirmed,unknown,confirmed,broker note,true,very_high,reason,risks,checks,entry,"
    "avoid,invalidate,sufficient,https://example.com/qqq,notes"
)
# DRIV: low_priority, data_quality=insufficient
_DRIV = (
    "DRIV,Global X Autonomous & EV,Global X,equity,Excluded,automotive,autonomous_driving,"
    "watch,offense,watch_only,low_priority,false,"
    "druckenmiller,howard_marks;core_ai_auditor,unknown,needs fee check,2026-06-06,medium,note,"
    "true,false,false,true,false,false,unknown,avoid_rotation,taxable,"
    "unknown,unknown,unknown,unknown,broker note,true,moderate,reason,risks,checks,entry,"
    "avoid,invalidate,insufficient,https://example.com/driv,notes"
)
# AGG: fallback
_AGG = (
    "AGG,iShares Core US Aggregate Bond,iShares,fixed_income,Bonds,bonds,risk_off,"
    "buffer,defense,risk_off_buffer,fallback,false,"
    "howard_marks;core_ai_auditor,druckenmiller,unknown,needs fee check,2026-06-06,very_high,note,"
    "false,false,false,false,true,false,unknown,taxable_preferred,taxable,"
    "unknown,unknown,unknown,unknown,broker note,true,low,reason,risks,checks,entry,"
    "avoid,invalidate,needs_fee_check,https://example.com/agg,notes"
)

_ROWS = [_ITA, _XLE, _BOTZ, _QQQ, _DRIV, _AGG]


def _write_master(tmp_path, rows=_ROWS):
    p = tmp_path / "etf_master.csv"
    p.write_text(_HEADER + "\n" + "\n".join(rows) + "\n", encoding="utf-8")
    return p


def _master(tmp_path, rows=_ROWS):
    return load_etf_master(_write_master(tmp_path, rows))


def _candidate(symbol="ITA", theme="defense", asset_type="theme_etf"):
    return Candidate(symbol=symbol, name=symbol, asset_type=asset_type, theme=theme,
                     candidate_action=CandidateAction.NEW_BUY)


# ── loading ──────────────────────────────────────────────────────────────────


def test_etf_master_loads_and_keys_by_symbol(tmp_path):
    master = _master(tmp_path)
    assert set(master) == {"ITA", "XLE", "BOTZ", "QQQ", "DRIV", "AGG"}
    assert master["ITA"].name.startswith("iShares")


def test_etf_master_parses_bools_and_lists(tmp_path):
    ita = _master(tmp_path)["ITA"]
    assert ita.include_in_active_universe is True
    assert ita.needs_order_screen_check is True
    assert ita.trend_candidate is True and ita.value_candidate is False
    assert ita.agent_affinity == ["druckenmiller", "buffett", "paul_tudor_jones"]
    assert ita.agent_concern == ["howard_marks"]
    assert ita.source_urls == ["https://example.com/ita"]


def test_etf_master_normalizes_display_agent_names(tmp_path):
    # QQQ uses "Paul Tudor Jones" -> must normalize to the member_id form.
    qqq = _master(tmp_path)["QQQ"]
    assert "paul_tudor_jones" in qqq.agent_affinity


def test_etf_master_missing_file_is_graceful(tmp_path):
    assert load_etf_master(tmp_path / "nope.csv") == {}


# ── universe status / role / frequency ─────────────────────────────────────────


def test_etf_master_active_core_only_is_normal_target(tmp_path):
    master = _master(tmp_path)
    active = {e.symbol for e in select_active_core(master)}
    assert active == {"ITA", "XLE"}
    assert master["ITA"].is_normal_review_target is True
    # research/support/fallback/low_priority are NOT normal targets...
    for sym in ("BOTZ", "QQQ", "DRIV", "AGG"):
        assert master[sym].is_normal_review_target is False


def test_etf_master_non_active_core_kept_with_limited_role(tmp_path):
    master = _master(tmp_path)
    # ...but they are NOT dropped — they remain loaded with a role + frequency.
    assert master["BOTZ"].review_frequency == "quarterly"
    assert master["QQQ"].review_frequency == "reference_only"
    assert master["AGG"].review_frequency == "fallback_only"
    assert master["DRIV"].review_frequency == "watch_only"
    assert select_by_status(master, "research")[0].symbol == "BOTZ"


# ── unknown metadata ───────────────────────────────────────────────────────────


def test_etf_master_unknown_symbol_not_dropped(tmp_path):
    master = _master(tmp_path)
    entry = get_entry(master, "NEWETF")
    assert entry.symbol == "NEWETF"
    assert entry.is_known is False
    assert entry.universe_status == "unknown"
    assert entry.review_frequency == "ad_hoc"


def test_etf_master_unknown_entry_helper():
    e = unknown_entry("ZZZ")
    assert e.is_known is False and e.universe_status == "unknown"


# ── data quality warning ───────────────────────────────────────────────────────


def test_etf_master_data_quality_warning_flag(tmp_path):
    master = _master(tmp_path)
    assert master["ITA"].needs_data_quality_warning is True   # needs_broker_check
    assert master["DRIV"].needs_data_quality_warning is True  # insufficient
    assert master["AGG"].needs_data_quality_warning is True   # needs_fee_check
    assert master["XLE"].needs_data_quality_warning is False  # sufficient
    assert master["QQQ"].needs_data_quality_warning is False  # sufficient


# ── enrichment block ───────────────────────────────────────────────────────────


def test_etf_master_enrichment_has_hints_not_filter(tmp_path):
    enr = build_enrichment(_master(tmp_path)["ITA"])
    assert enr["agent_affinity_hint"] == ["druckenmiller", "buffett", "paul_tudor_jones"]
    assert enr["agent_concern_hint"] == ["howard_marks"]
    # The hint is explicitly NOT a restriction on who evaluates.
    assert "全メンバーが全ETFを独立に評価" in enr["hint_note"]
    assert enr["needs_order_screen_check"] is True
    assert enr["data_quality_needs_warning"] is True


# ── candidate-context integration ──────────────────────────────────────────────


def test_etf_master_candidate_context_receives_metadata(tmp_path):
    master = _master(tmp_path)
    ctx = build_candidate_context(_candidate("ITA"), etf_master=master)
    assert "etf_master_metadata" in ctx
    md = ctx["etf_master_metadata"]
    assert md["universe_status"] == "active_core"
    assert md["needs_order_screen_check"] is True
    # All-agents-evaluate instruction is injected into the prompt context.
    assert "全メンバーが全ETFを独立に評価" in ctx["instruction"]


def test_etf_master_candidate_context_unknown_symbol(tmp_path):
    ctx = build_candidate_context(_candidate("NOPE"), etf_master=_master(tmp_path))
    assert ctx["etf_master_metadata"]["known_in_master"] is False
    assert ctx["etf_master_metadata"]["universe_status"] == "unknown"


# ── review_candidate enrichment (client=None -> deterministic) ──────────────────


def test_etf_master_review_candidate_surfaces_order_screen(tmp_path):
    cfg = load_committee_config()
    r = review_candidate(_candidate("ITA"), cfg, None, etf_master=_master(tmp_path))
    assert r.needs_order_screen_check is True
    assert r.universe_status == "active_core"
    assert r.etf_master_known is True
    assert "paul_tudor_jones" in r.agent_affinity


def test_etf_master_review_candidate_data_quality_warning(tmp_path):
    cfg = load_committee_config()
    r = review_candidate(_candidate("ITA"), cfg, None, etf_master=_master(tmp_path))
    assert r.data_quality_status == "needs_broker_check"
    assert r.data_quality_warning
    assert any("データ品質警告" in x for x in r.key_risks)


def test_etf_master_review_candidate_unknown_symbol(tmp_path):
    cfg = load_committee_config()
    r = review_candidate(_candidate("WHATEVER"), cfg, None, etf_master=_master(tmp_path))
    assert r.etf_master_known is False
    assert r.universe_status == "unknown"


def test_etf_master_review_candidate_without_master_unchanged(tmp_path):
    cfg = load_committee_config()
    r = review_candidate(_candidate("ITA"), cfg, None)
    assert r.needs_order_screen_check is False
    assert r.universe_status == ""
    assert r.etf_master_known is True  # default when no master provided


# ── formatters ─────────────────────────────────────────────────────────────────


def test_etf_master_markdown_shows_order_screen_warning(tmp_path):
    cfg = load_committee_config()
    r = review_candidate(_candidate("ITA"), cfg, None, etf_master=_master(tmp_path))
    md = build_candidate_markdown([r])
    assert "発注前確認要" in md
    assert "active_core" in md
    assert "データ品質警告" in md


def test_etf_master_slack_shows_order_screen_warning(tmp_path):
    cfg = load_committee_config()
    r = review_candidate(_candidate("ITA"), cfg, None, etf_master=_master(tmp_path))
    slack = build_candidate_slack_summary(r)
    assert "発注前確認要" in slack


# ── safety invariants ──────────────────────────────────────────────────────────


def test_etf_master_enrichment_no_allocation_or_order_quantity(tmp_path):
    enr = build_enrichment(_master(tmp_path)["ITA"])
    blob = json.dumps(enr, ensure_ascii=False)
    for k in ("final_allocation", "weights", "allocation_override",
              "quantity", "shares", "order_amount", "order_quantity"):
        assert k not in blob


def test_etf_master_no_auto_trade_language(tmp_path):
    cfg = load_committee_config()
    r = review_candidate(_candidate("ITA"), cfg, None, etf_master=_master(tmp_path))
    md = build_candidate_markdown([r])
    slack = build_candidate_slack_summary(r)
    for k in ("auto_trade", "place_order", "execute_trade", "買え", "売れ"):
        assert k not in md
        assert k not in slack


def test_etf_master_entry_has_no_order_quantity_fields():
    fields = set(EtfMasterEntry.model_fields)
    for k in ("quantity", "shares", "order_amount", "order_quantity", "weight"):
        assert k not in fields
