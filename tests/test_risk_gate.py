import numpy as np
import pandas as pd
import pytest

from src.config_loader import RiskConfig
from src.risk_gate import RiskGateResult, apply_risk_gate, evaluate_risk_gate, EQUITY_CATEGORIES


def make_prices(ticker: str, trend: str = "flat", n: int = 100) -> pd.DataFrame:
    idx = pd.date_range("2023-01-01", periods=n, freq="B")
    if trend == "down":
        prices = np.linspace(100, 80, n)
    elif trend == "up":
        prices = np.linspace(100, 120, n)
    else:
        prices = np.full(n, 100.0)
    return pd.DataFrame({ticker: prices}, index=idx)


@pytest.fixture
def cfg():
    return RiskConfig(
        risk_off_ticker="VOO",
        risk_off_window=60,
        risk_off_threshold=-0.07,
        risk_off_equity_cap=0.40,
    )


def test_risk_on_when_price_flat(cfg):
    prices = make_prices("VOO", trend="flat", n=100)
    result = evaluate_risk_gate(prices, cfg)
    assert not result.risk_off


def test_risk_off_when_large_drawdown(cfg):
    prices = make_prices("VOO", trend="down", n=100)
    result = evaluate_risk_gate(prices, cfg)
    assert result.risk_off


def test_missing_ticker_returns_risk_on(cfg):
    prices = make_prices("TLT", trend="down", n=100)
    result = evaluate_risk_gate(prices, cfg)
    assert not result.risk_off


def test_apply_risk_gate_no_change_when_risk_on():
    gate = RiskGateResult(risk_off=False, sp500_return=0.05, message="ok")
    weights = {"VOO": 0.5, "TLT": 0.3, "SGOV": 0.2}
    cat_map = {"VOO": "core_equity", "TLT": "bond", "SGOV": "cash_like"}
    result = apply_risk_gate(weights, cat_map, gate, equity_cap=0.40)
    assert result == weights


def test_apply_risk_gate_caps_equity():
    gate = RiskGateResult(risk_off=True, sp500_return=-0.10, message="risk off")
    weights = {"VOO": 0.70, "TLT": 0.20, "SGOV": 0.10}
    cat_map = {"VOO": "core_equity", "TLT": "bond", "SGOV": "cash_like"}
    result = apply_risk_gate(weights, cat_map, gate, equity_cap=0.40)
    equity = sum(w for t, w in result.items() if cat_map.get(t) in EQUITY_CATEGORIES)
    assert equity <= 0.40 + 1e-9


def test_risk_gate_sp500_return_stored(cfg):
    prices = make_prices("VOO", trend="up", n=100)
    result = evaluate_risk_gate(prices, cfg)
    assert result.sp500_return is not None
    assert result.sp500_return > 0


def test_apply_risk_gate_no_new_tickers():
    """Risk gate must not add tickers that weren't already in the portfolio."""
    gate = RiskGateResult(risk_off=True, sp500_return=-0.10, message="risk off")
    # Portfolio has no cash_like position
    weights = {"VOO": 0.70, "TLT": 0.30}
    # Universe has SGOV (cash_like) but it is NOT in portfolio
    cat_map = {"VOO": "core_equity", "TLT": "bond", "SGOV": "cash_like"}
    result = apply_risk_gate(weights, cat_map, gate, equity_cap=0.40)
    # SGOV must NOT appear in the result
    assert "SGOV" not in result
    assert set(result.keys()) == {"VOO", "TLT"}


def test_apply_risk_gate_equity_cap_no_cash_portfolio():
    """Equity cap is respected even when no cash ticker is in the portfolio."""
    gate = RiskGateResult(risk_off=True, sp500_return=-0.10, message="risk off")
    weights = {"VOO": 0.70, "TLT": 0.30}
    cat_map = {"VOO": "core_equity", "TLT": "bond", "SGOV": "cash_like"}
    result = apply_risk_gate(weights, cat_map, gate, equity_cap=0.40)
    equity = sum(w for t, w in result.items() if cat_map.get(t) in EQUITY_CATEGORIES)
    assert equity <= 0.40 + 1e-9
    assert abs(sum(result.values()) - 1.0) < 1e-9


def test_apply_risk_gate_redistributes_to_existing_cash():
    """Excess equity weight goes to cash tickers already in the portfolio."""
    gate = RiskGateResult(risk_off=True, sp500_return=-0.10, message="risk off")
    weights = {"VOO": 0.70, "TLT": 0.20, "SGOV": 0.10}
    cat_map = {"VOO": "core_equity", "TLT": "bond", "SGOV": "cash_like"}
    result = apply_risk_gate(weights, cat_map, gate, equity_cap=0.40)
    # SGOV weight must have increased (received the excess)
    assert result["SGOV"] > weights["SGOV"]
    assert abs(sum(result.values()) - 1.0) < 1e-9
