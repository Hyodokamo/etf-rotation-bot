import numpy as np
import pandas as pd
import pytest

from src.config_loader import ScoringConfig
from src.indicators import compute_indicators


def make_price_df(n: int = 300, tickers: list[str] | None = None) -> pd.DataFrame:
    if tickers is None:
        tickers = ["AAA", "BBB"]
    rng = np.random.default_rng(42)
    idx = pd.date_range("2022-01-01", periods=n, freq="B")
    data = {}
    for t in tickers:
        returns = rng.normal(0.0005, 0.01, n)
        prices = 100 * np.cumprod(1 + returns)
        data[t] = prices
    return pd.DataFrame(data, index=idx)


@pytest.fixture
def cfg():
    return ScoringConfig()


def test_returns_expected_columns(cfg):
    prices = make_price_df(300)
    result = compute_indicators(prices, cfg)
    assert "mom_1m" in result.columns
    assert "mom_3m" in result.columns
    assert "mom_6m" in result.columns
    assert "mom_12m" in result.columns
    assert "vol_20d" in result.columns
    assert "sma50_signal" in result.columns
    assert "sma200_signal" in result.columns


def test_index_is_tickers(cfg):
    prices = make_price_df(300, tickers=["VOO", "TLT"])
    result = compute_indicators(prices, cfg)
    assert set(result.index) == {"VOO", "TLT"}


def test_vol_is_positive(cfg):
    prices = make_price_df(300)
    result = compute_indicators(prices, cfg)
    assert (result["vol_20d"] > 0).all()


def test_sma_signal_is_binary(cfg):
    prices = make_price_df(300)
    result = compute_indicators(prices, cfg)
    assert result["sma50_signal"].isin([0.0, 1.0]).all()
    assert result["sma200_signal"].isin([0.0, 1.0]).all()


def test_insufficient_history_skipped(cfg):
    prices = make_price_df(100, tickers=["SHORT"])
    result = compute_indicators(prices, cfg)
    assert result.empty or "SHORT" not in result.index


def test_momentum_direction(cfg):
    # Trending up asset should have positive 12m momentum
    idx = pd.date_range("2022-01-01", periods=300, freq="B")
    prices = pd.DataFrame({"UP": np.linspace(100, 200, 300)}, index=idx)
    result = compute_indicators(prices, cfg)
    assert result.loc["UP", "mom_12m"] > 0
