"""Tests for Phase 5.1: signal_committee_runner.py."""
import pytest

from src.committee.runner import load_committee_config
from src.signals.signal_committee_runner import (
    _SIGNAL_PERSONAS,
    run_signal_committee,
)
from src.signals.signal_config import load_signal_config
from src.signals.signal_context_builder import build_signal_context

_KNOWN_MEMBER_IDS = {
    "aqr_meb", "howard_marks", "rob_arnott", "core_ai_auditor",
    "buffett", "paul_tudor_jones", "druckenmiller",
}


def _cfg():
    return load_signal_config("/nonexistent/no_config.yaml")


def _ctx(symbol="GRID"):
    return build_signal_context(symbol, {}, _cfg())


# ── uses existing members ─────────────────────────────────────────────────────


def test_signal_committee_uses_existing_members():
    """Signal runner must use only the 7 members from committee.yaml."""
    committee_cfg = load_committee_config()
    configured_ids = {
        m.member_id
        for m in committee_cfg.core_committee + committee_cfg.satellite_committee
    }
    assert configured_ids == _KNOWN_MEMBER_IDS
    outputs = run_signal_committee(_ctx(), committee_cfg, client=None)
    output_ids = {o.member_id for o in outputs}
    assert output_ids == configured_ids


def test_signal_committee_does_not_create_new_functional_agents():
    """Signal committee runner must not define new agent personas beyond the existing 7."""
    # All persona keys must be in the known 7
    unknown = set(_SIGNAL_PERSONAS.keys()) - _KNOWN_MEMBER_IDS
    assert not unknown, f"New agent personas found in _SIGNAL_PERSONAS: {unknown}"
    # Also check no new agent source files were created
    import importlib, inspect
    mod = importlib.import_module("src.signals.signal_committee_runner")
    src = inspect.getsource(mod)
    for new_agent in ("market_regime_agent", "semiconductor_ai_stress_agent",
                      "etf_entry_agent", "portfolio_risk_agent",
                      "theme_conviction_agent", "skeptic_agent",
                      "committee_chair"):
        assert new_agent not in src.lower(), f"New agent type '{new_agent}' found in runner"


def test_signal_committee_all_members_review_all_etfs():
    """All committee members must produce output for every ETF evaluated."""
    committee_cfg = load_committee_config()
    total = len(committee_cfg.core_committee) + len(committee_cfg.satellite_committee)

    for symbol in ("GRID", "ITA", "QQQM"):
        outputs = run_signal_committee(build_signal_context(symbol, {}, _cfg()),
                                       committee_cfg, client=None)
        assert len(outputs) == total, f"Expected {total} outputs for {symbol}, got {len(outputs)}"
        output_ids = {o.member_id for o in outputs}
        assert output_ids == _KNOWN_MEMBER_IDS


def test_signal_committee_affinity_not_filter():
    """agent_affinity in ETF master context must NOT restrict which members evaluate."""
    committee_cfg = load_committee_config()
    total = len(committee_cfg.core_committee) + len(committee_cfg.satellite_committee)

    ctx = _ctx("GRID")
    # Simulate ETF with affinity for only 1 member
    ctx.etf_master_metadata["agent_affinity_hint"] = ["aqr_meb"]

    outputs = run_signal_committee(ctx, committee_cfg, client=None)
    # All members must still evaluate (affinity is a hint, not a filter)
    assert len(outputs) == total
    assert {o.member_id for o in outputs} == _KNOWN_MEMBER_IDS


def test_signal_committee_client_none_returns_neutral_fallbacks():
    """With no LLM client, all members return neutral stance and score=0."""
    committee_cfg = load_committee_config()
    outputs = run_signal_committee(_ctx(), committee_cfg, client=None)
    for o in outputs:
        assert o.score == 0
        assert o.veto is False
        assert o.confidence == 0.0
