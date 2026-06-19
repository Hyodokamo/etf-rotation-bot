"""Tests for Phase 5.1: crash_detector.py."""
import pytest

from src.signals.crash_detector import detect_crash_triggers, load_market_data
from src.signals.signal_config import load_signal_config


def _cfg(tmp_path):
    return load_signal_config(tmp_path / "no_config.yaml")


def _market_data(rows: list[dict]) -> dict[str, dict]:
    return {r["symbol"].upper(): r for r in rows}


def test_crash_detector_no_triggers_with_empty_data(tmp_path):
    cfg = _cfg(tmp_path)
    triggers = detect_crash_triggers({}, cfg)
    assert triggers == []


def test_crash_detector_missing_file_returns_empty_market_data(tmp_path):
    data = load_market_data(tmp_path / "no_market_data.csv")
    assert data == {}


def test_crash_detector_spy_drop_triggers(tmp_path):
    cfg = _cfg(tmp_path)
    data = _market_data([{"symbol": "SPY", "daily_return_pct": "-2.5", "drawdown_from_52w_high_pct": "-5.0"}])
    triggers = detect_crash_triggers(data, cfg)
    assert any("SPY急落" in t for t in triggers)


def test_crash_detector_qqq_drawdown_triggers(tmp_path):
    cfg = _cfg(tmp_path)
    data = _market_data([{"symbol": "QQQ", "daily_return_pct": "-1.0", "drawdown_from_52w_high_pct": "-10.0"}])
    triggers = detect_crash_triggers(data, cfg)
    assert any("QQQ高値比下落" in t for t in triggers)


def test_crash_detector_qqq_severe_drawdown_triggers(tmp_path):
    cfg = _cfg(tmp_path)
    data = _market_data([{"symbol": "QQQ", "daily_return_pct": "-2.0", "drawdown_from_52w_high_pct": "-16.0"}])
    triggers = detect_crash_triggers(data, cfg)
    assert any("QQQ深刻下落" in t for t in triggers)


def test_crash_detector_vix_stress_triggers(tmp_path):
    cfg = _cfg(tmp_path)
    data = _market_data([{"symbol": "SPY", "daily_return_pct": "-1.0", "vix_level": "28.0"}])
    triggers = detect_crash_triggers(data, cfg)
    assert any("VIXストレス" in t for t in triggers)


def test_crash_detector_vix_panic_triggers(tmp_path):
    cfg = _cfg(tmp_path)
    data = _market_data([{"symbol": "SPY", "daily_return_pct": "0.0", "vix_level": "38.0"}])
    triggers = detect_crash_triggers(data, cfg)
    assert any("VIXパニック" in t for t in triggers)


def test_crash_detector_symbol_specific_trigger(tmp_path):
    cfg = _cfg(tmp_path)
    data = _market_data([
        {"symbol": "GRID", "daily_return_pct": "-5.0", "drawdown_from_52w_high_pct": "-12.0"},
    ])
    triggers = detect_crash_triggers(data, cfg, symbol="GRID")
    assert any("GRID" in t for t in triggers)


def test_crash_detector_no_symbol_trigger_without_flag(tmp_path):
    cfg = _cfg(tmp_path)
    data = _market_data([
        {"symbol": "GRID", "daily_return_pct": "-5.0", "drawdown_from_52w_high_pct": "-12.0"},
    ])
    # Without symbol= kwarg, GRID-specific trigger should not fire
    triggers = detect_crash_triggers(data, cfg)
    assert not any("GRID急落" in t for t in triggers)
