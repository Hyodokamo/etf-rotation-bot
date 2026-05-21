from datetime import date, timedelta

import pandas as pd
import yfinance as yf

from src.logger import logger


def fetch_prices(tickers: list[str], years: int = 3) -> pd.DataFrame:
    """Fetch adjusted close prices for given tickers via yfinance.

    Returns a DataFrame indexed by date with tickers as columns.
    Columns with no data are dropped with a warning.
    """
    end_date = date.today()
    start_date = end_date - timedelta(days=int(years * 365.25))

    logger.info(f"Fetching prices for {len(tickers)} tickers from {start_date} to {end_date}")

    try:
        raw = yf.download(
            tickers=tickers,
            start=start_date.isoformat(),
            end=end_date.isoformat(),
            auto_adjust=True,
            progress=False,
            group_by="ticker",
        )
    except Exception as e:
        logger.error(f"yfinance download failed: {e}")
        raise

    if raw.empty:
        raise ValueError("yfinance returned empty DataFrame")

    prices = _extract_close(raw, tickers)
    _log_coverage(prices)
    return prices


def _extract_close(raw: pd.DataFrame, tickers: list[str]) -> pd.DataFrame:
    if isinstance(raw.columns, pd.MultiIndex):
        close_frames: dict[str, pd.Series] = {}
        for ticker in tickers:
            try:
                series = raw[ticker]["Close"].dropna()
                if not series.empty:
                    close_frames[ticker] = series
                else:
                    logger.warning(f"No data returned for ticker: {ticker}")
            except KeyError:
                logger.warning(f"Ticker not found in response: {ticker}")
        return pd.DataFrame(close_frames)
    else:
        # Single ticker case
        return raw[["Close"]].rename(columns={"Close": tickers[0]})


def _log_coverage(prices: pd.DataFrame) -> None:
    for ticker in prices.columns:
        n = prices[ticker].count()
        first = prices[ticker].first_valid_index()
        last = prices[ticker].last_valid_index()
        logger.debug(f"{ticker}: {n} rows, {first} – {last}")
