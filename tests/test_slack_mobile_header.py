"""MVP-1 (mobile UX): tests for the phone-first summary header and wording.

Scope: docs/slack_mobile_ux_review.md + docs/slack_message_templates.md.
Records decisions only — no orders, no quantities, no auto-trade are involved.
"""
import pytest

from src.slack_blocks import (
    build_mobile_summary_header,
    build_review_decision_section,
)

_STATES = ["PASS", "PASS_WITH_CAUTION", "REVIEW_REQUIRED", "FAIL"]

# Promotional trade language that must never appear (per スコープ文言制約).
# Applied to the *static* strings we author (header / review section).
_NG_WORDS = ["買う", "売る", "購入", "注文", "利確", "損切り", "今が買い時", "必ず上がる"]

# Unambiguously promotional subset — safe to assert even on dynamically composed
# messages (e.g. build_slack_summary may legitimately carry cautionary advisory
# text like「追加購入は控える」, so 注文/購入 are intentionally NOT banned there).
_NG_PROMO_ONLY = ["利確", "損切り", "今が買い時", "必ず上がる"]

# Wording that conveys "we do not auto-trade / size orders / human decides".
_AUTO_TRADE_MARKERS = ["自動売買は行いません", "最終判断は人間"]


# ── build_mobile_summary_header structure ────────────────────────────────────

@pytest.mark.parametrize("state", _STATES)
def test_header_has_conclusion_and_action(state):
    text = build_mobile_summary_header(state)
    assert "今月の結論:" in text
    assert "推奨アクション:" in text


@pytest.mark.parametrize("state", _STATES)
def test_header_first_three_lines_carry_the_gist(state):
    """First 3 lines must answer 結論／急ぎ度／やること."""
    lines = build_mobile_summary_header(state).split("\n")
    assert lines[0].startswith(("✅", "⚠️", "🔶", "❌", "ℹ️"))
    assert "ETF Rotation Bot 月次レビュー" in lines[0]
    assert lines[1].startswith("今月の結論:")
    assert lines[2].startswith("推奨アクション:")


@pytest.mark.parametrize("state", _STATES)
def test_header_includes_auto_trade_notice(state):
    text = build_mobile_summary_header(state)
    for marker in _AUTO_TRADE_MARKERS:
        assert marker in text


@pytest.mark.parametrize("state", _STATES)
def test_header_no_promotional_trade_language(state):
    text = build_mobile_summary_header(state)
    for ng in _NG_WORDS:
        assert ng not in text, f"NG word {ng!r} found in {state} header"


def test_header_fail_signals_caution():
    text = build_mobile_summary_header("FAIL")
    assert "要対応" in text or "推奨しません" in text


def test_header_pass_signals_no_urgency():
    text = build_mobile_summary_header("PASS")
    assert "急ぎ" in text


def test_header_unknown_status_falls_back_to_na():
    text = build_mobile_summary_header(None)
    assert "今月の結論:" in text
    assert "ℹ️" in text


def test_header_includes_month_when_run_date_given():
    text = build_mobile_summary_header("PASS", run_date="2026-06-20")
    assert "（2026-06）" in text


def test_header_omits_month_when_run_date_missing():
    text = build_mobile_summary_header("PASS", run_date=None)
    assert "（" not in text.split("\n")[0]


# ── review decision section: phone-first wording ─────────────────────────────

@pytest.mark.parametrize("state", _STATES)
def test_review_section_points_to_pc_nas(state):
    text = build_review_decision_section(state, ["single_asset_limit_check"])
    assert "後でPC/NASで" in text


@pytest.mark.parametrize("state", _STATES)
def test_review_section_no_promotional_trade_language(state):
    text = build_review_decision_section(state, ["single_asset_limit_check"])
    for ng in _NG_WORDS:
        assert ng not in text, f"NG word {ng!r} found in {state} review section"


@pytest.mark.parametrize("state", _STATES)
def test_review_section_states_auto_trade_not_done(state):
    text = build_review_decision_section(state)
    assert "自動売買は行いません" in text
    assert "最終判断は人間" in text


# ── build_slack_summary integration ──────────────────────────────────────────

def _gate(status="PASS", checks=None):
    from src.pre_trade_gate import PreTradeGateResult
    return PreTradeGateResult(overall_status=status, checks=checks or [], enabled=True)


def test_summary_starts_with_mobile_header():
    from src.slack_client import build_slack_summary
    msg = build_slack_summary(
        weights={"BND": 0.32, "VTV": 0.27, "VOO": 0.23, "QQQM": 0.18},
        risk_off=False, turnover=0.20, report_path="outputs/report_2026-06-20.md",
        pre_trade_gate=_gate("PASS"),
    )
    assert msg.startswith("✅ ETF Rotation Bot 月次レビュー")
    assert "今月の結論:" in msg


def test_summary_top_allocation_is_single_line():
    from src.slack_client import build_slack_summary
    msg = build_slack_summary(
        weights={"BND": 0.32, "VTV": 0.27, "VOO": 0.23, "QQQM": 0.18},
        risk_off=False, turnover=0.20, report_path="outputs/report_2026-06-20.md",
        pre_trade_gate=_gate("PASS"),
    )
    top_line = next(l for l in msg.split("\n") if l.startswith("Top配分:"))
    assert " · " in top_line
    assert "BND" in top_line and "QQQM" in top_line


def test_summary_report_line_notes_phone_limitation():
    from src.slack_client import build_slack_summary
    msg = build_slack_summary(
        weights={"VOO": 0.6, "BND": 0.4}, risk_off=False, turnover=0.10,
        report_path="outputs/report_2026-06-20.md", pre_trade_gate=_gate("PASS"),
    )
    assert "スマホで開けない場合あり" in msg


def test_summary_has_pc_nas_record_path():
    from src.slack_client import build_slack_summary
    msg = build_slack_summary(
        weights={"VOO": 0.6, "BND": 0.4}, risk_off=False, turnover=0.10,
        report_path="outputs/report_2026-06-20.md", pre_trade_gate=_gate("PASS"),
    )
    assert "後でPC/NASで" in msg


def test_summary_no_promotional_trade_language():
    from src.slack_client import build_slack_summary
    msg = build_slack_summary(
        weights={"VOO": 0.6, "BND": 0.4}, risk_off=False, turnover=0.10,
        report_path="outputs/report_2026-06-20.md", pre_trade_gate=_gate("PASS"),
    )
    # Only the unambiguous promo subset — avoids false positives on legitimate
    # cautionary wording that dynamic sections may include.
    for ng in _NG_PROMO_ONLY:
        assert ng not in msg, f"NG word {ng!r} found in summary"
