"""Phase 2 integration test using a mocked LLM client.

Verifies the full pipeline: audit context → LLM call → validation → report → Slack.
No real API key required.
"""
import json
from datetime import date
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

from src.audit_context_builder import build_audit_context
from src.config_loader import load_config
from src.llm_auditor import _extract_json, run_audit
from src.prompt_templates import build_user_prompt
from src.report_builder import build_report
from src.risk_gate import RiskGateResult
from src.schemas import AuditStatus, LlmAuditResult
from src.slack_client import build_slack_summary

# ──────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────

MOCK_LLM_RESPONSE = json.dumps(
    {
        "status": "PASS_WITH_CAUTION",
        "summary": "配分は概ね良好ですが、コアエクイティへの集中度が高い点に注意が必要です。リスクオンモードとの整合性は確認されました。",
        "adjustments": [
            {
                "ticker": "VOO",
                "current_weight": 0.25,
                "suggested_weight": 0.22,
                "reason": "コアエクイティカテゴリ内での集中を緩和するため",
            }
        ],
        "pre_trade_checks": [
            {
                "check_id": "max_weight_check",
                "result": "PASS",
                "description": "全銘柄がmax_weight_per_asset(0.25)以内に収まっています",
                "value": 0.25,
            },
            {
                "check_id": "category_cap_check",
                "result": "WARN",
                "description": "core_equityカテゴリが上限(0.50)に近い水準です",
                "value": 0.48,
            },
            {
                "check_id": "turnover_check",
                "result": "PASS",
                "description": "ターンオーバーがmax_turnover(0.50)以内です",
                "value": 0.15,
            },
        ],
        "nisa_checks": [
            {"ticker": "VOO", "is_nisa_suitable": True, "reason": "海外ETF・特定口座対応"},
            {"ticker": "TLT", "is_nisa_suitable": True, "reason": "海外ETF・特定口座対応"},
            {"ticker": "SGOV", "is_nisa_suitable": True, "reason": "海外ETF・特定口座対応"},
            {"ticker": "GLDM", "is_nisa_suitable": True, "reason": "海外ETF・特定口座対応"},
        ],
        "apply_adjustment": False,
    },
    ensure_ascii=False,
)


@pytest.fixture
def cfg():
    return load_config("config.yaml")


@pytest.fixture
def run_date():
    return date(2026, 5, 23)


@pytest.fixture
def weights():
    return {
        "VOO": 0.25,
        "TLT": 0.20,
        "SGOV": 0.18,
        "GLDM": 0.12,
        "QQQM": 0.10,
        "IEF": 0.08,
        "VNQ": 0.07,
    }


@pytest.fixture
def scores(weights):
    return pd.Series(
        {"VOO": 0.85, "TLT": 0.72, "SGOV": 0.61, "GLDM": 0.58, "QQQM": 0.54, "IEF": 0.49, "VNQ": 0.43}
    )


@pytest.fixture
def indicators(weights):
    idx = list(weights.keys())
    rng = np.random.default_rng(42)
    return pd.DataFrame(
        {
            "mom_1m": rng.normal(0.01, 0.02, len(idx)),
            "mom_3m": rng.normal(0.03, 0.04, len(idx)),
            "mom_6m": rng.normal(0.06, 0.06, len(idx)),
            "mom_12m": rng.normal(0.10, 0.08, len(idx)),
            "vol_20d": np.abs(rng.normal(0.15, 0.05, len(idx))),
        },
        index=idx,
    )


@pytest.fixture
def prices(weights):
    idx = pd.date_range("2024-01-01", periods=300, freq="B")
    rng = np.random.default_rng(42)
    data = {}
    for t in weights:
        data[t] = np.cumprod(1 + rng.normal(0.0003, 0.01, 300)) * 100
    return pd.DataFrame(data, index=idx)


@pytest.fixture
def risk_gate():
    return RiskGateResult(risk_off=False, sp500_return=0.05, message="S&P500 60日リターン: +5.00%")


@pytest.fixture
def prev_weights():
    return {"VOO": 0.30, "TLT": 0.20, "SGOV": 0.15, "GLDM": 0.10, "QQQM": 0.15, "IEF": 0.10}


# ──────────────────────────────────────────────
# Tests
# ──────────────────────────────────────────────


def test_audit_context_is_serializable(cfg, weights, scores, indicators, risk_gate, prev_weights, run_date):
    ctx = build_audit_context(
        cfg=cfg,
        weights=weights,
        scores=scores,
        indicators=indicators,
        risk_gate=risk_gate,
        prev_weights=prev_weights,
        turnover=0.15,
        run_date=run_date,
    )
    # Must be JSON-serializable (sent to Claude)
    dumped = json.dumps(ctx, ensure_ascii=False)
    assert len(dumped) > 100
    assert json.loads(dumped)["run_date"] == "2026-05-23"


def test_user_prompt_embeds_context(cfg, weights, scores, indicators, risk_gate, run_date):
    ctx = build_audit_context(
        cfg=cfg,
        weights=weights,
        scores=scores,
        indicators=indicators,
        risk_gate=risk_gate,
        prev_weights=None,
        turnover=None,
        run_date=run_date,
    )
    prompt = build_user_prompt(ctx)
    assert "VOO" in prompt
    assert "2026-05-23" in prompt


