import pandas as pd
import pytest

from src.allocation import compute_allocation, _normalize, _apply_asset_cap, trim_to_max_assets, resolve_max_assets
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


# --- trim_to_max_assets ---

def test_trim_reduces_to_max():
    w = {"A": 0.40, "B": 0.30, "C": 0.20, "D": 0.10}
    result = trim_to_max_assets(w, max_assets=2)
    assert len(result) == 2
    assert set(result) == {"A", "B"}


def test_trim_normalizes_to_one():
    w = {"A": 0.40, "B": 0.30, "C": 0.20, "D": 0.10}
    result = trim_to_max_assets(w, max_assets=3)
    assert abs(sum(result.values()) - 1.0) < 1e-9


def test_trim_no_op_when_within_limit():
    w = {"A": 0.6, "B": 0.4}
    result = trim_to_max_assets(w, max_assets=4)
    assert result == w


def test_trim_keeps_top_by_weight():
    w = {"LOW": 0.05, "HIGH": 0.60, "MID": 0.35}
    result = trim_to_max_assets(w, max_assets=2)
    assert "HIGH" in result
    assert "MID" in result
    assert "LOW" not in result


def test_trim_single_asset():
    w = {"A": 0.5, "B": 0.3, "C": 0.2}
    result = trim_to_max_assets(w, max_assets=1)
    assert len(result) == 1
    assert abs(sum(result.values()) - 1.0) < 1e-9


# --- resolve_max_assets ---

def test_resolve_zero_eligible():
    assert resolve_max_assets(0, 4) == 1


def test_resolve_few_eligible():
    assert resolve_max_assets(1, 4) == 1
    assert resolve_max_assets(2, 4) == 2
    assert resolve_max_assets(3, 4) == 3


def test_resolve_medium_eligible():
    assert resolve_max_assets(4, 4) == 3
    assert resolve_max_assets(7, 4) == 3


def test_resolve_large_eligible():
    assert resolve_max_assets(8, 4) == 4
    assert resolve_max_assets(20, 4) == 4


def test_resolve_respects_max_portfolio_assets():
    # If max_portfolio_assets is 2, medium range caps at min(3, 2) = 2
    assert resolve_max_assets(5, 2) == 2
    assert resolve_max_assets(10, 2) == 2


def test_full_pipeline_within_max_assets(alloc_cfg, risk_cfg):
    """compute_allocation + trim_to_max_assets enforces the final asset cap."""
    scores = make_scores(15)
    ind = make_indicators(15)
    cat_map = {f"T{i}": "core_equity" for i in range(15)}
    alloc_cfg_wide = AllocationConfig(top_n=15, min_assets=3)
    weights = compute_allocation(scores, ind, cat_map, alloc_cfg_wide, risk_cfg)
    trimmed = trim_to_max_assets(weights, max_assets=4)
    assert len(trimmed) <= 4
    assert abs(sum(trimmed.values()) - 1.0) < 1e-9
