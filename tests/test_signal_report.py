"""Tests for Phase 5.1: signal_report.py + CLI dry-run integration."""
import pytest

from src.signals.signal_config import load_signal_config
from src.signals.signal_context_builder import build_signal_context
from src.signals.signal_engine import aggregate_signal
from src.signals.signal_models import FinalSignal, MemberStance, SignalMemberOutput, SignalResult, SignalSide
from src.signals.signal_report import (
    FORBIDDEN_WORDS,
    build_signal_markdown,
    build_signal_slack_message,
    save_signal_report,
)


def _cfg():
    return load_signal_config("/nonexistent/no_config.yaml")


def _result(
    symbol="GRID",
    final_signal=FinalSignal.BUY_CANDIDATE,
    watchlist_update="BUY_CANDIDATE",
    trigger_labels=None,
) -> SignalResult:
    return SignalResult(
        symbol=symbol,
        signal_side=SignalSide.BUY,
        final_signal=final_signal,
        confidence=0.72,
        total_score=4,
        positive_member_count=4,
        veto_count=0,
        trigger_labels=trigger_labels or ["QQQ急落(-3.5%)"],
        positive_reasons=["モメンタム良好。中期トレンドが維持されている。"],
        dissenting_views=["aqr_meb: バリュエーションの確認が不十分。"],
        recommended_action_text=(
            "買い候補条件を満たしました。WatchlistをBUY_CANDIDATEに更新候補です。"
            "最終判断は人間が行います。自動売買は行いません。注文数量は計算しません。"
        ),
        watchlist_update=watchlist_update,
        member_outputs=[
            SignalMemberOutput(member_id="aqr_meb", stance=MemberStance.POSITIVE, score=2, confidence=0.8),
            SignalMemberOutput(member_id="howard_marks", stance=MemberStance.NEUTRAL, score=0, confidence=0.5),
        ],
    )


# ── markdown ──────────────────────────────────────────────────────────────────


def test_signal_report_markdown():
    md = build_signal_markdown([_result()], market_regime="correction")
    assert "Daily Signal Report" in md
    assert "GRID" in md
    assert "BUY_CANDIDATE" in md
    assert "correction" in md
    assert "安全注記" in md
    assert "最終判断は人間" in md
    assert "no_order_quantity" in md
    assert "no_auto_trade" in md


def test_signal_report_markdown_empty_signals():
    md = build_signal_markdown([], market_regime="neutral")
    assert "Daily Signal Report" in md
    assert "安全注記" in md
    assert "シグナルはありません" in md


def test_signal_report_markdown_includes_safety_notes():
    md = build_signal_markdown([_result()])
    assert "SELL シグナルは今回のMVPでは実装されていません" in md
    assert "core_manual" in md
    assert "売却提案の対象外" in md


def test_signal_report_no_buy_sell_order_wording():
    """Forbidden imperative words must not appear in the report."""
    md = build_signal_markdown([_result()])
    for bad in ("買え", "売れ", "購入実行", "売却実行"):
        assert bad not in md, f"Forbidden word '{bad}' found in signal report"


def test_signal_report_slack_message():
    msg = build_signal_slack_message([_result()], market_regime="correction")
    assert "GRID" in msg
    assert "BUY_CANDIDATE" in msg
    for bad in ("買え", "売れ", "購入実行", "売却実行"):
        assert bad not in msg


def test_signal_report_save(tmp_path):
    md = build_signal_markdown([_result()])
    path = save_signal_report(md, out_dir=tmp_path)
    from pathlib import Path
    assert Path(path).exists()
    content = Path(path).read_text(encoding="utf-8")
    assert "Daily Signal Report" in content


# ── CLI dry-run integration ───────────────────────────────────────────────────


def test_signal_cli_dry_run(tmp_path):
    """Dry-run: signal generated in memory; no watchlist or history files written."""
    from src.signals.signal_committee_runner import run_signal_committee
    from src.signals.watchlist_store import (
        append_signal_history,
        load_watchlist,
        save_watchlist,
        update_watchlist_entry,
    )

    cfg = _cfg()
    ctx = build_signal_context("GRID", {}, cfg)
    members = run_signal_committee(ctx, client=None)
    result = aggregate_signal("GRID", SignalSide.BUY, members, ctx, cfg)

    history_path = tmp_path / "signal_history.csv"
    watchlist_path = tmp_path / "watchlist.csv"

    # dry_run: append + save produce no files
    watchlist = load_watchlist(watchlist_path)
    updated = update_watchlist_entry(watchlist, result, dry_run=True)
    append_signal_history(result, history_path, dry_run=True)
    save_watchlist(updated, watchlist_path, dry_run=True, archive_dir=tmp_path / "archive")

    assert not history_path.exists(), "dry_run must not write signal_history.csv"
    assert not watchlist_path.exists(), "dry_run must not write watchlist.csv"

    # signal pipeline still works: result is valid
    assert result.final_signal in list(FinalSignal)
    assert result.no_order_quantity is True
    assert result.no_auto_trade is True

    # report generated in memory is clean
    md = build_signal_markdown([result])
    for bad in FORBIDDEN_WORDS:
        assert bad not in md


def test_signal_report_forbidden_words_constant():
    """FORBIDDEN_WORDS list covers the required terms."""
    for required in ("買え", "売れ", "購入実行", "売却実行"):
        assert required in FORBIDDEN_WORDS
