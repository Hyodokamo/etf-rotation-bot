"""Phase 2.6: Deterministic pre-trade constraint gate.

Runs machine-verifiable checks against the final portfolio allocation.
Results are authoritative — the AI auditor may only explain or supplement,
not override these decisions.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from src.config_loader import AppConfig, RiskModeCheckConfig
from src.logger import logger


@dataclass
class GateCheckResult:
    check_id: str
    status: str   # PASS | FAIL | PASS_WITH_CAUTION | REVIEW_REQUIRED | N/A
    severity: str  # HIGH | MEDIUM | LOW | INFO
    message: str
    value: float | None = None
    limit: float | None = None
    affected_assets: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "check_id": self.check_id,
            "status": self.status,
            "severity": self.severity,
            "message": self.message,
            "value": self.value,
            "limit": self.limit,
            "affected_assets": self.affected_assets,
        }


@dataclass
class PreTradeGateResult:
    overall_status: str
    checks: list[GateCheckResult]
    enabled: bool = True

    def to_dict(self) -> dict:
        return {
            "overall_status": self.overall_status,
            "checks": [c.to_dict() for c in self.checks],
        }


# ── individual checks ────────────────────────────────────────────────────────


def _resolve_overall_status(checks: list[GateCheckResult]) -> str:
    """Return the worst status across all non-INFO checks; ignore N/A."""
    for status in ("FAIL", "REVIEW_REQUIRED", "PASS_WITH_CAUTION", "PASS"):
        if any(c.status == status and c.severity != "INFO" for c in checks):
            return status
    return "PASS"


def check_single_asset_limit(
    weights: dict[str, float],
    limit: float,
) -> GateCheckResult:
    failures = [(t, w) for t, w in weights.items() if w > limit + 1e-9]
    if not failures:
        max_w = max(weights.values()) if weights else 0.0
        return GateCheckResult(
            check_id="single_asset_limit_check",
            status="PASS",
            severity="HIGH",
            message=f"All assets within single asset limit {limit:.1%}.",
            value=round(max_w, 4),
            limit=round(limit, 4),
        )
    worst_ticker, worst_weight = max(failures, key=lambda x: x[1])
    return GateCheckResult(
        check_id="single_asset_limit_check",
        status="FAIL",
        severity="HIGH",
        message=(
            f"{worst_ticker} weight {worst_weight:.1%} exceeds single asset limit {limit:.1%}."
        ),
        value=round(worst_weight, 4),
        limit=round(limit, 4),
        affected_assets=[t for t, _ in failures],
    )


def check_category_limit(
    weights: dict[str, float],
    ticker_to_category: dict[str, str],
    category_limits: dict[str, float],
) -> GateCheckResult:
    cat_sums: dict[str, float] = {}
    cat_assets: dict[str, list[str]] = {}
    for ticker, w in weights.items():
        cat = ticker_to_category.get(ticker, "unknown")
        cat_sums[cat] = cat_sums.get(cat, 0.0) + w
        cat_assets.setdefault(cat, []).append(ticker)

    failures = [
        (cat, cat_sums.get(cat, 0.0), lim)
        for cat, lim in category_limits.items()
        if cat_sums.get(cat, 0.0) > lim + 1e-9
    ]

    if not failures:
        return GateCheckResult(
            check_id="category_limit_check",
            status="PASS",
            severity="HIGH",
            message="All categories within limits.",
        )

    worst_cat, worst_total, worst_limit = max(failures, key=lambda x: x[1] - x[2])
    affected: list[str] = []
    for cat, _, _ in failures:
        affected.extend(cat_assets.get(cat, []))

    return GateCheckResult(
        check_id="category_limit_check",
        status="FAIL",
        severity="HIGH",
        message=(
            f"{worst_cat} weight {worst_total:.1%} exceeds category limit {worst_limit:.1%}."
        ),
        value=round(worst_total, 4),
        limit=round(worst_limit, 4),
        affected_assets=affected,
    )


def check_risk_mode_alignment(
    risk_mode_check,
    weights: dict[str, float] | None = None,
    ticker_to_category: dict[str, str] | None = None,
    defensive_categories: list[str] | None = None,
    risk_mode_cfg: RiskModeCheckConfig | None = None,
) -> GateCheckResult:
    if risk_mode_check is None or not risk_mode_check.enabled:
        return GateCheckResult(
            check_id="risk_mode_alignment_check",
            status="N/A",
            severity="MEDIUM",
            message="Risk mode check disabled.",
        )

    gate_status = risk_mode_check.status

    limit: float | None = None
    if risk_mode_cfg is not None:
        if risk_mode_check.status == "REVIEW_REQUIRED":
            limit = risk_mode_cfg.risk_on_defensive_review_threshold
        elif risk_mode_check.status == "PASS_WITH_CAUTION":
            limit = risk_mode_cfg.risk_on_defensive_warning_threshold

    affected: list[str] = []
    if weights and ticker_to_category and defensive_categories:
        def_cats = set(defensive_categories)
        affected = [
            t for t, w in weights.items()
            if ticker_to_category.get(t, "") in def_cats and w > 0
        ]

    return GateCheckResult(
        check_id="risk_mode_alignment_check",
        status=gate_status,
        severity="MEDIUM",
        message=risk_mode_check.message,
        value=round(risk_mode_check.defensive_weight, 4),
        limit=round(limit, 4) if limit is not None else None,
        affected_assets=affected,
    )


def check_turnover(
    actual_turnover: float | None,
    limit: float,
) -> GateCheckResult:
    if actual_turnover is None:
        return GateCheckResult(
            check_id="turnover_check",
            status="N/A",
            severity="LOW",
            message="No previous portfolio state — turnover check skipped.",
        )
    if actual_turnover <= limit + 1e-9:
        return GateCheckResult(
            check_id="turnover_check",
            status="PASS",
            severity="LOW",
            message=f"Turnover {actual_turnover:.1%} within limit {limit:.1%}.",
            value=round(actual_turnover, 4),
            limit=round(limit, 4),
        )
    return GateCheckResult(
        check_id="turnover_check",
        status="FAIL",
        severity="LOW",
        message=f"Turnover {actual_turnover:.1%} exceeds limit {limit:.1%}.",
        value=round(actual_turnover, 4),
        limit=round(limit, 4),
    )


def check_nisa_policy() -> GateCheckResult:
    return GateCheckResult(
        check_id="nisa_policy_check",
        status="N/A",
        severity="INFO",
        message="NISA月次ローテーションは非推奨（情報表示のみ）。",
    )


# ── public API ───────────────────────────────────────────────────────────────


def run_pre_trade_gate(
    weights: dict[str, float],
    ticker_to_category: dict[str, str],
    cfg: AppConfig,
    turnover: float | None = None,
    risk_mode_check=None,
) -> PreTradeGateResult:
    """Run all deterministic pre-trade constraint checks."""
    gate_cfg = cfg.pre_trade_gate

    if not gate_cfg.enabled:
        logger.info("Pre-trade gate disabled.")
        return PreTradeGateResult(overall_status="N/A", checks=[], enabled=False)

    single_limit = (
        gate_cfg.single_asset_limit
        if gate_cfg.single_asset_limit is not None
        else cfg.risk.max_weight_per_asset
    )
    cat_limits = gate_cfg.category_limits if gate_cfg.category_limits else cfg.risk.max_category_weights
    turnover_limit = cfg.turnover.effective_limit

    checks = [
        check_single_asset_limit(weights, single_limit),
        check_category_limit(weights, ticker_to_category, cat_limits),
        check_risk_mode_alignment(
            risk_mode_check,
            weights=weights,
            ticker_to_category=ticker_to_category,
            defensive_categories=cfg.risk_mode_checks.defensive_categories,
            risk_mode_cfg=cfg.risk_mode_checks,
        ),
        check_turnover(turnover, turnover_limit),
        check_nisa_policy(),
    ]

    overall = _resolve_overall_status(checks)
    logger.info(f"Pre-trade gate: overall_status={overall}")
    return PreTradeGateResult(overall_status=overall, checks=checks, enabled=True)


def save_pre_trade_gate_result(result: PreTradeGateResult, output_dir: str) -> str:
    """Save pre_trade_gate_result.json to output_dir; returns the file path."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / "pre_trade_gate_result.json"
    path.write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"Pre-trade gate result saved to {path}")
    return str(path)
