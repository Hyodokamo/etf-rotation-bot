"""ETF Rotation Bot — Phase 1 entry point.

Usage:
    python main.py [--config config.yaml] [--date YYYY-MM-DD]

Outputs:
    outputs/report_YYYY-MM-DD.md
    outputs/portfolio_state.json
    outputs/bot.log
"""

import argparse
import sys
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

from src.allocation import compute_allocation
from src.config_loader import load_config
from src.data_fetcher import fetch_prices
from src.data_quality import check_and_clean
from src.indicators import compute_indicators
from src.logger import logger
from src.portfolio_state import PortfolioState, load_state, save_state
from src.report_builder import build_report, save_report
from src.risk_gate import apply_risk_gate, evaluate_risk_gate
from src.scoring import compute_scores
from src.slack_client import build_slack_summary, post_to_slack
from src.turnover import apply_turnover_limit, compute_turnover


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="ETF Rotation Bot Phase 1")
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml")
    parser.add_argument("--date", default=None, help="Run date override (YYYY-MM-DD)")
    return parser.parse_args()


def main() -> None:
    load_dotenv()
    args = parse_args()

    run_date = date.fromisoformat(args.date) if args.date else date.today()
    logger.info(f"=== ETF Rotation Bot starting (run_date={run_date}) ===")

    cfg = load_config(args.config)
    assets = cfg.production_assets()
    tickers = [a.ticker for a in assets]
    ticker_to_category = {a.ticker: a.category for a in assets}

    logger.info(f"Universe: {len(tickers)} tickers")

    prices_raw = fetch_prices(tickers, years=cfg.data.fetch_years)
    prices, dq_report = check_and_clean(prices_raw, min_history_days=cfg.data.min_history_days)

    if dq_report.dropped_tickers:
        for t in dq_report.dropped_tickers:
            ticker_to_category.pop(t, None)

    active_tickers = prices.columns.tolist()

    indicators = compute_indicators(prices, cfg.scoring)
    if indicators.empty:
        logger.error("No indicators computed — aborting")
        sys.exit(1)

    scores = compute_scores(indicators, cfg.scoring)

    risk_gate = evaluate_risk_gate(prices, cfg.risk)

    raw_weights = compute_allocation(
        scores=scores,
        indicators=indicators,
        ticker_to_category=ticker_to_category,
        alloc_cfg=cfg.allocation,
        risk_cfg=cfg.risk,
    )

    weights = apply_risk_gate(raw_weights, ticker_to_category, risk_gate, cfg.risk.risk_off_equity_cap)

    prev_state = load_state()
    prev_weights = prev_state.weights if prev_state else None
    turnover: float | None = None

    if prev_weights:
        turnover = compute_turnover(weights, prev_weights)
        logger.info(f"Proposed turnover: {turnover:.1%}")
        weights = apply_turnover_limit(weights, prev_weights, cfg.turnover.max_turnover)
        turnover = compute_turnover(weights, prev_weights)

    report_text = build_report(
        cfg=cfg,
        weights=weights,
        scores=scores,
        indicators=indicators,
        prices=prices,
        risk_gate=risk_gate,
        prev_weights=prev_weights,
        turnover=turnover,
        run_date=run_date,
    )

    report_path = save_report(report_text, cfg.report.output_dir, run_date=run_date)

    new_state = PortfolioState(date=run_date.isoformat(), weights=weights)
    save_state(new_state)

    slack_msg = build_slack_summary(
        weights=weights,
        risk_off=risk_gate.risk_off,
        turnover=turnover,
        report_path=str(report_path),
    )
    post_to_slack(slack_msg)

    logger.info("=== ETF Rotation Bot completed successfully ===")
    print(f"\nReport: {report_path}")
    print(f"Top allocations:")
    for ticker, w in sorted(weights.items(), key=lambda x: -x[1])[:10]:
        asset = cfg.get_asset_by_ticker(ticker)
        name = asset.display_name if asset else ticker
        print(f"  {name} ({ticker}): {w:.1%}")


if __name__ == "__main__":
    main()
