"""Phase 5.3 / 5.3.1: Market Data Fetcher with symbol role separation.

Fetches ETF/index/macro market data and writes data/market_data_latest.csv
for use by the Crash Signal Layer.

Symbol roles (from config/market_data_config.yaml):
  market_reference  — used for market_regime/stress calculation; never buy-candidate
  candidate         — active buy-candidate universe (AI検証枠対象)
  benchmark         — reference only; not a buy-candidate
  macro_indicator   — macro data; not a buy-candidate

Safety invariants (always enforced):
- No order quantity calculation
- No auto-trade execution
- No brokerage / securities account integration
- watchlist.csv never modified
- signal_history.csv never modified
- ai_sleeve_state.csv never modified
- etf_master.csv never modified
- core_manual assets: no sell proposals
- SELL side reserved; not implemented here
"""
from __future__ import annotations

import csv
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from src.logger import logger

DEFAULT_MARKET_DATA_OUTPUT_PATH = "data/market_data_latest.csv"
DEFAULT_CONFIG_PATH = "config/market_data_config.yaml"

MARKET_DATA_COLUMNS = [
    "symbol", "as_of_date", "last_price", "daily_return_pct",
    "return_5d_pct", "return_20d_pct", "drawdown_from_52w_high_pct",
    "above_200dma", "rsi_14", "volume_ratio",
    "vix_level", "us10y_yield", "dxy_level", "notes", "role",
]

# Roles that indicate a symbol is for reference/context only — not a buy-candidate
MARKET_REFERENCE_ROLES = frozenset({"market_reference", "benchmark", "macro_indicator"})

DEFAULT_ETF_SYMBOLS = [
    "SPY", "QQQ", "SOXX", "SMH", "GRID", "BOTZ",
    "ITA", "XLU", "PAVE", "AVUV", "CIBR",
]

_VIX_SYMBOL = "^VIX"
_YIELD_SYMBOL = "^TNX"
_DXY_SYMBOL = "DX-Y.NYB"


def load_market_data_config(path: str = DEFAULT_CONFIG_PATH) -> dict:
    """Load market_data_config.yaml. Returns empty dict if file not found."""
    p = Path(path)
    if not p.exists():
        logger.warning(f"Market data config not found at {p}; using defaults")
        return {}
    with open(p, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    return raw.get("market_data", {})


def get_all_symbols(cfg: dict) -> list[str]:
    """Get all symbols to fetch (regardless of role).

    Supports both the new `symbols` list format and the legacy `etf_symbols` list.
    """
    if "symbols" in cfg:
        return [
            (s["symbol"] if isinstance(s, dict) else s)
            for s in cfg["symbols"]
        ]
    return cfg.get("etf_symbols", list(DEFAULT_ETF_SYMBOLS))


def get_symbol_roles(cfg: dict) -> dict[str, str]:
    """Return {SYMBOL_UPPER: role_str} mapping from config.

    Returns {} if the config has no `symbols` section (legacy format).
    Unknown symbols default to "candidate" when looked up.
    """
    if "symbols" not in cfg:
        return {}
    return {
        (s["symbol"].upper() if isinstance(s, dict) else s.upper()):
        (s.get("role", "candidate") if isinstance(s, dict) else "candidate")
        for s in cfg["symbols"]
    }


def get_candidate_symbols(cfg: dict) -> list[str]:
    """Get symbols with role=candidate only.

    Falls back to all symbols if the config has no `symbols` section (legacy format).
    """
    if "symbols" not in cfg:
        return list(cfg.get("etf_symbols", DEFAULT_ETF_SYMBOLS))
    return [
        (s["symbol"] if isinstance(s, dict) else s)
        for s in cfg["symbols"]
        if not isinstance(s, dict) or s.get("role", "candidate") == "candidate"
    ]


def is_market_reference(symbol: str, roles: dict[str, str]) -> bool:
    """True if symbol has a market_reference, benchmark, or macro_indicator role.

    Unknown symbols (not in roles dict) default to False (treated as candidate).
    """
    return roles.get(symbol.upper(), "candidate") in MARKET_REFERENCE_ROLES


# ── Pure calculation functions (no yfinance dependency) ───────────────────────


def calculate_returns(closes: list[float]) -> dict[str, float | None]:
    """Compute daily, 5-day, and 20-day percentage returns.

    Args:
        closes: Close prices in chronological order (most recent last).

    Returns:
        {"daily": ..., "return_5d": ..., "return_20d": ...}  (None if insufficient data)
    """
    n = len(closes)
    if n < 2:
        return {"daily": None, "return_5d": None, "return_20d": None}

    current = closes[-1]

    def _pct(prior: float) -> float | None:
        if prior == 0:
            return None
        return round((current - prior) / prior * 100, 4)

    return {
        "daily": _pct(closes[-2]),
        "return_5d": _pct(closes[-6]) if n >= 6 else None,
        "return_20d": _pct(closes[-21]) if n >= 21 else None,
    }


def calculate_drawdown_from_52w_high(
    closes: list[float],
    highs: list[float] | None = None,
) -> float | None:
    """Drawdown % from 52-week high (last 252 trading days).

    Uses daily high prices when provided; falls back to closes as proxy.
    Returns None if data is empty.
    """
    if not closes:
        return None
    current = closes[-1]
    series = highs if highs else closes
    window = series[-252:]
    if not window:
        return None
    peak = max(window)
    if peak == 0:
        return None
    return round((current - peak) / peak * 100, 2)


def calculate_above_200dma(closes: list[float]) -> bool | None:
    """True if current close > 200-day SMA. None if fewer than 200 data points."""
    if len(closes) < 200:
        return None
    sma200 = sum(closes[-200:]) / 200
    return closes[-1] > sma200


def calculate_rsi_14(closes: list[float], period: int = 14) -> float | None:
    """RSI(period) using simple average of last `period` up/down moves.

    Returns None if fewer than period+1 data points are available.
    Note: uses simple (non-Wilder-smoothed) average — adequate for advisory signals.
    """
    if len(closes) < period + 1:
        return None
    deltas = [
        closes[i] - closes[i - 1]
        for i in range(len(closes) - period, len(closes))
    ]
    gains = [max(d, 0.0) for d in deltas]
    losses = [max(-d, 0.0) for d in deltas]
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - 100 / (1 + rs), 1)