def test_run_audit_with_mock_returns_result(weights):
    """run_audit() parses mocked LLM response and returns LlmAuditResult."""
    mock_client = MagicMock()
    mock_client.complete.return_value = MOCK_LLM_RESPONSE

    result = run_audit(
        context={"run_date": "2026-05-23", "allocation": []},
        weights=weights,
        client=mock_client,
    )

    assert result is not None
    assert result.status == AuditStatus.PASS_WITH_CAUTION
    assert result.apply_adjustment is False
    assert len(result.adjustments) == 1
    assert len(result.pre_trade_checks) == 3
    assert len(result.nisa_checks) == 4


def test_run_audit_apply_adjustment_forced_false(weights):
    """apply_adjustment is always forced to False regardless of LLM response."""
    response_with_true = json.loads(MOCK_LLM_RESPONSE)
    response_with_true["apply_adjustment"] = True

    mock_client = MagicMock()
    mock_client.complete.return_value = json.dumps(response_with_true, ensure_ascii=False)

    result = run_audit(context={}, weights=weights, client=mock_client)

    assert result is not None
    assert result.apply_adjustment is False


def test_run_audit_returns_none_on_api_error(weights):
    """run_audit() returns None (Phase 1 fallback) when API call fails."""
    mock_client = MagicMock()
    mock_client.complete.side_effect = Exception("network error")

    result = run_audit(context={}, weights=weights, client=mock_client, max_retries=0)

    assert result is None


def test_run_audit_returns_none_on_bad_json(weights):
    """run_audit() returns None when LLM returns unparseable response."""
    mock_client = MagicMock()
    mock_client.complete.return_value = "申し訳ありません。JSONを生成できませんでした。"

    result = run_audit(context={}, weights=weights, client=mock_client, max_retries=0)

    assert result is None


def test_report_contains_audit_section(cfg, weights, scores, indicators, prices, risk_gate, run_date):
    """Generated Markdown report includes AI audit section when audit_result is provided."""
    audit_result = LlmAuditResult.model_validate(json.loads(MOCK_LLM_RESPONSE))

    report = build_report(
        cfg=cfg,
        weights=weights,
        scores=scores,
        indicators=indicators,
        prices=prices,
        risk_gate=risk_gate,
        prev_weights=None,
        turnover=None,
        run_date=run_date,
        audit_result=audit_result,
    )

    assert "AI監査結果" in report
    assert "PASS_WITH_CAUTION" in report
    assert "調整提案" in report
    assert "プレトレードチェック" in report
    assert "NISA適格性" in report
    assert "apply_adjustment" not in report.lower() or "false" in report.lower()


def test_report_no_audit_section_when_none(cfg, weights, scores, indicators, prices, risk_gate, run_date):
    """Report does not include AI audit section when audit_result is None."""
    report = build_report(
        cfg=cfg,
        weights=weights,
        scores=scores,
        indicators=indicators,
        prices=prices,
        risk_gate=risk_gate,
        prev_weights=None,
        turnover=None,
        run_date=run_date,
        audit_result=None,
    )
    assert "AI監査結果" not in report


def test_report_includes_disclaimer(cfg, weights, scores, indicators, prices, risk_gate, run_date):
    """AI audit section always includes the 'audit-only' disclaimer."""
    audit_result = LlmAuditResult.model_validate(json.loads(MOCK_LLM_RESPONSE))
    report = build_report(
        cfg=cfg,
        weights=weights,
        scores=scores,
        indicators=indicators,
        prices=prices,
        risk_gate=risk_gate,
        prev_weights=None,
        turnover=None,
        run_date=run_date,
        audit_result=audit_result,
    )
    assert "参考情報" in report
    assert "自動売買は行いません" in report


def test_slack_message_includes_audit_status(weights, risk_gate):
    audit_result = LlmAuditResult.model_validate(json.loads(MOCK_LLM_RESPONSE))
    msg = build_slack_summary(
        weights=weights,
        risk_off=risk_gate.risk_off,
        turnover=0.15,
        report_path="outputs/report_2026-05-23.md",
        audit_result=audit_result,
    )
    assert "AI監査" in msg
    assert "PASS_WITH_CAUTION" in msg


def test_slack_message_without_audit(weights, risk_gate):
    msg = build_slack_summary(
        weights=weights,
        risk_off=False,
        turnover=0.15,
        report_path="outputs/report_2026-05-23.md",
        audit_result=None,
    )
    assert "AI監査" not in msg
    assert "Top5" in msg or "VOO" in msg


def test_full_pipeline_output(cfg, weights, scores, indicators, prices, risk_gate, prev_weights, run_date, capsys):
    """Full Phase 2 pipeline smoke test — prints report excerpt."""
    audit_result = LlmAuditResult.model_validate(json.loads(MOCK_LLM_RESPONSE))

    report = build_report(
        cfg=cfg,
        weights=weights,
        scores=scores,
        indicators=indicators,
        prices=prices,
        risk_gate=risk_gate,
        prev_weights=prev_weights,
        turnover=0.15,
        run_date=run_date,
        audit_result=audit_result,
    )

    slack = build_slack_summary(
        weights=weights,
        risk_off=risk_gate.risk_off,
        turnover=0.15,
        report_path="outputs/report_2026-05-23.md",
        audit_result=audit_result,
    )

    print("\n" + "=" * 60)
    print("SLACK MESSAGE:")
    print("=" * 60)
    print(slack)
    print("\n" + "=" * 60)
    print("REPORT (AI audit section only):")
    print("=" * 60)
    start = report.find("## AI監査結果")
    end = report.find("## 注意事項")
    print(report[start:end].strip())

    captured = capsys.readouterr()
    assert "PASS_WITH_CAUTION" in captured.out
    assert "AI監査" in captured.out
