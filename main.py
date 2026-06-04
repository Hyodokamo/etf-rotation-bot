"""ETF Rotation Bot — Phase 2.3~2.5 entry point.

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
    outputs/YYYY-MM/audit_result.json         (when AI audit runs)
    outputs/YYYY-MM/ai_audit_evaluation.md    (when AI audit runs)
    outputs/YYYY-MM/run_log.json
"""

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

from src.ai_audit_evaluation import build_evaluation_markdown, build_quality_checks, save_evaluation
from src.allocation import compute_allocation, resolve_max_assets, trim_to_max_assets
from src.asset_role import filter_ranking_scores
from src.audit_context_builder import build_audit_context
from src.committee.runner import load_committee_config, run_committee, save_committee_result
from src.committee.decision_logger import (
    append_committee_decision_log,
    build_committee_log_entry,
)
from src.committee.review_comparison import compare_latest_committee_runs
from src.committee.advisory import build_advisory
from src.backtest import build_backtest_markdown, compare_backtest
from src.config_loader import load_config
from src.data_fetcher import fetch_prices
from src.data_quality import check_and_clean
from src.decision_logger import (
    ReviewDecision,
    create_decision_log,
    load_latest_run_context,
    save_decision_log,
    update_run_log_with_decision,
    validate_decision,
)
from src.indicators import compute_indicators
from src.llm.factory import create_client
from src.llm_auditor import run_audit, save_audit_result
from src.logger import logger
from src.portfolio_state import PortfolioState, load_state, save_state
from src.pre_trade_gate import run_pre_trade_gate, save_pre_trade_gate_result
from src.report_builder import build_report, save_report
from src.risk_gate import apply_risk_gate, evaluate_risk_gate
from src.risk_mode_check import check_risk_mode_consistency
from src.scoring import compute_scores
from src.slack_client import build_slack_summary, post_to_slack
from src.strategy_runner import compare_variants, save_comparison_report
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
    parser.add_argument(
        "--compare-strategy-variants",
        action="store_true",
        default=False,
        help="Compare allocation across strategy variants and generate comparison report",
    )
    parser.add_argument(
        "--backtest-months",
        type=int,
        default=12,
        help="Number of months for simplified backtest (default: 12)",
    )
    parser.add_argument(
        "--record-decision",
        default=None,
        metavar="DECISION",
        help=(
            "Record a review decision without re-running the pipeline. "
            "Valid values: REVIEW_CONFIRMED, SKIP_THIS_MONTH, REQUEST_RERUN, MANUAL_OVERRIDE"
        ),
    )
    parser.add_argument(
        "--decision-comment",
        default="",
        help="Comment to attach to the review decision (required for MANUAL_OVERRIDE and FAIL gate)",
    )
    parser.add_argument(
        "--decided-by",
        default="manual",
        help="Who made the decision (default: manual)",
    )
    parser.add_argument(
        "--committee",
        action="store_true",
        default=None,
        help="Force-enable the Investment Committee (shadow mode). Overrides config/committee.yaml enabled.",
    )
    parser.add_argument(
        "--committee-mode",
        default=None,
        help="Committee mode. Phase 3.1 supports only 'shadow' (no allocation override).",
    )
    parser.add_argument(
        "--record-committee-decision",
        action="store_true",
        default=False,
        help="Also record a human decision (--human-decision/--human-note) into the committee log.",
    )
    parser.add_argument(
        "--human-decision",
        default=None,
        choices=["HOLD", "BUY", "ADD", "TRIM", "EXIT", "WAIT", "SKIP"],
        help="Human decision to record alongside the committee log (requires --record-committee-decision).",
    )
    parser.add_argument(
        "--human-note",
        default=None,
        help="Free-text note for the human committee decision.",
    )
    parser.add_argument(
        "--no-committee-comparison",
        action="store_true",
        default=False,
        help="Disable the Committee Review Comparison (前回比) section even if 2+ log entries exist.",
    )
    parser.add_argument(
        "--no-committee-advisory",
        action="store_true",
        default=False,
        help="Disable the Committee Advisory (助言) section.",
    )
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


