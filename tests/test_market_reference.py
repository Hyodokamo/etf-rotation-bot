"""Phase 5.3.1: Market Reference / Candidate Universe Separation tests.

Verifies that SPY/QQQ/SOXX/SMH (market_reference) are never nominated as
BUY_CANDIDATE or HIGH_PRIORITY_CANDIDATE, while ITA/XLU/PAVE/AVUV/CIBR/GRID/BOTZ
(candidate) remain eligible for normal evaluation.

Safety invariants tested:
- market_reference symbols yield WATCH or NO_ACTION only — never BUY_CANDIDATE
- watchlist_update is None for reference_only results
- no_order_quantity / no_auto_trade always True
- candidate universe excludes market_reference symbols
"""
from __future__ import annotations

import pandas as pd
import pytest

from src.signals.market_data_fetcher import (
    MARKET_REFERENCE_ROLES,
    get_candidate_symbols,
    get_symbol_roles,
    is_market_reference,
    load_market_data_config,
)
from src.signals.signal_models import (
    FinalSignal,
    MemberStance,
    SignalContext,
    SignalMemberOutput,
    SignalSide,
)

MARKET_REFERENCE_SYMBOLS = {"SPY", "QQQ", "SOXX", "SMH"}
CANDIDATE_SYMBOLS = {"GRID", "BOTZ", "ITA", "XLU", "PAVE", "AVUV", "CIBR"}

CONFIG_PATH = "config/market_data_config.yaml"


# ── Config / role helpers ─────────────────────────────────────────────────────


def test_market_data_config_has_symbol_roles():
    cfg = load_market_data_config(CONFIG_PATH)
    assert "symbols" in cfg, "config must have `symbols` list with role fields"
    roles = get_symbol_roles(cfg)
    for sym in MARKET_REFERENCE_SYMBOLS:
        assert roles.get(sym) == "market_reference", f"{sym} must be market_reference"
    for sym in CANDIDATE_SYMBOLS:
        assert roles.get(sym) == "candidate", f"{sym} must be candidate"


def test_market_data_fetcher_outputs_symbol_role():
    """fetch_market_data rows must include a `role` field matching config."""
    from src.signals.market_data_fetcher import MARKET_DATA_COLUMNS, fetch_market_data

    assert "role" in MARKET_DATA_COLUMNS

    # Use FakeYF so no real network call
    closes = [float(100 + i) for i in range(260)]
    volumes = [float(1_000_000)] * 260
    df = pd.DataFrame({
        "Close": closes,
        "High": [c + 1 for c in closes],
        "Volume": volumes,
    })

    class _FakeTicker:
        def __init__(self, d):
            self._d = d
        def history(self, period="1y"):
            return self._d

    class _FakeYF:
        def Ticker(self, sym):
            return _FakeTicker(df)

    cfg = load_market_data_config(CONFIG_PATH)
    roles = get_symbol_roles(cfg)
    rows = fetch_market_data(
        symbols=["SPY", "ITA"],
        yf_module=_FakeYF(),
        _symbol_roles=roles,
    )
    by_sym = {r["symbol"]: r for r in rows}
    assert by_sym["SPY"]["role"] == "market_reference"
    assert by_sym["ITA"]["role"] == "candidate"


# ── is_market_reference helper ────────────────────────────────────────────────


def test_is_market_reference_true_for_reference_symbols():
    roles = {"SPY": "market_reference", "QQQ": "market_reference",
             "SOXX": "market_reference", "SMH": "market_reference",
             "ITA": "candidate"}
    for sym in ("SPY", "QQQ", "SOXX", "SMH"):
        assert is_market_reference(sym, roles)


def test_is_market_reference_false_for_candidates():
    roles = {"ITA": "candidate", "XLU": "candidate", "GRID": "candidate"}
    for sym in ("ITA", "XLU", "GRID"):
        assert not is_market_reference(sym, roles)


# ── get_candidate_symbols ─────────────────────────────────────────────────────


def test_crash_signal_all_symbols_excludes_market_reference_from_candidates():
    cfg = load_market_data_config(CONFIG_PATH)
    candidates = get_candidate_symbols(cfg)
    candidate_set = {s.upper() for s in candidates}
    for ref_sym in MARKET_REFERENCE_SYMBOLS:
        assert ref_sym not in candidate_set, f"{ref_sym} must not appear in candidate universe"


