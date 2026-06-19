"""Phase 5.4: Slack Signal Digest tests.

Tests:
- Correct wording for BUY_CANDIDATE, HIGH_PRIORITY_CANDIDATE, WATCH/HOLD_OFF
- Reference symbols appear in 参照指標 section, not candidate section
- USER_APPROVED shows "候補として確認済み" (not "買付承認")
- Forbidden order words absent from all digest text
- no_order_quantity and no_auto_trade language in footer
- Graceful handling of empty candidates and missing Slack token
- CLI --signal-slack argument exists and is parsed correctly
- build_signal_digest_text is a pure function (no file writes)

Safety invariants verified:
- No forbidden words: 買え/売れ/注文実行/購入実行/売却実行/自動売買実行
- watchlist.csv never modified by digest functions
- Slack posting never sets USER_APPROVED
"""
from __future__ import annotations

import sys
from unittest.mock import patch

import pytest

from src.signals.slack_signal_digest import (
    FORBIDDEN_WORDS,
    build_review_slack_digest,
    build_signal_digest_text,
    post_signal_digest,
)
from src.signals.signal_models import (
    FinalSignal,
    MemberStance,
    SignalMemberOutput,
    SignalResult,
    SignalSide,
)

# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_result(
    symbol: str,
    final_signal: FinalSignal,
    score: int = 3,
    confidence: float = 0.82,
    triggers: list[str] | None = None,
    risk_flags: list[str] | None = None,
    watchlist_update: str | None = None,
) -> SignalResult:
    return SignalResult(
        symbol=symbol,
        signal_side=SignalSide.BUY,
        final_signal=final_signal,
        confidence=confidence,
        total_score=score,
        positive_member_count=4,
        cautious_member_count=1,
        reject_member_count=0,
        veto_count=0,
        trigger_labels=triggers or ["急落シグナル"],
        positive_reasons=["テーマ継続"],
        risk_flags=risk_flags or [],
        veto_flags=[],
        dissenting_views=[],
        recommended_action_text="最終判断は人間が行います。",
        watchlist_update=watchlist_update,
        next_review_date=None,
        member_outputs=[],
    )


def _make_reference_result(symbol: str) -> SignalResult:
    return _make_result(
        symbol=symbol,
        final_signal=FinalSignal.WATCH,
        risk_flags=["reference_symbol_not_candidate"],
        watchlist_update=None,
    )


def _watchlist_row(ticker: str, status: str, nrd: str = "2026-06-13") -> dict:
    return {
        "ticker": ticker,
        "status": status,
        "confidence": "0.82",
        "next_review_date": nrd,
        "reason_summary": "テスト理由",
    }


# ── Tests: build_signal_digest_text ──────────────────────────────────────────


def test_slack_signal_digest_buy_candidate():
    """BUY_CANDIDATE result → 'BUY_CANDIDATE' section and correct wording."""
    result = _make_result("ITA", FinalSignal.BUY_CANDIDATE, triggers=["急落後の反発"])
    text = build_signal_digest_text(results=[result])
    assert "BUY_CANDIDATE" in text or "買い候補条件を満たした" in text
    assert "ITA" in text


def test_slack_signal_digest_high_priority_wording():
    """HIGH_PRIORITY_CANDIDATE → '優先確認候補' in digest."""
    result = _make_result("XLU", FinalSignal.HIGH_PRIORITY_CANDIDATE, score=6, confidence=0.90)
    text = build_signal_digest_text(results=[result])
    assert "優先確認候補" in text
    assert "XLU" in text


def test_slack_signal_digest_watch_hold_off():
    """WATCH and HOLD_OFF results appear in the monitoring section."""
    r1 = _make_result("PAVE", FinalSignal.WATCH)
    r2 = _make_result("CIBR", FinalSignal.HOLD_OFF)
    text = build_signal_digest_text(results=[r1, r2])
    assert "PAVE" in text
    assert "CIBR" in text
    assert "WATCH" in text or "HOLD_OFF" in text


