import pandas as pd
import pytest

from src.allocation import compute_allocation, _normalize, _apply_asset_cap
from src.config_loader import AllocationConfig, RiskConfig


def make_scores(n: int = 10) -> pd.Series:
    return pd.Series(
        {f"T{i}": float(n - i) for i in range(n)},
        name="score",
    )


def make_indicators(n: int = 10) -> pd.DataFrame:
    return pd.DataFrame(
        {"vol_20d": [0.15] * n},
        index=[f"T{i}" for i in range(n)],
    )


def make_cat_map(n: int = 10) -> dict[str, str]:
    cats = ["core_equity", "bond", "commodity", "reit", "sector"]
    return {f"T{i}": cats[i % len(cats)] for i in range(n)}


@pytest.fixture
def alloc_cfg():
    return AllocationConfig(top_n=5, min_assets=3)


@pytest.fixture
def risk_cfg():
    return RiskConfig(max_weight_per_asset=0.30, min_weight_per_selected=0.01)


def test_weights_sum_to_one(alloc_cfg, risk_cfg):
    scores = make_scores(10)
    ind = make_indicators(10)
    cat_map = make_cat_map(10)
    weights = compute_allocation(scores, ind, cat_map, alloc_cfg, risk_cfg)
    assert abs(sum(weights.values()) - 1.0) < 1e-6


def test_max_weight_respected(alloc_cfg, risk_cfg):
    scores = make_scores(10)
    ind = make_indicators(10)
    cat_map = make_cat_map(10)
    weights = compute_allocation(scores, ind, cat_map, alloc_cfg, risk_cfg)
    assert all(w <= risk_cfg.max_weight_per_asset + 1e-9 for w in weights.values())


def test_top_n_at_most(alloc_cfg, risk_cfg):
    scores = make_scores(10)
    ind = make_indicators(10)
    cat_map = make_cat_map(10)
    weights = compute_allocation(scores, ind, cat_map, alloc_cfg, risk_cfg)
    assert len(weights) <= alloc_cfg.top_n


def test_category_cap_respected():
    scores = pd.Series({"A": 5.0, "B": 4.0, "C": 3.0}, name="score")
    ind = pd.DataFrame({"vol_20d": [0.15, 0.15, 0.15]}, index=["A", "B", "C"])
    cat_map = {"A": "core_equity", "B": "core_equity", "C": "bond"}
    risk_cfg = RiskConfig(
        max_weight_per_asset=0.60,
        min_weight_per_selected=0.01,
        max_category_weights={"core_equity": 0.50},
    )
    alloc_cfg = AllocationConfig(top_n=3, min_assets=1)
    weights = compute_allocation(scores, ind, cat_map, alloc_cfg, risk_cfg)
    equity_total = sum(w for t, w in weights.items() if cat_map[t] == "core_equity")
    assert equity_total <= 0.50 + 1e-6


def test_normalize():
    w = {"A": 2.0, "B": 3.0, "C": 5.0}
    n = _normalize(w)
    assert abs(sum(n.values()) - 1.0) < 1e-9
    assert abs(n["C"] - 0.5) < 1e-9


def test_apply_asset_cap():
    w = {"A": 0.5, "B": 0.3, "C": 0.2}
    capped = _apply_asset_cap(w, 0.30)
    assert all(v <= 0.30 + 1e-9 for v in capped.values())