def _save_run_log(
    output_dir: str,
    run_date: date,
    weights: dict,
    audit_result,
    elapsed_ok: bool,
    turnover_info: dict | None = None,
    quality_checks: dict | None = None,
    evaluation_path: str | None = None,
    pre_trade_gate=None,
    pre_trade_gate_file: str | None = None,
    strategy_variant: str | None = None,
    committee_result=None,
) -> None:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    invalidated = audit_result.adjustments_invalidated if audit_result else False
    warnings = list(audit_result.validation_warnings) if audit_result else []
    log: dict = {
        "run_date": run_date.isoformat(),
        "strategy_variant": strategy_variant,
        "final_allocation": {t: round(w, 4) for t, w in weights.items()},
        "ai_audit_status": audit_result.status.value if audit_result else None,
        "ai_audit_valid": audit_result is not None,
        "ai_adjustment_applied": False,
        "ai_adjustment_invalidated": invalidated,
        "ai_adjustment_invalid_reason": "adjustment delta exceeds +-5%" if invalidated else None,
        "ai_audit_validation_warnings": warnings,
        "success": elapsed_ok,
    }
    if turnover_info:
        log.update(turnover_info)
    if quality_checks:
        log["ai_audit_quality_checks"] = quality_checks
    if evaluation_path:
        log["ai_audit_evaluation_file"] = evaluation_path
    if pre_trade_gate is not None:
        log["pre_trade_gate_status"] = pre_trade_gate.overall_status
        log["pre_trade_gate_failures"] = [
            c.check_id for c in pre_trade_gate.checks
            if c.status in ("FAIL", "REVIEW_REQUIRED") and c.severity != "INFO"
        ]
    if pre_trade_gate_file:
        log["pre_trade_gate_file"] = pre_trade_gate_file
    if committee_result is not None:
        log["committee_shadow_mode"] = committee_result.shadow_mode
        log["committee_allocation_override"] = committee_result.allocation_override
        log["committee_final_verdict"] = committee_result.final_committee_verdict.value
        log["committee_core_verdict"] = committee_result.core_committee_verdict.value
        log["committee_satellite_verdict"] = committee_result.satellite_committee_verdict.value
    path = out / "run_log.json"
    path.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"Run log saved to {path}")


def _handle_record_decision(args: argparse.Namespace) -> None:
    """Record a review decision from CLI without re-running the pipeline."""
    run_date = date.fromisoformat(args.date) if args.date else date.today()
    run_date_str = run_date.isoformat()
    logger.info(f"=== Recording review decision: {args.record_decision} (run_date={run_date_str}) ===")

    cfg = load_config(args.config)
    review_cfg = cfg.slack_review_decision

    try:
        decision = ReviewDecision(args.record_decision)
    except ValueError:
        valid = [d.value for d in ReviewDecision]
        print(f"Error: '{args.record_decision}' は無効な判断種別です。有効な値: {valid}", flush=True)
        sys.exit(1)

    # Load context from most recent run
    try:
        ctx = load_latest_run_context(run_date_str, cfg.report.output_dir)
    except FileNotFoundError as e:
        print(f"Error: {e}", flush=True)
        sys.exit(1)

    run_log = ctx["run_log"]
    gate_raw = ctx.get("pre_trade_gate") or {}
    gate_status = run_log.get("pre_trade_gate_status") or gate_raw.get("overall_status")
    gate_failures = run_log.get("pre_trade_gate_failures") or [
        c["check_id"] for c in gate_raw.get("checks", [])
        if c.get("status") in ("FAIL", "REVIEW_REQUIRED") and c.get("severity") != "INFO"
    ]
    ai_audit_status = run_log.get("ai_audit_status") or ctx.get("ai_audit_status")
    final_allocation = run_log.get("final_allocation", {})
    strategy_variant = run_log.get("strategy_variant")

    try:
        validate_decision(
            decision=decision,
            comment=args.decision_comment,
            pre_trade_gate_status=gate_status,
            allow_manual_override=review_cfg.allow_manual_override,
            require_comment_on_manual_override=review_cfg.require_comment_on_manual_override,
            require_comment_on_fail_gate=review_cfg.require_comment_on_fail_gate,
        )
    except ValueError as e:
        print(f"Error: {e}", flush=True)
        sys.exit(1)

    log = create_decision_log(
        run_date=run_date_str,
        decision=decision,
        comment=args.decision_comment,
        decided_by=args.decided_by,
        strategy_variant=strategy_variant,
        pre_trade_gate_status=gate_status,
        pre_trade_gate_failures=gate_failures,
        ai_audit_status=ai_audit_status,
        final_allocation=final_allocation,
    )

    output_dir = Path(cfg.report.output_dir) / run_date.strftime("%Y-%m")
    json_path, md_path = save_decision_log(log, str(output_dir))
    update_run_log_with_decision(ctx["run_log_path"], log, json_path)

    print(f"\n判断ログを保存しました:")
    print(f"  JSON: {json_path}")
    print(f"  Markdown: {md_path}")
    print(f"  判断: {log.decision.value} ({log.to_dict()['decision_label']})")
    logger.info("=== Review decision recorded successfully ===")


