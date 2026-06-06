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
from src.committee.candidate_review import (
    build_candidate_markdown,
    build_candidate_slack_summary,
    fetch_candidate_trends,
    filter_candidates,
    load_watchlist,
    review_watchlist,
    save_candidate_report,
)
from src.committee.candidate_decision_logger import (
    append_candidate_decision_log,
    build_candidate_log_entry,
)
from src.committee.candidate_stability import (
    build_stability_markdown,
    build_stability_slack_summary,
    check_candidate_stability,
    save_stability_report,
)
from src.committee.slack_digest import build_executive_digest
from src.etf_master import DEFAULT_MASTER_PATH, load_etf_master
from src.portfolio_context import (
    DEFAULT_AI_SLEEVE_STATE_PATH,
    DEFAULT_POLICY_PATH,
    DEFAULT_SNAPSHOT_PATH,
    build_slack_context_line,
    load_portfolio_context,
)
from src.ai_sleeve_deployment_log import record_sleeve_deployment
from src.decision_audit import build_audit_markdown, build_decision_audit, save_audit_report
from src.slack_actions import build_action_value
from src.slack_publish import (
    bot_token_available,
    build_candidate_review_blocks,
    build_monthly_digest_blocks,
    post_committee_message,
)
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
    parser.add_argument(
        "--candidate-review",
        action="store_true",
        default=False,
        help="Run Candidate Review (new-buy candidates) instead of the monthly review.",
    )
    parser.add_argument(
        "--candidate-file",
        default="data/watchlist_candidates.csv",
        help="Path to the watchlist CSV for Candidate Review.",
    )
    parser.add_argument(
        "--candidate-symbol",
        default=None,
        help="Review only this single candidate symbol (e.g. GRID).",
    )
    parser.add_argument(
        "--candidate-review-slack",
        action="store_true",
        default=False,
        help="Post the Candidate Review summary to Slack.",
    )
    parser.add_argument(
        "--record-candidate-decision",
        action="store_true",
        default=False,
        help="Also record a human decision into the candidate review log.",
    )
    parser.add_argument(
        "--candidate-human-decision",
        default=None,
        choices=["WATCHLIST", "SMALL_TEST_BUY_CANDIDATE", "WAIT", "REJECT", "RE_REVIEW", "SKIP"],
        help="Human decision for the candidate (requires --record-candidate-decision).",
    )
    parser.add_argument(
        "--candidate-human-note",
        default=None,
        help="Free-text note for the candidate human decision.",
    )
    parser.add_argument(
        "--candidate-stability",
        action="store_true",
        default=False,
        help="Audit candidate verdict stability from the candidate review log.",
    )
    parser.add_argument(
        "--candidate-stability-slack",
        action="store_true",
        default=False,
        help="Post the Candidate Stability Check summary to Slack.",
    )
    parser.add_argument(
        "--audit-summary",
        action="store_true",
        default=False,
        help="Generate the integrated monthly decision audit summary (retrospective only).",
    )
    parser.add_argument(
        "--audit-month",
        default=None,
        help="Target month for the audit summary (YYYY-MM). Defaults to the run month.",
    )
    parser.add_argument(
        "--audit-output",
        default=None,
        help="Output path for the audit summary Markdown (default: reports/audit/decision_audit_YYYYMM.md).",
    )
    # Phase 5.0.7: AI sleeve deployment log (manual record; no order quantity)
    parser.add_argument(
        "--sleeve-record",
        action="store_true",
        default=False,
        help=(
            "Record one AI-sleeve deployment decision (append-only log). "
            "Advisory only — no brokerage call, no order quantity computed."
        ),
    )
    parser.add_argument(
        "--sleeve-action",
        default="deploy",
        choices=["deploy", "reduce", "note", "correct"],
        help="Deployment action: deploy (cash->invested) | reduce (invested->cash) | note | correct.",
    )
    parser.add_argument(
        "--sleeve-symbol",
        default="",
        help="ETF symbol recorded in the deployment log entry.",
    )
    parser.add_argument(
        "--sleeve-theme",
        default="",
        help="Theme for the deployment log entry.",
    )
    parser.add_argument(
        "--sleeve-amount",
        type=float,
        default=0.0,
        help="Consideration amount in JPY (NOT an order quantity; human-entered JPY intent).",
    )
    parser.add_argument(
        "--sleeve-account",
        default="taxable",
        help="Account for the deployment log entry (default: taxable).",
    )
    parser.add_argument(
        "--sleeve-notes",
        default="",
        help="Free-text notes for the deployment log entry.",
    )
    parser.add_argument(
        "--sleeve-month",
        default=None,
        help="YYYY-MM month for the deployment log file (default: run date month).",
    )
    # Phase 5.1: Crash Signal MVP — advisory watchlist candidate detection
    parser.add_argument(
        "--crash-signal-check",
        action="store_true",
        default=False,
        help=(
            "Run crash signal check (advisory only). "
            "No orders, no auto-trade, no brokerage integration."
        ),
    )
    parser.add_argument(
        "--signal-symbol",
        default=None,
        help="Check only this ETF symbol (e.g. GRID). Default: all active universe ETFs.",
    )
    parser.add_argument(
        "--signal-side",
        default="BUY",
        choices=["BUY", "SELL", "HOLD", "RISK_REVIEW"],
        help="Signal side (default: BUY). SELL is reserved in MVP and always returns NO_ACTION.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Dry-run: generate signals in memory only; do not write watchlist or history files.",
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


def _resolve_ai_provider_model(args: argparse.Namespace, cfg) -> tuple[str, str]:
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
    return provider, model


def load_candidate_enrichment(
    master_path: str = DEFAULT_MASTER_PATH,
    snapshot_path: str = DEFAULT_SNAPSHOT_PATH,
    policy_path: str = DEFAULT_POLICY_PATH,
    sleeve_state_path: str = DEFAULT_AI_SLEEVE_STATE_PATH,
):
    """Phase 5.0 wiring: load ETF master + read-only portfolio context.

    Both are best-effort and read-only:
    - ETF master missing -> warning + ``None`` (Candidate Review continues unchanged).
    - total_portfolio_snapshot.csv missing -> ``None`` portfolio_context (no sleeve scope).
      ai_sleeve_state.csv is optional; when absent the policy defaults (¥1,000,000 /
      cash ¥1,000,000 / invested ¥0) apply.

    Never changes final_allocation, never computes order quantity, never trades.
    """
    master = load_etf_master(master_path)
    if not master:
        logger.warning(
            f"ETF master not found at {master_path}; Candidate Review continues without enrichment."
        )
        master = None

    portfolio_ctx = None
    if Path(snapshot_path).exists():
        portfolio_ctx = load_portfolio_context(snapshot_path, policy_path, sleeve_state_path)
        logger.info(
            "Portfolio context loaded (read-only): "
            f"core={len(portfolio_ctx.core_manual)} / "
            f"ai_sleeve cash={int(portfolio_ctx.ai_sleeve.current_cash_jpy):,} "
            f"invested={int(portfolio_ctx.ai_sleeve.current_invested_jpy):,}"
        )
    else:
        logger.warning(
            f"{snapshot_path} not found; portfolio_context (read-only core / AI sleeve) skipped."
        )
    return master, portfolio_ctx


def _handle_candidate_review(args: argparse.Namespace) -> None:
    """Phase 3.5: review new-buy candidates with the committee (advisory-only).

    Separate path: does NOT run the monthly pipeline, does NOT touch
    portfolio_state / final_allocation.
    """
    run_date = date.fromisoformat(args.date) if args.date else date.today()
    logger.info(f"=== Candidate Review starting (run_date={run_date}) ===")

    cfg = load_config(args.config)
    committee_cfg = load_committee_config()
    committee_cfg.enabled = True
    committee_cfg.satellite_activation = "always"  # Satellite always runs for candidates

    try:
        candidates = load_watchlist(args.candidate_file)
    except (FileNotFoundError, ValueError) as e:
        print(f"Error: {e}", flush=True)
        sys.exit(1)

    candidates = filter_candidates(candidates, args.candidate_symbol)
    if not candidates:
        print(f"No matching candidates (symbol={args.candidate_symbol}).", flush=True)
        return

    # LLM client (reuse AI-audit provider/model). None -> INSUFFICIENT_DATA results.
    provider, model = _resolve_ai_provider_model(args, cfg)
    llm_client = None
    try:
        llm_client = create_client(provider=provider, model=model)
        logger.info(f"Candidate Review using provider={provider}, model={model}")
    except (ValueError, NotImplementedError) as e:
        logger.warning(f"Candidate Review: no LLM client ({e}) — results will be INSUFFICIENT_DATA.")

    prev_state = load_state()
    portfolio_holdings = prev_state.weights if prev_state else {}
    universe_categories = {a.ticker: a.category for a in cfg.universe.assets}

    # Phase 5.0 wiring: ETF master enrichment + read-only portfolio context.
    etf_master, portfolio_ctx = load_candidate_enrichment()

    # Best-effort price trends (network; failures degrade to None per symbol).
    price_trends = fetch_candidate_trends([c.symbol for c in candidates])

    results = review_watchlist(
        candidates,
        committee_cfg,
        llm_client,
        portfolio_holdings=portfolio_holdings,
        universe_categories=universe_categories,
        price_trends=price_trends,
        portfolio_context=portfolio_ctx,
        etf_master=etf_master,
        review_date=run_date.isoformat(),
    )

    markdown = build_candidate_markdown(results, run_date.isoformat(), portfolio_context=portfolio_ctx)
    report_path = save_candidate_report(markdown, run_date.isoformat())

    # Phase 3.6: append each candidate review to the append-only decision log.
    # AI review is always logged; human decision filled only with the flag.
    if args.candidate_human_decision and not args.record_candidate_decision:
        logger.warning(
            "--candidate-human-decision を反映するには --record-candidate-decision が必要です。"
            "今回は人間判断を記録しません。"
        )
    record_human = args.record_candidate_decision
    for r in results:
        try:
            entry = build_candidate_log_entry(
                r,
                human_decision=args.candidate_human_decision if record_human else None,
                human_note=args.candidate_human_note if record_human else None,
            )
            append_candidate_decision_log(entry)
        except Exception as e:
            logger.warning(f"Candidate decision log skipped for {r.candidate.get('symbol')}: {type(e).__name__}: {e}")

    if args.candidate_review_slack:
        channel = os.environ.get("SLACK_CHANNEL_ID", "").strip() or None
        if bot_token_available() and channel:
            # Phase 4.3: one message per candidate with verdict-derived button gating.
            # (Full stability gating is available via the Phase 3.7 stability check.)
            _verdict_to_handling = {
                "APPROVE_SMALL_TEST_BUY": "OK_FOR_WATCHLIST",   # full button set
                "REJECT_FOR_NOW": "DO_NOT_ACT_YET",             # hide buy-ish buttons
                "INSUFFICIENT_DATA": "HUMAN_REVIEW_REQUIRED",   # hide buy-ish buttons
            }
            for r in results:
                handling = _verdict_to_handling.get(r.candidate_verdict.value)
                summary = build_candidate_slack_summary(r)
                action_value = build_action_value(
                    source_type="candidate_review",
                    review_id=r.review_id,
                    candidate_symbol=r.candidate.get("symbol"),
                    recommended_handling=handling,
                    channel_id=channel,
                )
                blocks = build_candidate_review_blocks(
                    summary, action_value, recommended_handling=handling, interactive=True,
                )
                post_committee_message(summary, blocks, channel)
        else:
            slack_text = "\n\n".join(build_candidate_slack_summary(r) for r in results)
            header = ("\n".join(build_slack_context_line(portfolio_ctx)) + "\n\n") if portfolio_ctx else ""
            post_to_slack("*Candidate Review*\n\n" + header + slack_text)

    print(f"\nCandidate Review report: {report_path}")
    for r in results:
        print(f"  {r.candidate['symbol']}: {r.candidate_verdict.value} (conf {r.confidence:.0%})")
    logger.info("=== Candidate Review completed ===")


def _handle_candidate_stability(args: argparse.Namespace) -> None:
    """Phase 3.7: audit candidate verdict stability (quality audit, not approval).

    Reads the candidate review log only; does NOT run the monthly pipeline or
    touch portfolio_state / final_allocation.
    """
    run_date = date.fromisoformat(args.date) if args.date else date.today()
    logger.info(f"=== Candidate Stability Check (run_date={run_date}) ===")

    results = check_candidate_stability(symbol=args.candidate_symbol)
    if not results:
        print("No candidate history found in logs/candidate_review_log.jsonl.", flush=True)
        return

    markdown = build_stability_markdown(results, run_date.isoformat())
    report_path = save_stability_report(markdown, run_date.isoformat())

    if args.candidate_stability_slack:
        slack_text = "\n\n".join(build_stability_slack_summary(r) for r in results)
        post_to_slack("*Candidate Stability Check*\n\n" + slack_text)

    print(f"\nCandidate Stability report: {report_path}")
    for r in results:
        print(f"  {r.candidate_symbol}: {r.stability.value}/{r.severity.value} "
              f"-> {r.recommended_handling.value}")
    logger.info("=== Candidate Stability Check completed ===")


def _handle_audit_summary(args: argparse.Namespace) -> None:
    """Phase 5: integrated monthly decision audit (retrospective; reads logs only)."""
    run_date = date.fromisoformat(args.date) if args.date else date.today()
    month = args.audit_month or run_date.strftime("%Y-%m")
    logger.info(f"=== Decision Audit Summary (month={month}) ===")

    audit = build_decision_audit(month)
    # Phase 5.0.6: AI sleeve state (ai_sleeve_state.csv) — read-only; existing
    # BOTZ/GRID are NOT summed into the sleeve invested amount.
    _, portfolio_ctx = load_candidate_enrichment()
    markdown = build_audit_markdown(audit, portfolio_context=portfolio_ctx)
    report_path = save_audit_report(markdown, month, output_path=args.audit_output)

    print(f"\nDecision audit report: {report_path}")
    print(f"  {audit.conclusion}")
    logger.info("=== Decision Audit Summary completed ===")


def _handle_sleeve_record(args: argparse.Namespace) -> None:
    """Phase 5.0.7: record one AI-sleeve deployment decision (advisory only).

    Appends to ``data/ai_sleeve_state_YYYYMM.csv`` and updates
    ``data/ai_sleeve_state.csv`` with the resulting cash/invested balance.
    No brokerage call, no order quantity computed.
    """
    run_date = date.fromisoformat(args.date) if args.date else date.today()
    logger.info(f"=== Sleeve Deployment Record (run_date={run_date}) ===")

    entry = record_sleeve_deployment(
        as_of_date=run_date.isoformat(),
        action=args.sleeve_action,
        symbol=args.sleeve_symbol,
        theme=args.sleeve_theme,
        consideration_jpy=args.sleeve_amount,
        account=args.sleeve_account,
        notes=args.sleeve_notes,
        month=args.sleeve_month or run_date.strftime("%Y-%m"),
    )

    print(f"Sleeve record appended: {entry.action.value} {entry.symbol or '(no symbol)'}")
    print(f"  consideration_jpy: {entry.consideration_jpy:,.0f} (NOT an order quantity)")
    print(f"  resulting: cash={entry.resulting_cash_jpy:,.0f}  invested={entry.resulting_invested_jpy:,.0f}")
    logger.info("=== Sleeve Deployment Record completed ===")


def _handle_crash_signal_check(args: argparse.Namespace) -> None:
    """Phase 5.1: crash signal check (advisory only).

    Generates watchlist candidate signals for AI-sleeve ETFs.
    - No orders, no order quantities, no brokerage API calls.
    - SELL reserved in MVP: always returns NO_ACTION.
    - dry_run=True: no watchlist.csv / signal_history.csv writes.
    """
    from src.signals.crash_detector import DEFAULT_MARKET_DATA_PATH, detect_crash_triggers, load_market_data
    from src.signals.signal_committee_runner import run_signal_committee
    from src.signals.signal_config import load_signal_config
    from src.signals.signal_context_builder import build_signal_context
    from src.signals.signal_engine import aggregate_signal
    from src.signals.signal_models import SignalSide
    from src.signals.signal_report import build_signal_markdown, save_signal_report
    from src.signals.watchlist_store import (
        append_signal_history,
        load_watchlist,
        save_watchlist,
        update_watchlist_entry,
    )

    run_date = date.fromisoformat(args.date) if args.date else date.today()
    dry_run: bool = args.dry_run
    logger.info(
        f"=== Crash Signal Check starting (run_date={run_date}, dry_run={dry_run}) ==="
    )

    signal_cfg = load_signal_config("config/signal_config.yaml")
    market_data = load_market_data(DEFAULT_MARKET_DATA_PATH)

    triggers = detect_crash_triggers(market_data, signal_cfg)
    logger.info(f"Global crash triggers detected: {triggers or '(none)'}")

    # Determine which symbols to evaluate
    if args.signal_symbol:
        symbols = [args.signal_symbol.upper()]
    else:
        master = load_etf_master(DEFAULT_MASTER_PATH)
        if master:
            symbols = [
                sym for sym, e in master.items()
                if getattr(e, "include_in_active_universe", True)
            ]
        else:
            symbols = ["GRID", "ITA", "CIBR", "QQQM", "SOXX"]
        logger.info(f"Evaluating {len(symbols)} symbols from ETF master")

    signal_side = SignalSide(args.signal_side)

    # Load read-only portfolio context (best-effort)
    _, portfolio_ctx = load_candidate_enrichment()

    # Load committee config (reuse existing 7 members)
    committee_cfg = load_committee_config()
    committee_cfg.enabled = True
    committee_cfg.satellite_activation = "always"

    # LLM client: None → neutral fallbacks (no cost in dry-run / no API key)
    llm_client = None
    try:
        cfg = load_config(args.config)
        provider, model = _resolve_ai_provider_model(args, cfg)
        llm_client = create_client(provider=provider, model=model)
        logger.info(f"Signal committee using provider={provider}, model={model}")
    except Exception as e:
        logger.warning(f"Signal committee: no LLM client ({e}) — neutral fallbacks used.")

    # Load watchlist for in-memory update
    watchlist_path = "data/watchlist.csv"
    archive_dir = "data/archive"
    watchlist = load_watchlist(watchlist_path)

    results = []
    for symbol in symbols:
        try:
            ctx = build_signal_context(
                symbol, market_data, signal_cfg,
                portfolio_context=portfolio_ctx,
            )
            members = run_signal_committee(ctx, committee_cfg, client=llm_client)
            result = aggregate_signal(symbol, signal_side, members, ctx, signal_cfg, portfolio_ctx)
            watchlist = update_watchlist_entry(watchlist, result, dry_run=dry_run)
            append_signal_history(result, "logs/signal_history.csv", dry_run=dry_run)
            results.append(result)
            logger.info(
                f"  {symbol}: {result.final_signal.value} "
                f"(score={result.total_score:+d}, veto={result.veto_count})"
            )
        except Exception as e:
            logger.warning(f"Signal check failed for {symbol}: {type(e).__name__}: {e}")

    if not dry_run and results:
        save_watchlist(watchlist, watchlist_path, dry_run=False, archive_dir=archive_dir)

    # Determine market_regime for report
    market_regime = (
        results[0].member_outputs[0].rationale[:20]
        if results and results[0].member_outputs
        else "neutral"
    )
    # Use the signal context market_regime from the first result's trigger labels
    first_ctx_regime = "normal"
    if triggers:
        first_ctx_regime = "correction"
    if any("パニック" in t or "VIXパニック" in t for t in triggers):
        first_ctx_regime = "panic"
    elif any("急落" in t or "暴落" in t for t in triggers):
        first_ctx_regime = "crash"

    markdown = build_signal_markdown(results, market_regime=first_ctx_regime)

    if dry_run:
        print("\n[DRY-RUN] Signal Report (in-memory only; no files written)\n")
        sys.stdout.buffer.write((markdown[:3000] + "\n").encode("utf-8", errors="replace"))
        sys.stdout.buffer.flush()
    else:
        report_path = save_signal_report(markdown, out_dir="reports")
        print(f"\nSignal report: {report_path}")

    summary_lines = [
        f"\nCrash Signal Check: {len(results)} symbol(s) evaluated",
        f"Global triggers: {triggers or '(none)'}",
    ] + [
        f"  {r.symbol}: {r.final_signal.value} "
        f"(score={r.total_score:+d}, conf={r.confidence:.0%}, veto={r.veto_count})"
        for r in results
    ]
    sys.stdout.buffer.write(("\n".join(summary_lines) + "\n").encode("utf-8", errors="replace"))
    sys.stdout.buffer.flush()

    logger.info("=== Crash Signal Check completed ===")


def main() -> None:
    load_dotenv()
    args = parse_args()

    # Phase 5.1: crash signal check — advisory only, separate from monthly pipeline
    if args.crash_signal_check:
        _handle_crash_signal_check(args)
        return

    # Phase 5: integrated decision audit — separate, reads logs only
    if args.audit_summary:
        _handle_audit_summary(args)
        return

    # Phase 5.0.7: sleeve deployment record — manual, advisory only
    if args.sleeve_record:
        _handle_sleeve_record(args)
        return

    # Phase 3: record-decision mode — no pipeline re-run
    if args.record_decision:
        _handle_record_decision(args)
        return

    # Phase 3.5: candidate review mode — separate from the monthly pipeline
    if args.candidate_review:
        _handle_candidate_review(args)
        return

    # Phase 3.7: candidate stability check — reads the candidate log only
    if args.candidate_stability:
        _handle_candidate_stability(args)
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

    # Phase 3.6.1: when the committee ran, post the concise Executive Digest;
    # otherwise keep the existing summary (e.g. --no-ai-audit runs).
    if committee_result is not None:
        slack_msg = build_executive_digest(
            weights=final_weights,
            risk_off=risk_gate.risk_off,
            turnover=turnover,
            report_path=str(report_path),
            audit_result=audit_result,
            turnover_cfg=cfg.turnover,
            risk_mode_check=risk_mode_check_result,
            pre_trade_gate=pre_trade_gate_result,
            strategy_variant=variant_name,
            committee_result=committee_result,
            committee_advisory=committee_advisory,
            committee_comparison=committee_comparison,
            slack_review_cfg=cfg.slack_review_decision,
        )
    else:
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

    # Phase 4.3: attach Monthly Review action buttons (Bot Token) when available;
    # otherwise fall back to the Incoming Webhook (text only).
    if committee_result is not None and bot_token_available():
        channel = os.environ.get("SLACK_CHANNEL_ID", "").strip() or None
        action_value = build_action_value(
            source_type="monthly_review",
            run_id=run_date.isoformat(),
            channel_id=channel,
        )
        digest_blocks = build_monthly_digest_blocks(slack_msg, action_value, interactive=True)
        post_committee_message(slack_msg, digest_blocks, channel)
    else:
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