def calculate_volume_ratio(volumes: list[float], period: int = 20) -> float | None:
    """Current volume / prior 20-day average volume.

    Returns None if fewer than period+1 data points are available.
    """
    if len(volumes) < period + 1:
        return None
    current = volumes[-1]
    avg = sum(volumes[-(period + 1):-1]) / period
    if avg == 0:
        return None
    return round(current / avg, 2)


# ── Data fetch orchestration ──────────────────────────────────────────────────


def fetch_market_data(
    symbols: list[str] | None = None,
    *,
    as_of_date: str | None = None,
    config_path: str = DEFAULT_CONFIG_PATH,
    yf_module=None,
    _symbol_roles: dict[str, str] | None = None,
) -> list[dict]:
    """Fetch market data for ETF symbols and macro indicators.

    Args:
        symbols: Override list of symbols. If None, loads from config (or uses defaults).
        as_of_date: Date string (YYYY-MM-DD). Defaults to today.
        config_path: Path to market_data_config.yaml.
        yf_module: yfinance module override (injectable for testing; loads yfinance if None).
        _symbol_roles: Role mapping override for testing; loads from config if None.

    Returns:
        List of row dicts matching MARKET_DATA_COLUMNS schema (includes `role` field).

    Safety: Never writes watchlist / signal_history / ai_sleeve_state / etf_master.
    Never computes order quantity. Never calls brokerage APIs.
    """
    if yf_module is None:
        import yfinance as yf_module  # noqa: PLC0415

    cfg = load_market_data_config(config_path)

    if symbols is None:
        symbols = get_all_symbols(cfg)

    # Role lookup: injected for testing, else loaded from config
    roles = _symbol_roles if _symbol_roles is not None else get_symbol_roles(cfg)

    today = as_of_date or date.today().isoformat()
    macro_cfg = cfg.get("macro", {})
    vix_sym = macro_cfg.get("vix_symbol", _VIX_SYMBOL)
    yield_sym = macro_cfg.get("yield_10y_symbol", _YIELD_SYMBOL)
    dxy_sym = macro_cfg.get("dxy_symbol", _DXY_SYMBOL)
    history_period = cfg.get("history_period", "1y")

    macro = _fetch_macro(yf_module, vix_sym=vix_sym, yield_sym=yield_sym, dxy_sym=dxy_sym)

    rows: list[dict] = []
    for symbol in symbols:
        role = roles.get(symbol.upper(), "candidate")
        row = _fetch_one(symbol, today, macro, yf_module, period=history_period, role=role)
        rows.append(row)

    logger.info(f"Market data fetched: {len(rows)} symbols as of {today}")
    return rows


