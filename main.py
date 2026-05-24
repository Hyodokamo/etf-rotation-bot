"""ETF Rotation Bot — Phase 2.1 entry point.

Usage:
    python main.py [--config config.yaml] [--date YYYY-MM-DD]
    python main.py --ai-audit                        # enable AI audit override
    python main.py --no-ai-audit                     # disable AI audit override
    python main.py --ai-audit-provider claude|openai  # override LLM provider
    python main.py --ai-audit-model MODEL            # override audit model
    python main.py --portfolio-state PATH            # override state file path

Outputs:
    outputs/report_YYYY-MM-DD.md
    outputs/portfolio_state.json
    outputs/bot.log
    outputs/YYYY-MM/audit_result.json  (when AI audit runs)
    outputs/YYYY-MM/run_log.json
"""

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

from src.allocation import compute_allocation
from src.audit_context_builder import build_audit_context
from src.config_loader import load_config
from src.data_fetcher import fetch_prices
from src.data_quality import check_and_clean
from src.indicators import compute_indicators
from src.llm.factory import create_client
from src.llm_auditor import run_audit, save_audit_result
from src.logger import logger
from src.portfolio_state import PortfolioState, load_state, save_state
from src.report_builder import build_report, save_report
from src.risk_gate import apply_risk_gate, evaluate_risk_gate
from src.scoring import compute_scores
from src.slack_client import build_slack_summary, post_to_slack
from src.turnover import apply_turnover_limit, compute_turnover


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="ETF Rotation Bot Phase 2")
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml")
    parser.add_argument("--date", default=None, help="Run date override (YYYY-MM-DD)")
    parser.add_argument("--ai-audit", action="store_true", default=None, help="Enable AI audit")
    parser.add_argument("--no-ai-audit", action="store_true", default=None, help="Disable AI audit")
    parser.add_argument("--ai-audit-provider", default=None, help="Override LLM provider (claude|openai|gemini)")
    parser.add_argument("--ai-audit-model", default=None, help="Override AI audit model")
    parser.add_argument("--portfolio-state", default=None, help="Override portfolio state file path")
    return parser.parse_args()


def _resolve_ai_audit_enabled(args: argparse.Namespace, cfg_enabled: bool) -> bool:
    if args.no_ai_audit:
        return False
    if args.ai_audit:
        return True
    env_val = os.environ.get("AI_AUDIT_ENABLED", "").strip().lower()
    if env_val in ("1", "true", "yes"):
        return True
    if env_val in ("0", "false", "no"):
        return False
    return cfg_enabled


def _save_run_log(output_dir: str, run_date: date, weights: dict, audit_result, elapsed_ok: bool) -> None:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    log = {
        "run_date": run_date.isoformat(),
        "final_allocation": {t: round(w, 4) for t, w in weights.items()},
        "ai_audit_status": audit_result.status.value if audit_result else None,
        "apply_adjustment": False,
        "success": elapsed_ok,
    }
    path = out / "run_log.json"
    path.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"Run log saved to {path}")


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

    state_path = Path(args.portfolio_state) if args.portfolio_state else None
    prev_state = load_state(state_path) if state_path else load_state()
    prev_weights = prev_state.weights if prev_state else None
    turnover: float | None = None

    if prev_weights:
        turnover = compute_turnover(weights, prev_weights)
        logger.info(f"Proposed turnover: {turnover:.1%}")
        weights = apply_turnover_limit(weights, prev_weights, cfg.turnover.max_turnover)
        turnover = compute_turnover(weights, prev_weights)

    # final_allocation = quant_recommendation (always, Phase 2)
    final_weights = weights

    # --- AI Audit (Phase 2) ---
    audit_result = None
    ai_enabled = _resolve_ai_audit_enabled(args, cfg.ai_audit.enabled)
    audit_output_dir = Path(cfg.report.output_dir) / run_date.strftime("%Y-%m")

    if ai_enabled:
        provider = (
            args.ai_audit_provider
            or os.environ.get("AI_AUDIT_PROVIDER", "").strip()
            or cfg.ai_audit.provider
        )
        model = (
            args.ai_audit_model
            or os.environ.get("AI_AUDIT_MODEL", "").strip()
            or cfg.ai_audit.model
        )
        logger.info(f"AI audit enabled (provider={provider}, model={model})")
        context = build_audit_context(
            cfg=cfg,
            weights=final_weights,
            scores=scores,
            indicators=indicators,
            risk_gate=risk_gate,
            prev_weights=prev_weights,
            turnover=turnover,
            run_date=run_date,
        )
        try:
            llm_client = create_client(provider=provider, model=model)
            audit_result = run_audit(context=context, weights=final_weights, client=llm_client)
        except (ValueError, NotImplementedError) as e:
            logger.error(f"AI audit skipped: {e}")
            audit_result = None
        if audit_result is not None:
            save_audit_result(audit_result, str(audit_output_dir))
        else:
            logger.warning("AI audit returned None — Phase 1 results preserved")
    else:
        logger.info("AI audit disabled")

    report_text = build_report(
        cfg=cfg,
        weights=final_weights,
        scores=scores,
        indicators=indicators,
        prices=prices,
        risk_gate=risk_gate,
        prev_weights=prev_weights,
        turnover=turnover,
        run_date=run_date,
        audit_result=audit_result,
    )

    report_path = save_report(report_text, cfg.report.output_dir, run_date=run_date)

    new_state = PortfolioState(date=run_date.isoformat(), weights=final_weights)
    save_state(new_state, state_path) if state_path else save_state(new_state)

    slack_msg = build_slack_summary(
        weights=final_weights,
        risk_off=risk_gate.risk_off,
        turnover=turnover,
        report_path=str(report_path),
        audit_result=audit_result,
    )
    post_to_slack(slack_msg)

    _save_run_log(str(audit_output_dir), run_date, final_weights, audit_result, elapsed_ok=True)

    logger.info("=== ETF Rotation Bot completed successfully ===")
    print(f"\nReport: {report_path}")
    print("Top allocations:")
    for ticker, w in sorted(final_weights.items(), key=lambda x: -x[1])[:10]:
        asset = cfg.get_asset_by_ticker(ticker)
        name = asset.display_name if asset else ticker
        print(f"  {name} ({ticker}): {w:.1%}")


if __name__ == "__main__":
    main()
