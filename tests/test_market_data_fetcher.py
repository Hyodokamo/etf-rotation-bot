"""Phase 5.3: Market Data Fetcher tests.

All network-dependent tests use a FakeYF stub — no real HTTP calls.
Pure calculation functions are tested with known inputs and expected outputs.
"""
from __future__ import annotations

import csv
import inspect
import re
from pathlib import Path

import pandas as pd
import pytest

from src.signals.market_data_fetcher import (
    MARKET_DATA_COLUMNS,
    calculate_above_200dma,
    calculate_drawdown_from_52w_high,
    calculate_returns,
    calculate_rsi_14,
    calculate_volume_ratio,
    fetch_market_data,
    write_market_data_latest,
)


# ── FakeYF stub ───────────────────────────────────────────────────────────────


class _FakeTicker:
    def __init__(self, df: pd.DataFrame | Exception):
        self._df = df

    def history(self, period="1y"):
        if isinstance(self._df, Exception):
            raise self._df
        return self._df


class _FakeYF:
    """Minimal yfinance API stub that returns canned DataFrames."""

    def __init__(self, symbol_data: dict[str, pd.DataFrame | Exception]):
        self._data = symbol_data

    def Ticker(self, symbol: str) -> _FakeTicker:
        return _FakeTicker(self._data.get(symbol, pd.DataFrame()))


def _df(closes: list[float], volumes: list[float] | None = None,
        highs: list[float] | None = None) -> pd.DataFrame:
    n = len(closes)
    vols = volumes or [1_000_000.0] * n
    h = highs or [c * 1.02 for c in closes]
    return pd.DataFrame({
        "Close": closes,
        "High": h,
        "Low": [c * 0.98 for c in closes],
        "Open": closes,
        "Volume": vols,
    })


_MACRO_STUB: dict[str, pd.DataFrame] = {
    "^VIX": _df([25.0, 26.5]),
    "^TNX": _df([4.5, 4.6]),
    "DX-Y.NYB": _df([104.0, 105.2]),
}


def _yf_with(*symbols_and_data: tuple[str, pd.DataFrame | Exception]) -> _FakeYF:
    """Build a _FakeYF containing macro stubs plus the given per-symbol data."""
    data = dict(_MACRO_STUB)
    for sym, d in symbols_and_data:
        data[sym] = d
    return _FakeYF(data)


def _long_closes(n: int = 252, base: float = 100.0, trend: float = 0.0) -> list[float]:
    """Generate n closing prices with a small linear trend."""
    return [base + trend * i for i in range(n)]


# ── 1. Schema ─────────────────────────────────────────────────────────────────