def main() -> None:
    load_dotenv()
    args = parse_args()

    # Phase 3: record-decision mode — no pipeline re-run
    if args.record_decision:
        _handle_record_decision(args)
        return

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

    # Phase 2.8: Strategy variant comparison mode
    if args.compare_strategy_variants:
        state_path = Path(args.portfolio_state) if args.portfolio_state else None
        prev_state = load_state(state_path) if state_path else load_state()
        prev_weights_cmp = prev_state.weights if prev_state else None
        audit_output_dir_cmp = Path(cfg.report.output_dir) / run_date.strftime("%Y-%m")
        audit_output_dir_cmp.mkdir(parents=True, exist_ok=True)

        logger.info("=== Strategy Variant Comparison Mode ===")
        cmp_result = compare_variants(
            prices=prices,
            cfg=cfg,
            ticker_to_category=ticker_to_category,
            prev_weights=prev_weights_cmp,
            run_date=run_date,
        )
        md_path, json_path = save_comparison_report(cmp_result, str(audit_output_dir_cmp), run_date)

        # Append backtest results to comparison markdown
        logger.info(f"Running simplified backtest ({args.backtest_months} months)...")
        bt_results = compare_backtest(prices, cfg, ticker_to_category, n_months=args.backtest_months)
        bt_md = build_backtest_markdown(bt_results)
        with open(md_path, "a", encoding="utf-8") as f:
            f.write("\n" + bt_md)

        print(f"\nComparison report: {md_path}")
        print(f"Comparison JSON:   {json_path}")
        print("\nVariant summary:")
        for v in cmp_result.variants:
            print(f"  [{v.variant_name}] gate={v.pre_trade_gate.overall_status} "
                  f"defensive={v.defensive_weight:.1%} "
                  f"tickers={list(v.weights.keys())}")
        if bt_results:
            print("\nBacktest summary:")
            for bt in bt_results:
                print(f"  [{bt.variant_name}] annRet={bt.annual_return:.1%} "
                      f"DD={bt.max_drawdown:.1%} SGOV={bt.sgov_adoption_rate:.0%} "
                      f"gate_fails={bt.pre_trade_gate_fail_count}/{bt.n_months}")
        logger.info("=== Comparison Mode completed ===")
        return

    # Phase 2.7: Apply strategy variant filter to ranking scores
    variant_name = cfg.strategy_variant.name
    ranking_scores, excluded_from_ranking = filter_ranking_scores(scores, cfg, variant_name)
    if excluded_from_ranking:
        logger.info(f"Strategy variant [{variant_name}]: excluded from ranking: {excluded_from_ranking}")

    raw_weights = compute_allocation(
        scores=ranking_scores,
        indicators=indicators.loc[indicators.index.intersection(ranking_scores.index)],
        ticker_to_category=ticker_to_category,
        alloc_cfg=cfg.allocation,
        risk_cfg=cfg.risk,
    )

    weights = apply_risk_gate(raw_weights, ticker_to_category, risk_gate, cfg.risk.risk_off_equity_cap)

    state_path = Path(args.portfolio_state) if args.portfolio_state else None
    prev_state = load_state(state_path) if state_path else load_state()
    prev_weights = prev_state.weights if prev_state else None
    turnover: float | None = None
    proposed_turnover: float | None = None

    if prev_weights:
        proposed_turnover = compute_turnover(weights, prev_weights)
        logger.info(f"Proposed turnover: {proposed_turnover:.1%}")
        effective_limit = cfg.turnover.effective_limit
        logger.info(
            f"Turnover mode: {cfg.turnover.mode_label}, limit: {effective_limit:.0%}"
        )
        # Zero out excluded tickers in prev_weights so they cannot re-enter via blending.
        # Excluded assets should be treated as already-at-zero for turnover smoothing.
        excluded_set = set(excluded_from_ranking)
        prev_weights_for_limit = {t: w for t, w in prev_weights.items() if t not in excluded_set}
        weights = apply_turnover_limit(weights, prev_weights_for_limit, effective_limit)
        turnover = compute_turnover(weights, prev_weights)

    # Enforce max portfolio assets — applied as the absolute last quant step
    n_eligible = int((ranking_scores > 0).sum())
    max_assets = resolve_max_assets(n_eligible, cfg.global_settings.max_portfolio_assets)
    final_weights = trim_to_max_assets(weights, max_assets)
    logger.info(
        f"Final allocation: {len(final_weights)} assets "
        f"(max={max_assets}, n_eligible={n_eligible}, "
        f"max_portfolio_assets={cfg.global_settings.max_portfolio_assets})"
    )

    # Phase 2.4: Risk-ON defensive weight check
    risk_mode_check_result = check_risk_mode_consistency(
        weights=final_weights,
        ticker_to_category=ticker_to_category,
        risk_off=risk_gate.risk_off,
        cfg=cfg.risk_mode_checks,
    )

    audit_output_dir = Path(cfg.report.output_dir) / run_date.strftime("%Y-%m")

    # Phase 2.6: Deterministic pre-trade constraint gate
    pre_trade_gate_result = run_pre_trade_gate(
        weights=final_weights,
        ticker_to_category=ticker_to_category,
        cfg=cfg,
        turnover=turnover,
        risk_mode_check=risk_mode_check_result,
    )
    pre_trade_gate_file = save_pre_trade_gate_result(pre_trade_gate_result, str(audit_output_dir))
    logger.info(f"Pre-trade gate: {pre_trade_gate_result.overall_status}")

    # Turnover info for run_log
    turnover_info = {
        "turnover_mode": cfg.turnover.mode_label,
        "turnover_limit_used": cfg.turnover.effective_limit,
        "normal_turnover_limit": cfg.turnover.normal_limit,
        "migration_turnover_limit": cfg.turnover.migration_limit,
        "proposed_turnover": round(proposed_turnover, 4) if proposed_turnover is not None else None,
        "actual_turnover": round(turnover, 4) if turnover is not None else None,
    }

    # --- AI Audit (Phase 2) ---
    audit_result = None
    llm_client = None
    audit_context: dict | None = None
    ai_enabled = _resolve_ai_audit_enabled(args, cfg.ai_audit.enabled)
    provider = cfg.ai_audit.provider
    model = cfg.ai_audit.model

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
        audit_context = build_audit_context(
            cfg=cfg,
            weights=final_weights,
            scores=scores,
            indicators=indicators,
            risk_gate=risk_gate,
            prev_weights=prev_weights,
            turnover=turnover,
            run_date=run_date,
            risk_mode_check=risk_mode_check_result,
            pre_trade_gate=pre_trade_gate_result,
        )
        try:
            llm_client = create_client(provider=provider, model=model)
            audit_result = run_audit(context=audit_context, weights=final_weights, client=llm_client)
        except (ValueError, NotImplementedError) as e:
            logger.error(f"AI audit skipped: {e}")
            llm_client = None
            audit_result = None
        if audit_result is not None:
            save_audit_result(audit_result, str(audit_output_dir))
        else:
            logger.warning("AI audit returned None — Phase 1 results preserved")
    else:
        logger.info("AI audit disabled")

    # --- Investment Committee (Phase 3.1, shadow mode) ---
    # Runs only when an LLM client is available (i.e. AI audit enabled). The
    # committee NEVER changes final_weights — it is display-only advisory output.
    committee_result = None
    committee_comparison = None
    committee_advisory = None
    committee_cfg = load_committee_config()
    # CLI overrides (Step 5): --committee force-enables; --committee-mode selects mode.
    if args.committee:
        committee_cfg.enabled = True
    if args.committee_mode is not None:
        if args.committee_mode != "shadow":
            logger.warning(
                f"--committee-mode='{args.committee_mode}' は未対応。Phase 3.1 は shadow のみ — shadow を強制します。"
            )
        committee_cfg.shadow_mode = True
    if args.human_decision and not args.record_committee_decision:
        logger.warning(
            "--human-decision を反映するには --record-committee-decision が必要です。今回は人間判断を記録しません。"
        )
    if committee_cfg.enabled and llm_client is not None:
        try:
            if audit_context is None:
                audit_context = build_audit_context(
                    cfg=cfg,
                    weights=final_weights,
                    scores=scores,
                    indicators=indicators,
                    risk_gate=risk_gate,
                    prev_weights=prev_weights,
                    turnover=turnover,
                    run_date=run_date,
                    risk_mode_check=risk_mode_check_result,
                    pre_trade_gate=pre_trade_gate_result,
                )
            committee_result = run_committee(
                audit_context,
                committee_cfg,
                llm_client,
                ai_audit_status=audit_result.status.value if audit_result else None,
            )
            save_committee_result(committee_result, str(audit_output_dir))

            # Phase 3.2: append the committee judgment to the append-only decision
            # log. The AI committee log is written on every committee run; human
            # decision fields are filled only when --record-committee-decision is set.
            try:
                record_human = args.record_committee_decision
                log_entry = build_committee_log_entry(
                    committee_result=committee_result,
                    run_date=run_date.isoformat(),
                    strategy_variant=variant_name,
                    risk_mode="risk_off" if risk_gate.risk_off else "risk_on",
                    final_allocation=final_weights,
                    ai_audit_status=audit_result.status.value if audit_result else None,
                    human_decision=args.human_decision if record_human else None,
                    human_note=args.human_note if record_human else None,
                )
                append_committee_decision_log(log_entry)
            except Exception as e:
                logger.warning(f"Committee decision log skipped due to error: {type(e).__name__}: {e}")

            # Phase 3.3: deterministic comparison vs the previous logged run.
            # Display-only; never affects allocation. Skipped if <2 valid entries.
            if not args.no_committee_comparison:
                try:
                    committee_comparison = compare_latest_committee_runs()
                    if committee_comparison is not None:
                        logger.info(
                            f"Committee Review Comparison: severity={committee_comparison.severity}"
                        )
                except Exception as e:
                    logger.warning(f"Committee Review Comparison skipped: {type(e).__name__}: {e}")
                    committee_comparison = None

            # Phase 3.4: deterministic advisory from the structured judgment.
            # Display-only; never changes allocation, never sizes orders.
            if not args.no_committee_advisory:
                try:
                    committee_advisory = build_advisory(
                        committee_result,
                        comparison=committee_comparison,
                        final_allocation=final_weights,
                        risk_mode="risk_off" if risk_gate.risk_off else "risk_on",
                        ai_audit_status=audit_result.status.value if audit_result else None,
                    )
                    logger.info(
                        f"Committee Advisory: stance={committee_advisory.overall_stance.value} "
                        f"action_items={len(committee_advisory.action_items)} (override=False)"
                    )
                except Exception as e:
                    logger.warning(f"Committee Advisory skipped: {type(e).__name__}: {e}")
                    committee_advisory = None
        except Exception as e:  # never break the pipeline on committee failure
            logger.warning(f"Investment Committee skipped due to error: {type(e).__name__}: {e}")
            committee_result = None
    elif committee_cfg.enabled:
        logger.info("Investment Committee enabled but no LLM client available — skipped (shadow).")

    # Phase 2.5: AI audit quality evaluation
    quality_checks = build_quality_checks(audit_result)
    evaluation_path: str | None = None
    if ai_enabled:
        eval_md = build_evaluation_markdown(
            run_date=run_date,
            provider=provider,
            model=model,
            audit_result=audit_result,
            quality_checks=quality_checks,
        )
        evaluation_path = save_evaluation(eval_md, str(audit_output_dir), run_date)

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
        proposed_turnover=proposed_turnover,
        risk_mode_check=risk_mode_check_result,
        pre_trade_gate=pre_trade_gate_result,
        strategy_variant=variant_name,
        committee_result=committee_result,
        committee_comparison=committee_comparison,
        committee_advisory=committee_advisory,
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
        proposed_turnover=proposed_turnover,
        turnover_cfg=cfg.turnover,
        risk_mode_check=risk_mode_check_result,
        pre_trade_gate=pre_trade_gate_result,
        strategy_variant=variant_name,
        slack_review_cfg=cfg.slack_review_decision,
        committee_result=committee_result,
        committee_comparison=committee_comparison,
        committee_advisory=committee_advisory,
    )
    post_to_slack(slack_msg)

    _save_run_log(
        str(audit_output_dir),
        run_date,
        final_weights,
        audit_result,
        elapsed_ok=True,
        turnover_info=turnover_info,
        quality_checks=quality_checks,
        evaluation_path=evaluation_path,
        pre_trade_gate=pre_trade_gate_result,
        pre_trade_gate_file=pre_trade_gate_file,
        strategy_variant=variant_name,
        committee_result=committee_result,
    )

    logger.info("=== ETF Rotation Bot completed successfully ===")
    print(f"\nReport: {report_path}")
    print("Top allocations:")
    for ticker, w in sorted(final_weights.items(), key=lambda x: -x[1])[:10]:
        asset = cfg.get_asset_by_ticker(ticker)
        name = asset.display_name if asset else ticker
        print(f"  {name} ({ticker}): {w:.1%}")


if __name__ == "__main__":
    main()
