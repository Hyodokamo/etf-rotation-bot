"""Phase 2.4: Risk-ON / defensive allocation consistency check."""
from dataclasses import dataclass

from src.config_loader import RiskModeCheckConfig
from src.logger import logger


@dataclass
class RiskModeCheckResult:
    enabled: bool
    risk_mode: str          # "RISK_ON" | "RISK_OFF"
    defensive_weight: float
    status: str             # "PASS" | "PASS_WITH_CAUTION" | "REVIEW_REQUIRED" | "N/A"
    message: str


def check_risk_mode_consistency(
    weights: dict[str, float],
    ticker_to_category: dict[str, str],
    risk_off: bool,
    cfg: RiskModeCheckConfig,
) -> RiskModeCheckResult:
    """Check if defensive allocation is consistent with the current risk mode.

    Risk-OFF: high defensive weight is expected → PASS unless risk assets are excessive.
    Risk-ON:  high defensive weight triggers warning or review.
    """
    risk_mode = "RISK_OFF" if risk_off else "RISK_ON"

    if not cfg.enabled:
        return RiskModeCheckResult(
            enabled=False,
            risk_mode=risk_mode,
            defensive_weight=0.0,
            status="N/A",
            message="Risk mode check disabled.",
        )

    defensive_cats = set(cfg.defensive_categories)
    defensive_weight = sum(
        w for t, w in weights.items() if ticker_to_category.get(t, "") in defensive_cats
    )

    if risk_off:
        return RiskModeCheckResult(
            enabled=True,
            risk_mode=risk_mode,
            defensive_weight=defensive_weight,
            status="PASS",
            message=f"Risk-OFF: defensive allocation ({defensive_weight:.1%}) is expected.",
        )

    # Risk-ON: evaluate defensive weight level
    if defensive_weight > cfg.risk_on_defensive_review_threshold:
        status = "REVIEW_REQUIRED"
        message = (
            f"Risk-ON ですが防御資産比率が {defensive_weight:.1%} と高くなっています"
            f"（閾値 {cfg.risk_on_defensive_review_threshold:.0%} 超）。"
            " score/vol 配分による低ボラ資産への偏りを確認してください。"
        )
    elif defensive_weight > cfg.risk_on_defensive_warning_threshold:
        status = "PASS_WITH_CAUTION"
        message = (
            f"Risk-ON ですが防御資産比率が {defensive_weight:.1%} とやや高めです"
            f"（閾値 {cfg.risk_on_defensive_warning_threshold:.0%} 超）。モニタリングを推奨します。"
        )
    else:
        status = "PASS"
        message = f"Risk-ON: 防御資産比率 ({defensive_weight:.1%}) は想定範囲内です。"

    logger.info(f"Risk mode check: {risk_mode}, defensive={defensive_weight:.1%}, status={status}")
    return RiskModeCheckResult(
        enabled=True,
        risk_mode=risk_mode,
        defensive_weight=defensive_weight,
        status=status,
        message=message,
    )