def test_candidate_universe_uses_active_core_or_watchlist():
    cfg = load_market_data_config(CONFIG_PATH)
    candidates = get_candidate_symbols(cfg)
    candidate_set = {s.upper() for s in candidates}
    for sym in CANDIDATE_SYMBOLS:
        assert sym in candidate_set, f"{sym} must be in candidate universe"


# ── signal_engine reference_only hard-block ───────────────────────────────────


def _neutral_members(n: int = 7) -> list[SignalMemberOutput]:
    return [
        SignalMemberOutput(
            member_id=f"m{i}",
            stance=MemberStance.POSITIVE,
            score=2,
            confidence=0.9,
            rationale="strong buy",
        )
        for i in range(n)
    ]


def _make_config():
    from src.signals.signal_config import load_signal_config
    return load_signal_config("config/signal_config.yaml")


def _run_aggregate(symbol: str, reference_only: bool, has_triggers: bool):
    from src.signals.signal_engine import aggregate_signal

    cfg = _make_config()
    members = _neutral_members()
    ctx = SignalContext(
        symbol=symbol,
        market_regime="correction" if has_triggers else "neutral",
        semiconductor_stress="macro_selloff" if has_triggers else "normal",
        reference_only=reference_only,
        crash_triggers=["急落シグナル"] if has_triggers else [],
    )
    return aggregate_signal(symbol, SignalSide.BUY, members, ctx, cfg)


def test_signal_engine_spy_reference_not_buy_candidate():
    result = _run_aggregate("SPY", reference_only=True, has_triggers=True)
    assert result.final_signal in (FinalSignal.WATCH, FinalSignal.NO_ACTION)
    assert result.final_signal not in (FinalSignal.BUY_CANDIDATE, FinalSignal.HIGH_PRIORITY_CANDIDATE)


def test_signal_engine_qqq_reference_not_buy_candidate():
    result = _run_aggregate("QQQ", reference_only=True, has_triggers=True)
    assert result.final_signal in (FinalSignal.WATCH, FinalSignal.NO_ACTION)


def test_signal_engine_soxx_reference_used_for_stress_but_not_candidate():
    result = _run_aggregate("SOXX", reference_only=True, has_triggers=False)
    assert result.final_signal in (FinalSignal.WATCH, FinalSignal.NO_ACTION)


def test_signal_engine_smh_reference_used_for_stress_but_not_candidate():
    result = _run_aggregate("SMH", reference_only=True, has_triggers=False)
    assert result.final_signal in (FinalSignal.WATCH, FinalSignal.NO_ACTION)


def test_signal_symbol_reference_returns_watch_or_no_action():
    """reference_only=True with triggers → WATCH; without → NO_ACTION."""
    result_with = _run_aggregate("SPY", reference_only=True, has_triggers=True)
    result_without = _run_aggregate("SPY", reference_only=True, has_triggers=False)
    assert result_with.final_signal == FinalSignal.WATCH
    assert result_without.final_signal == FinalSignal.NO_ACTION


def test_reference_symbol_report_mentions_not_candidate():
    result = _run_aggregate("SPY", reference_only=True, has_triggers=True)
    text = result.recommended_action_text
    assert "市場参照用" in text or "買い候補化対象ではありません" in text


def test_watchlist_does_not_add_reference_symbol():
    """reference_only result has watchlist_update=None → watchlist unchanged."""
    from src.signals.watchlist_store import update_watchlist_entry

    result = _run_aggregate("SPY", reference_only=True, has_triggers=True)
    assert result.watchlist_update is None, "watchlist_update must be None for reference symbols"

    initial_watchlist: list[dict] = []
    updated = update_watchlist_entry(initial_watchlist, result, dry_run=True)
    assert all(row.get("ticker") != "SPY" for row in updated), \
        "SPY must not be inserted into watchlist as a candidate"


def test_reference_symbol_no_order_quantity_auto_trade():
    result = _run_aggregate("QQQ", reference_only=True, has_triggers=True)
    assert result.no_order_quantity is True
    assert result.no_auto_trade is True
    assert result.sell_signal_reserved is True
    assert result.watchlist_update is None


# ── risk_flags contains marker ────────────────────────────────────────────────


def test_reference_symbol_risk_flag_present():
    result = _run_aggregate("SPY", reference_only=True, has_triggers=True)
    assert any("reference_symbol_not_candidate" in f for f in result.risk_flags)