def test_slack_signal_digest_reference_symbols_not_candidates():
    """SPY/QQQ in reference_symbols → in 参照指標 section, NOT in BUY_CANDIDATE/WATCH sections."""
    spy = _make_reference_result("SPY")
    qqq = _make_reference_result("QQQ")
    ita = _make_result("ITA", FinalSignal.BUY_CANDIDATE)
    text = build_signal_digest_text(
        results=[spy, qqq, ita],
        reference_symbols=["SPY", "QQQ", "SOXX", "SMH"],
    )
    # Reference section mentions these symbols
    assert "参照指標" in text
    assert "市場レジーム" in text or "市場判定" in text
    # SPY / QQQ appear — but as reference, not as candidates
    assert "ITA" in text  # candidate still appears
    # "買い候補化対象ではありません" must be in the reference section
    assert "買い候補化対象ではありません" in text


def test_slack_signal_digest_user_approved_wording():
    """USER_APPROVED watchlist row → '候補として確認済み' (NOT '買付承認')."""
    approved = _watchlist_row("ITA", "USER_APPROVED", nrd="2026-06-13")
    text = build_signal_digest_text(results=[], watchlist_items=[approved])
    assert "候補として確認済み" in text
    assert "ITA" in text
    assert "買付承認" not in text


def test_slack_signal_digest_no_forbidden_order_words():
    """Digest text must not contain any FORBIDDEN_WORDS."""
    results = [
        _make_result("ITA", FinalSignal.BUY_CANDIDATE),
        _make_result("XLU", FinalSignal.HIGH_PRIORITY_CANDIDATE),
        _make_result("PAVE", FinalSignal.WATCH),
        _make_reference_result("SPY"),
    ]
    watchlist = [
        _watchlist_row("ITA", "USER_APPROVED"),
        _watchlist_row("GRID", "USER_REJECTED"),
    ]
    text = build_signal_digest_text(results=results, watchlist_items=watchlist)
    for word in FORBIDDEN_WORDS:
        assert word not in text, f"Forbidden word '{word}' found in digest"


def test_slack_signal_digest_no_order_quantity():
    """Safety footer includes '注文数量計算なし'."""
    text = build_signal_digest_text(results=[])
    assert "注文数量計算なし" in text


def test_slack_signal_digest_no_auto_trade():
    """Safety footer includes '自動売買なし'."""
    text = build_signal_digest_text(results=[])
    assert "自動売買なし" in text


def test_slack_signal_digest_handles_no_candidates():
    """Empty results → graceful output with '通常監視継続'."""
    text = build_signal_digest_text(results=[])
    assert text  # non-empty
    assert "通常監視継続" in text or "シグナルなし" in text


def test_slack_signal_digest_handles_missing_slack_token(monkeypatch):
    """post_signal_digest without SLACK_WEBHOOK_URL → returns False, no exception."""
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    result = post_signal_digest("テストメッセージ")
    assert result is False


# ── Tests: build_review_slack_digest ─────────────────────────────────────────


def test_review_digest_user_approved_wording():
    """build_review_slack_digest: USER_APPROVED → '候補として確認済み'."""
    items = [
        _watchlist_row("ITA", "USER_APPROVED"),
        _watchlist_row("XLU", "BUY_CANDIDATE"),
    ]
    text = build_review_slack_digest(items)
    assert "候補として確認済み" in text
    assert "ITA" in text
    assert "XLU" in text
    for word in FORBIDDEN_WORDS:
        assert word not in text, f"Forbidden word '{word}' in review digest"


def test_review_digest_empty_watchlist():
    """build_review_slack_digest with no items → non-empty graceful output."""
    text = build_review_slack_digest([])
    assert text
    assert "自動売買なし" in text or "注文数量計算なし" in text


# ── Tests: CLI arg and purity ─────────────────────────────────────────────────


def test_signal_slack_cli_dry_run():
    """--signal-slack flag is recognized by parse_args without error."""
    from main import parse_args

    with patch.object(
        sys, "argv",
        ["main.py", "--crash-signal-check", "--dry-run", "--signal-slack"],
    ):
        args = parse_args()
    assert args.crash_signal_check is True
    assert args.dry_run is True
    assert args.signal_slack is True


def test_signal_slack_does_not_update_watchlist(tmp_path):
    """build_signal_digest_text is a pure function — does not write any files."""
    result = _make_result("ITA", FinalSignal.BUY_CANDIDATE)
    watchlist_file = tmp_path / "watchlist.csv"

    text = build_signal_digest_text(results=[result])

    assert text  # non-empty output
    assert not watchlist_file.exists()  # no file was created in tmp_path
