"""Tests for Phase 2.7: Asset role filtering."""
import pandas as pd
import pytest
from unittest.mock import MagicMock

from src.asset_role import filter_ranking_scores, get_fallback_tickers, get_ranking_tickers
from src.config_loader import AppConfig, AssetConfig, UniverseConfig


# ── helpers ──────────────────────────────────────────────────────────────────


def _asset(ticker: str, role: str = "standard", ranking: bool = True, fallback: bool = False) -> AssetConfig:
    return AssetConfig(
        asset_id=ticker.lower(),
        ticker=ticker,
        display_name=ticker,
        category="unknown",
        include_stage="production",
        role=role,
        include_in_momentum_ranking=ranking,
        include_in_fallback=fallback,
    )


def _make_cfg(assets: list[AssetConfig], variant: str = "baseline_current") -> AppConfig:
    raw = {
        "universe": {"assets": [a.model_dump() for a in assets]},
        "strategy_variant": {"name": variant},
    }
    return AppConfig.model_validate(raw)


def _scores(**kwargs) -> pd.Series:
    return pd.Series(kwargs)


# ── baseline_current: no filtering ───────────────────────────────────────────


def test_baseline_current_returns_all_scores():
    assets = [_asset("VOO"), _asset("SGOV", role="cash_fallback", ranking=False, fallback=True)]
    cfg = _make_cfg(assets, variant="baseline_current")
    scores = _scores(VOO=10.0, SGOV=8.0)
    filtered, excluded = filter_ranking_scores(scores, cfg)
    assert "SGOV" in filtered.index
    assert "VOO" in filtered.index
    assert excluded == []


def test_baseline_current_excludes_nothing():
    assets = [_asset("SGOV", role="cash_fallback", ranking=False, fallback=True)]
    cfg = _make_cfg(assets, variant="baseline_current")
    scores = _scores(SGOV=9.0)
    filtered, excluded = filter_ranking_scores(scores, cfg, variant_name="baseline_current")
    assert excluded == []


# ── cash_fallback_separated: SGOV excluded ──────────────────────────────────


def test_sgov_excluded_from_ranking_in_cash_fallback_separated():
    assets = [
        _asset("VOO"),
        _asset("SGOV", role="cash_fallback", ranking=False, fallback=True),
    ]
    cfg = _make_cfg(assets, variant="cash_fallback_separated")
    scores = _scores(VOO=10.0, SGOV=8.0)
    filtered, excluded = filter_ranking_scores(scores, cfg)
    assert "SGOV" not in filtered.index
    assert "SGOV" in excluded


def test_uup_excluded_from_ranking_in_cash_fallback_separated():
    assets = [
        _asset("VOO"),
        _asset("UUP", role="fx_defensive", ranking=False, fallback=False),
    ]
    cfg = _make_cfg(assets, variant="cash_fallback_separated")
    scores = _scores(VOO=10.0, UUP=5.0)
    filtered, excluded = filter_ranking_scores(scores, cfg)
    assert "UUP" not in filtered.index
    assert "UUP" in excluded


def test_standard_assets_remain_in_ranking():
    assets = [
        _asset("VOO"),
        _asset("BND", role="bond_defensive"),
        _asset("SGOV", role="cash_fallback", ranking=False, fallback=True),
    ]
    cfg = _make_cfg(assets, variant="cash_fallback_separated")
    scores = _scores(VOO=10.0, BND=6.0, SGOV=8.0)
    filtered, excluded = filter_ranking_scores(scores, cfg)
    assert "VOO" in filtered.index
    assert "BND" in filtered.index
    assert "SGOV" not in filtered.index


# ── fallback injection when eligible assets are critically low ───────────────


def test_sgov_reinjected_as_fallback_when_no_eligible_assets():
    """When all ranking tickers have non-positive scores, inject fallback."""
    assets = [
        _asset("VOO"),
        _asset("VTV"),
        _asset("SGOV", role="cash_fallback", ranking=False, fallback=True),
    ]
    cfg = _make_cfg(assets, variant="cash_fallback_separated")
    # VOO and VTV have non-positive scores → eligible = 0 → SGOV re-injected
    scores = _scores(VOO=-1.0, VTV=-0.5, SGOV=8.0)
    filtered, excluded = filter_ranking_scores(scores, cfg)
    assert "SGOV" in filtered.index


def test_uup_not_reinjected_when_not_fallback():
    assets = [
        _asset("VOO"),
        _asset("UUP", role="fx_defensive", ranking=False, fallback=False),
    ]
    cfg = _make_cfg(assets, variant="cash_fallback_separated")
    scores = _scores(VOO=1.0, UUP=9.0)
    filtered, _ = filter_ranking_scores(scores, cfg)
    # UUP should NOT be injected (include_in_fallback=False)
    assert "UUP" not in filtered.index


