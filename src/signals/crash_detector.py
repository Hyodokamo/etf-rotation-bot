"""Phase 5.1: crash/correction trigger detection.

Reads market_data_latest.csv and returns a list of trigger label strings
that fired. Purely deterministic — no LLM calls.
"""
from __future__ import annotations

import csv
from pathlib import Path

from src.logger import logger

DEFAULT_MARKET_DATA_PATH = "data/market_data_latest.csv"

MARKET_DATA_COLUMNS = [
    "symbol", "as_of_date", "last_price", "daily_return_pct",
    "return_5d_pct", "return_20d_pct", "drawdown_from_52w_high_pct",
    "above_200dma", "rsi_14", "volume_ratio", "vix_level",
    "us10y_yield", "dxy_level", "notes",
]


def load_market_data(
    path: str | Path = DEFAULT_MARKET_DATA_PATH,
) -> dict[str, dict]:
    """Load market_data_latest.csv as {SYMBOL.upper(): row_dict}. Missing → {}."""
    p = Path(path)
    if not p.exists():
        logger.info(f"Market data not found; crash detection skipped: {p}")
        return {}
    try:
        with open(p, encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
    except OSError as e:
        logger.warning(f"Market data read failed: {e}")
        return {}
    return {
        r.get("symbol", "").upper(): r
        for r in rows
        if r.get("symbol", "").strip()
    }


def _num(row: dict, key: str, default: float | None = None) -> float | None:
    v = row.get(key, "")
    if v is None or str(v).strip() == "":
        return default
    try:
        return float(str(v).replace(",", "").replace("%", "").strip())
    except (TypeError, ValueError):
        return default


def _vix_from_data(market_data: dict[str, dict]) -> float | None:
    vix_row = market_data.get("VIX", {})
    if vix_row:
        v = _num(vix_row, "last_price") or _num(vix_row, "vix_level")
        if v is not None:
            return v
    for sym in ("SPY", "QQQ", "QQQM"):
        v = _num(market_data.get(sym, {}), "vix_level")
        if v is not None:
            return v
    return None


def detect_crash_triggers(
    market_data: dict[str, dict],
    config,
    symbol: str | None = None,
) -> list[str]:
    """Return list of trigger labels that fired based on market data.

    Checks:
    - SPY / QQQ daily drop and drawdown
    - VIX stress / panic
    - Semiconductor (SOXX / SMH) drop and drawdown
    - Symbol-specific drop and drawdown (if ``symbol`` is supplied)
    """
    from src.signals.signal_config import SignalConfig
    cfg: SignalConfig = config
    mt = cfg.market_triggers
    tt = cfg.theme_triggers
    triggers: list[str] = []

    spy = market_data.get("SPY", {})
    qqq = market_data.get("QQQ", {}) or market_data.get("QQQM", {})

    spy_drop = _num(spy, "daily_return_pct")
    if spy_drop is not None and spy_drop <= mt.spy_daily_drop_pct:
        triggers.append(f"SPY急落({spy_drop:.1f}%)")

    qqq_drop = _num(qqq, "daily_return_pct")
    if qqq_drop is not None and qqq_drop <= mt.qqq_daily_drop_pct:
        triggers.append(f"QQQ急落({qqq_drop:.1f}%)")

    qqq_dd = _num(qqq, "drawdown_from_52w_high_pct")
    if qqq_dd is not None:
        if qqq_dd <= mt.qqq_severe_drawdown_from_high_pct:
            triggers.append(f"QQQ深刻下落({qqq_dd:.1f}%)")
        elif qqq_dd <= mt.qqq_drawdown_from_high_pct:
            triggers.append(f"QQQ高値比下落({qqq_dd:.1f}%)")

    vix_val = _vix_from_data(market_data)
    if vix_val is not None:
        if vix_val >= mt.vix_panic_level:
            triggers.append(f"VIXパニック({vix_val:.0f})")
        elif vix_val >= mt.vix_stress_level:
            triggers.append(f"VIXストレス({vix_val:.0f})")

    for name in ("SOXX", "SMH"):
        row = market_data.get(name, {})
        if not row:
            continue
        d = _num(row, "daily_return_pct")
        if d is not None and d <= tt.semiconductor_daily_drop_pct:
            triggers.append(f"{name}急落({d:.1f}%)")
        dd = _num(row, "drawdown_from_52w_high_pct")
        if dd is not None and dd <= tt.semiconductor_drawdown_from_high_pct:
            triggers.append(f"{name}高値比下落({dd:.1f}%)")

    if symbol and symbol.upper() not in ("SOXX", "SMH"):
        sym_row = market_data.get(symbol.upper(), {})
        if sym_row:
            d = _num(sym_row, "daily_return_pct")
            if d is not None and d <= tt.theme_daily_drop_pct:
                triggers.append(f"{symbol.upper()}急落({d:.1f}%)")
            dd = _num(sym_row, "drawdown_from_52w_high_pct")
            if dd is not None and dd <= tt.theme_drawdown_from_high_pct:
                triggers.append(f"{symbol.upper()}高値比下落({dd:.1f}%)")

    # Deduplicate while preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for t in triggers:
        if t not in seen:
            seen.add(t)
            unique.append(t)
    return unique
