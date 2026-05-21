import numpy as np
import pandas as pd
import pytest

from src.config_loader import ScoringConfig
from src.scoring import compute_scores


def make_indicators(n_assets: int = 5) -> pd.DataFrame:
    rng = np.random.default_rng(0)
    tickers = [f"T{i}" for i in range(n_assets)]
    df = pd.DataFrame(
        {
            "mom_1m": rng.uniform(-0.1, 0.2, n_assets),
            "mom_3m": rng.uniform(-0.1, 0.3, n_assets),
            "mom_6m": rng.uniform(-0.1, 0.4, n_assets),
            "mom_12m": rng.uniform(-0.2, 0.5, n_assets),
            "sma50_signal": rng.choice([0.0, 1.0], n_assets),
            "sma200_signal": rng.choice([0.0, 1.0], n_assets),
            "vol_20d": rng.uniform(0.05, 0.30, n_assets),
        },
        index=tickers,
    )
    return df


@pytest.fixture
def cfg():
    return ScoringConfig()


def test_scores_length_matches_input(cfg):
    ind = make_indicators(5)
    scores = compute_scores(ind, cfg)
    assert len(scores) == 5


def test_scores_are_positive(cfg):
    ind = make_indicators(10)
    scores = compute_scores(ind, cfg)
    assert (scores >= 0).all()


def test_higher_momentum_higher_score(cfg):
    # Two assets: one with all-high momentum, one with all-low
    df = pd.DataFrame(
        {
            "mom_1m": [0.10, -0.05],
            "mom_3m": [0.20, -0.10],
            "mom_6m": [0.30, -0.15],
            "mom_12m": [0.40, -0.20],
            "sma50_signal": [1.0, 0.0],
            "sma200_signal": [1.0, 0.0],
            "vol_20d": [0.15, 0.15],
        },
        index=["HIGH", "LOW"],
    )
    scores = compute_scores(df, cfg)
    assert scores["HIGH"] > scores["LOW"]


def test_empty_indicators_returns_empty(cfg):
    scores = compute_scores(pd.DataFrame(), cfg)
    assert scores.empty


def test_vol_adjust_favors_low_vol(cfg):
    df = pd.DataFrame(
        {
            "mom_1m": [0.05, 0.05],
            "mom_3m": [0.10, 0.10],
            "mom_6m": [0.15, 0.15],
            "mom_12m": [0.20, 0.20],
            "sma50_signal": [1.0, 1.0],
            "sma200_signal": [1.0, 1.0],
            "vol_20d": [0.10, 0.25],
        },
        index=["LOW_VOL", "HIGH_VOL"],
    )
    cfg_adj = ScoringConfig(vol_adjust=True)
    scores = compute_scores(df, cfg_adj)
    assert scores["LOW_VOL"] > scores["HIGH_VOL"]