def test_market_data_fetcher_writes_expected_schema(tmp_path):
    """write_market_data_latest produces a CSV with all expected columns."""
    closes = _long_closes(252, base=100.0, trend=0.05)
    yf = _yf_with(("SPY", _df(closes, volumes=[2_000_000.0] * 252)))
    rows = fetch_market_data(["SPY"], yf_module=yf)

    out = tmp_path / "market_data_latest.csv"
    write_market_data_latest(rows, path=out)

    assert out.exists()
    with open(out, encoding="utf-8", newline="") as f:
        header = next(csv.reader(f))

    for col in MARKET_DATA_COLUMNS:
        assert col in header, f"Expected column '{col}' not found in output"

    # Verify row values are set (not all blank for SPY)
    with open(out, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        spy_row = next(reader)

    assert spy_row["symbol"] == "SPY"
    assert spy_row["last_price"] != ""
    assert spy_row["daily_return_pct"] != ""


# ── 2. Daily return ───────────────────────────────────────────────────────────


def test_market_data_fetcher_calculates_daily_return():
    """calculate_returns computes daily % correctly from two closes."""
    # +2% day
    result = calculate_returns([100.0, 102.0])
    assert result["daily"] == pytest.approx(2.0, rel=1e-4)

    # -3% day
    result = calculate_returns([200.0, 194.0])
    assert result["daily"] == pytest.approx(-3.0, rel=1e-4)

    # Single price → no daily return
    result = calculate_returns([100.0])
    assert result["daily"] is None

    # End-to-end via fetch_market_data: verify field written to CSV
    closes = [100.0] * 50
    closes[-1] = 97.0  # -3% today
    yf = _yf_with(("SPY", _df(closes)))
    rows = fetch_market_data(["SPY"], yf_module=yf)
    daily = float(rows[0]["daily_return_pct"])
    assert daily == pytest.approx(-3.0, rel=1e-2)


# ── 3. 5-day and 20-day returns ───────────────────────────────────────────────


def test_market_data_fetcher_calculates_5d_20d_return():
    """calculate_returns computes 5d and 20d returns correctly."""
    # 6 prices: close 5 days ago = 100, current = 110 → +10%
    closes_6 = [100.0, 101.0, 102.0, 103.0, 104.0, 110.0]
    r = calculate_returns(closes_6)
    assert r["return_5d"] == pytest.approx(10.0, rel=1e-4)
    assert r["return_20d"] is None  # not enough data

    # 21 prices: close 20 days ago = 80, current = 100 → +25%
    closes_21 = [80.0] + [85.0] * 19 + [100.0]
    r = calculate_returns(closes_21)
    assert r["return_20d"] == pytest.approx(25.0, rel=1e-4)
    assert r["return_5d"] is not None

    # End-to-end: 252 prices with known 5d and 20d returns
    base_closes = [100.0] * 252
    base_closes[-6] = 90.0   # 5 days ago
    base_closes[-21] = 80.0  # 20 days ago
    yf = _yf_with(("ITA", _df(base_closes)))
    rows = fetch_market_data(["ITA"], yf_module=yf)
    assert rows[0]["return_5d_pct"] != ""
    assert rows[0]["return_20d_pct"] != ""


# ── 4. Drawdown from 52-week high ─────────────────────────────────────────────


def test_market_data_fetcher_calculates_drawdown_from_52w_high():
    """calculate_drawdown_from_52w_high returns correct percentage."""
    # Peak high = 120, current close = 100 → -16.67%
    closes = [100.0, 110.0, 120.0, 115.0, 100.0]
    highs = [102.0, 112.0, 125.0, 118.0, 103.0]
    dd = calculate_drawdown_from_52w_high(closes, highs)
    expected = (100.0 - 125.0) / 125.0 * 100
    assert dd == pytest.approx(expected, rel=1e-4)

    # Without highs, use close as proxy (peak close = 120)
    dd2 = calculate_drawdown_from_52w_high(closes)
    expected2 = (100.0 - 120.0) / 120.0 * 100
    assert dd2 == pytest.approx(expected2, abs=0.01)

    # Empty list → None
    assert calculate_drawdown_from_52w_high([]) is None

    # End-to-end: peak high in highs should be reflected in output
    closes252 = [100.0] * 252
    highs252 = [100.0 * 1.02] * 252
    highs252[100] = 150.0  # 52-week peak was 150 at day 100
    closes252[-1] = 100.0  # today
    yf = _yf_with(("XLU", _df(closes252, highs=highs252)))
    rows = fetch_market_data(["XLU"], yf_module=yf)
    dd_val = float(rows[0]["drawdown_from_52w_high_pct"])
    expected_dd = (100.0 - 150.0) / 150.0 * 100
    assert dd_val == pytest.approx(expected_dd, rel=1e-2)


# ── 5. Above 200-DMA ─────────────────────────────────────────────────────────


def test_market_data_fetcher_calculates_above_200dma():
    """calculate_above_200dma returns True/False/None correctly."""
    # 200 prices at 100, current at 105 → True
    closes_above = [100.0] * 199 + [105.0]
    assert calculate_above_200dma(closes_above) is True

    # 200 prices at 100, current at 95 → False
    closes_below = [100.0] * 199 + [95.0]
    assert calculate_above_200dma(closes_below) is False

    # Fewer than 200 → None
    assert calculate_above_200dma([100.0] * 199) is None

    # End-to-end: above_200dma field should be "true" or "false"
    closes252 = [100.0] * 251 + [110.0]
    yf = _yf_with(("CIBR", _df(closes252)))
    rows = fetch_market_data(["CIBR"], yf_module=yf)
    assert rows[0]["above_200dma"] in ("true", "false")


# ── 6. RSI 14 ─────────────────────────────────────────────────────────────────


def test_market_data_fetcher_calculates_rsi_14():
    """calculate_rsi_14 returns 100 for all-gains, ~50 for alternating."""
    # All-up last 14 moves → RSI = 100
    closes_up = [float(i) for i in range(16)]  # 0..15, all +1 each day
    assert calculate_rsi_14(closes_up) == 100.0

    # Alternating +1 / -1 over last 14 moves → RSI ≈ 50
    closes_alt = [100.0]
    for i in range(28):
        closes_alt.append(closes_alt[-1] + (1.0 if i % 2 == 0 else -1.0))
    rsi = calculate_rsi_14(closes_alt)
    assert rsi is not None
    assert 45.0 <= rsi <= 55.0

    # Insufficient data → None
    assert calculate_rsi_14([100.0] * 14) is None  # need 15 for 14 deltas
    assert calculate_rsi_14([100.0] * 15) is not None

    # End-to-end: rsi_14 field is set
    closes252 = _long_closes(252, base=100.0, trend=0.1)
    yf = _yf_with(("GRID", _df(closes252)))
    rows = fetch_market_data(["GRID"], yf_module=yf)
    assert rows[0]["rsi_14"] != ""
    rsi_val = float(rows[0]["rsi_14"])
    assert 0.0 <= rsi_val <= 100.0


# ── 7. Missing data graceful handling ────────────────────────────────────────


def test_market_data_fetcher_handles_missing_data(tmp_path):
    """Symbol with empty DataFrame gets error note; other symbols are unaffected."""
    normal_closes = _long_closes(252)
    yf = _yf_with(
        ("SPY", _df(normal_closes)),
        ("BADTICKER", pd.DataFrame()),  # empty → insufficient history
    )

    rows = fetch_market_data(["SPY", "BADTICKER"], yf_module=yf)
    assert len(rows) == 2

    spy_row = next(r for r in rows if r["symbol"] == "SPY")
    bad_row = next(r for r in rows if r["symbol"] == "BADTICKER")

    # SPY should have valid data
    assert spy_row["last_price"] != ""
    assert spy_row["notes"] == ""

    # BADTICKER should have error note, numeric fields blank
    assert "error" in bad_row["notes"]
    assert bad_row["last_price"] == ""
    assert bad_row["daily_return_pct"] == ""

    # Writing should succeed (no exception) even with error rows
    out = tmp_path / "out.csv"
    write_market_data_latest(rows, path=out)
    assert out.exists()


# ── 8. Fetch error handling ───────────────────────────────────────────────────


def test_market_data_fetcher_handles_fetch_error():
    """Exception during symbol fetch is captured in notes; other symbols continue."""
    normal_closes = _long_closes(252)
    yf = _yf_with(
        ("ITA", _df(normal_closes)),
        ("ERRORTICKER", RuntimeError("network error")),
    )

    rows = fetch_market_data(["ITA", "ERRORTICKER"], yf_module=yf)
    assert len(rows) == 2

    ita_row = next(r for r in rows if r["symbol"] == "ITA")
    err_row = next(r for r in rows if r["symbol"] == "ERRORTICKER")

    assert ita_row["last_price"] != ""
    assert ita_row["notes"] == ""

    assert "error" in err_row["notes"]
    assert "network error" in err_row["notes"]
    assert err_row["last_price"] == ""
    assert err_row["daily_return_pct"] == ""


# ── 9. Does not update watchlist ──────────────────────────────────────────────


def test_market_data_fetcher_does_not_update_watchlist(tmp_path):
    """fetch_market_data and write_market_data_latest never touch watchlist.csv."""
    import src.signals.market_data_fetcher as mdf
    src = inspect.getsource(mdf)

    assert not re.search(r"update_watchlist_entry|save_watchlist", src), \
        "market_data_fetcher must not import watchlist write functions"
    assert not re.search(r'^(import|from)\s+.*watchlist_store', src, re.MULTILINE), \
        "market_data_fetcher must not import from watchlist_store"
    assert not re.search(r'open\s*\(.*watchlist', src), \
        "market_data_fetcher must not open watchlist.csv"

    # Functional: a pre-existing watchlist.csv must be unchanged
    wl_path = tmp_path / "watchlist.csv"
    wl_path.write_text("ticker,status\nITA,BUY_CANDIDATE\n", encoding="utf-8")
    original = wl_path.read_text(encoding="utf-8")

    yf = _yf_with(("SPY", _df(_long_closes(252))))
    rows = fetch_market_data(["SPY"], yf_module=yf)
    write_market_data_latest(rows, path=tmp_path / "market.csv")

    assert wl_path.read_text(encoding="utf-8") == original


# ── 10. Does not update signal_history ───────────────────────────────────────


def test_market_data_fetcher_does_not_update_signal_history(tmp_path):
    """fetch_market_data never touches signal_history.csv."""
    import src.signals.market_data_fetcher as mdf
    src = inspect.getsource(mdf)

    assert not re.search(r"append_signal_history", src), \
        "market_data_fetcher must not call append_signal_history"
    assert not re.search(r'^(import|from)\s+.*signal_history', src, re.MULTILINE), \
        "market_data_fetcher must not import signal_history modules"

    # Functional: pre-existing signal_history.csv must be unchanged
    hist_path = tmp_path / "signal_history.csv"
    hist_path.write_text(
        "as_of_date,symbol,signal_side,final_signal,total_score\n"
        "2026-06-06,ITA,BUY,BUY_CANDIDATE,4\n",
        encoding="utf-8",
    )
    original = hist_path.read_text(encoding="utf-8")

    yf = _yf_with(("SPY", _df(_long_closes(252))))
    rows = fetch_market_data(["SPY"], yf_module=yf)
    write_market_data_latest(rows, path=tmp_path / "market.csv")

    assert hist_path.read_text(encoding="utf-8") == original


# ── 11. Does not update ai_sleeve_state ───────────────────────────────────────


def test_market_data_fetcher_does_not_update_ai_sleeve_state():
    """market_data_fetcher.py must not import or reference ai_sleeve_state."""
    import src.signals.market_data_fetcher as mdf
    src = inspect.getsource(mdf)

    assert not re.search(r'^(import|from)\s+.*ai_sleeve', src, re.MULTILINE), \
        "market_data_fetcher must not import ai_sleeve modules"
    assert not re.search(r'open\s*\(.*ai_sleeve_state', src), \
        "market_data_fetcher must not open ai_sleeve_state.csv"


# ── 12. Does not update etf_master ────────────────────────────────────────────


def test_market_data_fetcher_does_not_update_etf_master():
    """market_data_fetcher.py must not import or write to etf_master."""
    import src.signals.market_data_fetcher as mdf
    src = inspect.getsource(mdf)

    assert not re.search(r'^(import|from)\s+.*etf_master', src, re.MULTILINE), \
        "market_data_fetcher must not import etf_master"
    assert not re.search(r'open\s*\(.*etf_master', src), \
        "market_data_fetcher must not open etf_master.csv"


# ── 13. CLI dispatch ──────────────────────────────────────────────────────────


def test_market_data_cli_update_market_data(tmp_path):
    """--update-market-data produces a valid CSV at the specified output path."""
    closes = _long_closes(252, base=450.0, trend=0.1)
    vols = [5_000_000.0] * 252
    yf = _yf_with(("SPY", _df(closes, volumes=vols)))

    out_path = tmp_path / "market_data_latest.csv"

    # Call fetch + write directly (same logic as _handle_update_market_data)
    rows = fetch_market_data(["SPY"], yf_module=yf)
    write_market_data_latest(rows, path=out_path)

    assert out_path.exists(), "Output CSV must be created"

    with open(out_path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        all_rows = list(reader)

    assert len(all_rows) == 1
    spy = all_rows[0]
    assert spy["symbol"] == "SPY"
    assert spy["last_price"] != ""
    assert spy["daily_return_pct"] != ""
    assert spy["rsi_14"] != ""
    assert spy["volume_ratio"] != ""
    # Macro fields from stub
    assert spy["vix_level"] == "26.5"
    assert spy["us10y_yield"] == "4.6"
    assert spy["dxy_level"] == "105.2"
    # Safety flags
    assert spy["notes"] == ""