# ── get_ranking_tickers / get_fallback_tickers ────────────────────────────────


def test_get_ranking_tickers_baseline_returns_all():
    assets = [_asset("VOO"), _asset("SGOV", ranking=False, fallback=True)]
    cfg = _make_cfg(assets, variant="baseline_current")
    tickers = get_ranking_tickers(cfg)
    assert "VOO" in tickers
    assert "SGOV" in tickers


def test_get_ranking_tickers_variant_excludes_non_ranking():
    assets = [_asset("VOO"), _asset("SGOV", ranking=False, fallback=True)]
    cfg = _make_cfg(assets, variant="cash_fallback_separated")
    tickers = get_ranking_tickers(cfg)
    assert "VOO" in tickers
    assert "SGOV" not in tickers


def test_get_fallback_tickers_returns_include_in_fallback():
    assets = [
        _asset("VOO"),
        _asset("SGOV", role="cash_fallback", ranking=False, fallback=True),
        _asset("UUP", role="fx_defensive", ranking=False, fallback=False),
    ]
    cfg = _make_cfg(assets, variant="cash_fallback_separated")
    fallback = get_fallback_tickers(cfg)
    assert "SGOV" in fallback
    assert "UUP" not in fallback
    assert "VOO" not in fallback


# ── config.yaml asset roles load correctly ───────────────────────────────────


def test_config_yaml_sgov_role():
    from src.config_loader import load_config
    cfg = load_config("config.yaml")
    sgov = cfg.get_asset_by_ticker("SGOV")
    assert sgov is not None
    assert sgov.role == "cash_fallback"
    assert sgov.include_in_momentum_ranking is False
    assert sgov.include_in_fallback is True


def test_config_yaml_uup_role():
    from src.config_loader import load_config
    cfg = load_config("config.yaml")
    uup = cfg.get_asset_by_ticker("UUP")
    assert uup is not None
    assert uup.role == "fx_defensive"
    assert uup.include_in_momentum_ranking is False


def test_config_yaml_bnd_role():
    from src.config_loader import load_config
    cfg = load_config("config.yaml")
    bnd = cfg.get_asset_by_ticker("BND")
    assert bnd is not None
    assert bnd.role == "bond_defensive"
    assert bnd.include_in_momentum_ranking is True


def test_config_yaml_default_variant_is_cash_fallback_separated():
    from src.config_loader import load_config
    cfg = load_config("config.yaml")
    assert cfg.strategy_variant.name == "cash_fallback_separated"


# ── final_allocation invariants ───────────────────────────────────────────────


def test_max_4_assets_with_cash_fallback_separated():
    """cash_fallback_separated must still respect max_portfolio_assets=4."""
    from src.allocation import compute_allocation, trim_to_max_assets, resolve_max_assets
    from src.config_loader import load_config

    cfg = load_config("config.yaml")
    # Build minimal scores for non-SGOV, non-UUP tickers
    ranking_tickers = get_ranking_tickers(cfg, variant_name="cash_fallback_separated")
    scores = pd.Series({t: float(i + 1) for i, t in enumerate(ranking_tickers[:10])})

    weights = compute_allocation(
        scores=scores,
        indicators=pd.DataFrame({"vol_20d": [0.15] * len(scores)}, index=scores.index),
        ticker_to_category={t: "core_equity" for t in scores.index},
        alloc_cfg=cfg.allocation,
        risk_cfg=cfg.risk,
    )
    n_eligible = int((scores > 0).sum())
    max_assets = resolve_max_assets(n_eligible, cfg.global_settings.max_portfolio_assets)
    final = trim_to_max_assets(weights, max_assets)
    assert len(final) <= cfg.global_settings.max_portfolio_assets


def test_sgov_not_in_cash_fallback_separated_when_many_eligible():
    """With 20+ eligible assets, SGOV should NOT appear in final allocation."""
    from src.allocation import compute_allocation, trim_to_max_assets, resolve_max_assets
    from src.config_loader import load_config

    cfg = load_config("config.yaml")
    ranking_tickers = get_ranking_tickers(cfg, variant_name="cash_fallback_separated")
    scores = pd.Series({t: float(i + 1) for i, t in enumerate(ranking_tickers)})

    weights = compute_allocation(
        scores=scores,
        indicators=pd.DataFrame({"vol_20d": [0.15] * len(scores)}, index=scores.index),
        ticker_to_category={t: "core_equity" for t in scores.index},
        alloc_cfg=cfg.allocation,
        risk_cfg=cfg.risk,
    )
    n_eligible = int((scores > 0).sum())
    max_assets = resolve_max_assets(n_eligible, cfg.global_settings.max_portfolio_assets)
    final = trim_to_max_assets(weights, max_assets)
    assert "SGOV" not in final