def _fetch_macro(yf_module, *, vix_sym: str, yield_sym: str, dxy_sym: str) -> dict:
    """Fetch VIX, 10-year yield, and DXY. Failures silently degrade to None."""
    macro: dict[str, float | None] = {"vix": None, "us10y_yield": None, "dxy": None}

    def _last_close(sym: str) -> float | None:
        try:
            hist = yf_module.Ticker(sym).history(period="2d")
            if hist is None or len(hist) == 0:
                return None
            return float(hist["Close"].iloc[-1])
        except Exception as exc:
            logger.warning(f"Macro fetch failed for {sym}: {exc}")
            return None

    vix = _last_close(vix_sym)
    if vix is not None:
        macro["vix"] = round(vix, 2)

    raw_yield = _last_close(yield_sym)
    if raw_yield is not None:
        # ^TNX is already in percentage (e.g. 4.65 = 4.65%)
        macro["us10y_yield"] = round(raw_yield, 3)

    dxy = _last_close(dxy_sym)
    if dxy is not None:
        macro["dxy"] = round(dxy, 2)

    return macro


def _fetch_one(
    symbol: str,
    today: str,
    macro: dict,
    yf_module,
    period: str = "1y",
    role: str = "candidate",
) -> dict:
    """Fetch OHLCV history and compute all metrics for one symbol."""
    row: dict[str, Any] = {col: "" for col in MARKET_DATA_COLUMNS}
    row["symbol"] = symbol
    row["as_of_date"] = today
    row["vix_level"] = _str(macro.get("vix"))
    row["us10y_yield"] = _str(macro.get("us10y_yield"))
    row["dxy_level"] = _str(macro.get("dxy"))
    row["role"] = role

    try:
        hist = yf_module.Ticker(symbol).history(period=period)
        if hist is None or len(hist) < 2:
            row["notes"] = f"error: insufficient history for {symbol}"
            logger.warning(f"Insufficient history for {symbol}")
            return row

        closes = list(hist["Close"].astype(float))
        highs = list(hist["High"].astype(float))
        volumes = list(hist["Volume"].astype(float))

        row["last_price"] = _str(closes[-1], 4)

        ret = calculate_returns(closes)
        row["daily_return_pct"] = _str(ret["daily"])
        row["return_5d_pct"] = _str(ret["return_5d"])
        row["return_20d_pct"] = _str(ret["return_20d"])

        dd = calculate_drawdown_from_52w_high(closes, highs)
        row["drawdown_from_52w_high_pct"] = _str(dd)

        above = calculate_above_200dma(closes)
        row["above_200dma"] = "" if above is None else str(above).lower()

        rsi = calculate_rsi_14(closes)
        row["rsi_14"] = _str(rsi)

        vol_ratio = calculate_volume_ratio(volumes)
        row["volume_ratio"] = _str(vol_ratio)

        row["notes"] = ""

    except Exception as exc:
        logger.warning(f"Fetch failed for {symbol}: {exc}")
        row["notes"] = f"error: {str(exc)[:80]}"

    return row


def _str(val: float | None, decimals: int = 2) -> str:
    if val is None:
        return ""
    return str(round(val, decimals))


def write_market_data_latest(
    rows: list[dict],
    path: str | Path = DEFAULT_MARKET_DATA_OUTPUT_PATH,
    *,
    dry_run: bool = False,
) -> str | None:
    """Write market data rows to CSV.

    Safety: Never modifies watchlist / signal_history / ai_sleeve_state / etf_master.
    Never computes order quantity. No brokerage integration.

    Returns the output path string, or None if dry_run.
    """
    if dry_run:
        logger.info(f"Market data write skipped (dry_run): {len(rows)} rows")
        return None
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=MARKET_DATA_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    logger.info(f"Market data written: {len(rows)} rows → {p}")
    return str(p)
