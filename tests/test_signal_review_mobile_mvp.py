"""Signal Review mobile MVP tests.

Covers:
1. Candidate-only safe upsert (watchlist_store.upsert_candidate_entries)
2. Board member summary in the Slack digest (slack_signal_digest)
3. Button-aware delivery branch (deliver_signal_digest)
4. Button-path human decision record (create_if_missing / protect_human_locked)

Advisory only — no orders, no quantities, no auto-trade anywhere.
"""
from __future__ import annotations

import json

import pytest

from src.signals.signal_models import (
    FinalSignal,
    MemberStance,
    SignalMemberOutput,
    SignalResult,
    SignalSide,
)
from src.signals.watchlist_store import WATCHLIST_COLUMNS, load_watchlist, upsert_candidate_entries
from src.signals.slack_signal_digest import (
    FORBIDDEN_WORDS,
    board_summary_lines,
    build_signal_digest_text,
    candidate_items_for_blocks,
    deliver_signal_digest,
)
from src.signals.signal_review import record_human_signal_decision


# ── helpers ───────────────────────────────────────────────────────────────────

def _res(symbol, final_signal, *, score=4, conf=0.82, members=None) -> SignalResult:
    return SignalResult(
        symbol=symbol,
        signal_side=SignalSide.BUY,
        final_signal=final_signal,
        confidence=conf,
        total_score=score,
        positive_member_count=3,
        trigger_labels=["SPY急落(-2.5%)"],
        positive_reasons=["テーマ継続"],
        recommended_action_text="Watchlist候補化。最終判断は人間が行います。",
        watchlist_update=final_signal.value if "CANDIDATE" in final_signal.value else None,
        member_outputs=members or [],
    )


def _member(mid, stance, score, rationale="", dissent="") -> SignalMemberOutput:
    return SignalMemberOutput(
        member_id=mid, stance=stance, score=score, confidence=0.8,
        rationale=rationale, dissenting_view=dissent,
    )


def _wrow(ticker, status) -> dict:
    r = {c: "" for c in WATCHLIST_COLUMNS}
    r.update({"ticker": ticker, "status": status, "confidence": "0.80"})
    return r


# ── 1. candidate-only safe upsert ─────────────────────────────────────────────

def test_upsert_adds_buy_and_high_priority_only():
    results = [
        _res("ITA", FinalSignal.BUY_CANDIDATE),
        _res("XLU", FinalSignal.HIGH_PRIORITY_CANDIDATE),
        _res("SPY", FinalSignal.NO_ACTION),
        _res("PAVE", FinalSignal.WATCH),
        _res("GRID", FinalSignal.HOLD_OFF),
    ]
    rows, count = upsert_candidate_entries([], results)
    tickers = {r["ticker"]: r["status"] for r in rows}
    assert count == 2
    assert tickers == {"ITA": "BUY_CANDIDATE", "XLU": "HIGH_PRIORITY_CANDIDATE"}


def test_upsert_does_not_add_no_action():
    rows, count = upsert_candidate_entries([], [_res("SPY", FinalSignal.NO_ACTION)])
    assert count == 0
    assert rows == []


def test_upsert_never_overwrites_user_approved():
    rows, count = upsert_candidate_entries(
        [_wrow("ITA", "USER_APPROVED")], [_res("ITA", FinalSignal.BUY_CANDIDATE)]
    )
    assert count == 0
    assert rows[0]["status"] == "USER_APPROVED"


def test_upsert_never_overwrites_user_rejected():
    rows, count = upsert_candidate_entries(
        [_wrow("GRID", "USER_REJECTED")], [_res("GRID", FinalSignal.HIGH_PRIORITY_CANDIDATE)]
    )
    assert count == 0
    assert rows[0]["status"] == "USER_REJECTED"


def test_upsert_updates_ai_settable_row():
    rows, count = upsert_candidate_entries(
        [_wrow("ITA", "WATCH")], [_res("ITA", FinalSignal.BUY_CANDIDATE)]
    )
    assert count == 1
    assert rows[0]["status"] == "BUY_CANDIDATE"


# ── 2. board member summary in digest ─────────────────────────────────────────

def _candidate_with_board():
    members = [
        _member("howard_marks", MemberStance.POSITIVE, 2,
                rationale="下落後の安全余地は改善。ただし段階確認が必要。"),
        _member("rob_arnott", MemberStance.REJECT, -2,
                dissent="高バリュエーション。押し目買い的な追随は危険。"),
        _member("core_ai_auditor", MemberStance.CAUTIOUS, -1,
                dissent="コア保有との重複が高く、今はWatch継続が妥当。"),
    ]
    return _res("ITA", FinalSignal.BUY_CANDIDATE, members=members)


def test_board_summary_lines_structure():
    lines = board_summary_lines(_candidate_with_board())
    joined = "\n".join(lines)
    assert lines[0] == "ボード要約:"
    assert "賛成: Howard Marks" in joined
    assert "反対: Rob Arnott" in joined
    assert "AI監査: Core AI Auditor" in joined


def test_board_summary_falls_back_to_aggregates_without_members():
    # No member_outputs → use positive_reasons / dissenting_views
    r = _res("ITA", FinalSignal.BUY_CANDIDATE)
    r.dissenting_views = ["rob_arnott: 割高で見送り妥当"]
    lines = board_summary_lines(r)
    assert any("賛成:" in l for l in lines)
    assert any("反対:" in l for l in lines)


def test_digest_includes_board_summary_for_candidates():
    text = build_signal_digest_text(results=[_candidate_with_board()])
    assert "ボード要約:" in text
    assert "Howard Marks" in text
    assert "Core AI Auditor" in text


def test_digest_board_summary_no_forbidden_words_and_safety():
    text = build_signal_digest_text(results=[_candidate_with_board()])
    for w in FORBIDDEN_WORDS:
        assert w not in text, f"forbidden word {w!r} in digest"
    assert "自動売買なし" in text
    assert "注文数量計算なし" in text


def test_digest_zero_candidates_unchanged():
    text = build_signal_digest_text(results=[])
    assert "ボード要約:" not in text
    assert "通常監視継続" in text or "シグナルなし" in text


# ── 3. button-aware delivery branch ───────────────────────────────────────────

def test_deliver_with_bot_token_and_channel_posts_buttons(monkeypatch):
    import src.slack_publish as sp
    monkeypatch.setattr(sp, "bot_token_available", lambda: True)
    monkeypatch.setattr(sp, "_post_via_bot", lambda text, blocks, channel: True)

    res = deliver_signal_digest(
        [_res("ITA", FinalSignal.BUY_CANDIDATE)], "digest", channel_id="C123"
    )
    assert res["delivery"] == "bot"
    assert res["buttons"] is True
    assert res["candidates"] == 1


def test_deliver_without_bot_token_falls_back_to_webhook(monkeypatch):
    import src.slack_publish as sp
    import src.slack_client as sc
    monkeypatch.setattr(sp, "bot_token_available", lambda: False)
    monkeypatch.setattr(sc, "post_to_slack", lambda text: True)

    res = deliver_signal_digest(
        [_res("ITA", FinalSignal.BUY_CANDIDATE)], "digest", channel_id="C123"
    )
    assert res["delivery"] == "webhook"
    assert res["buttons"] is False
    assert res["candidates"] == 1


def test_deliver_zero_candidates_uses_plain_webhook(monkeypatch):
    import src.slack_client as sc
    monkeypatch.setattr(sc, "post_to_slack", lambda text: True)

    res = deliver_signal_digest([_res("SPY", FinalSignal.NO_ACTION)], "digest")
    assert res["delivery"] == "webhook"
    assert res["buttons"] is False
    assert res["candidates"] == 0


def test_candidate_items_for_blocks_filters_non_candidates():
    items = candidate_items_for_blocks([
        _res("ITA", FinalSignal.BUY_CANDIDATE),
        _res("XLU", FinalSignal.HIGH_PRIORITY_CANDIDATE),
        _res("SPY", FinalSignal.NO_ACTION),
        _res("PAVE", FinalSignal.WATCH),
    ])
    assert {i["ticker"] for i in items} == {"ITA", "XLU"}


# ── 4. button-path human decision record ──────────────────────────────────────

def test_record_create_if_missing_creates_entry(tmp_path):
    wl = tmp_path / "watchlist.csv"
    wl.write_text(",".join(WATCHLIST_COLUMNS) + "\n", encoding="utf-8")  # header only
    log = tmp_path / "log.jsonl"

    result = record_human_signal_decision(
        "NEWSYM", "USER_APPROVED", "候補として確認",
        watchlist_path=wl, log_path=log, archive_dir=tmp_path / "archive",
        create_if_missing=True,
    )
    assert result["created_new_entry"] is True
    assert result["new_status"] == "USER_APPROVED"
    rows = load_watchlist(wl)
    assert any(r["ticker"] == "NEWSYM" and r["status"] == "USER_APPROVED" for r in rows)


def test_record_missing_still_raises_without_flag(tmp_path):
    wl = tmp_path / "watchlist.csv"
    wl.write_text(",".join(WATCHLIST_COLUMNS) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="not found"):
        record_human_signal_decision(
            "NEWSYM", "USER_APPROVED",
            watchlist_path=wl, log_path=tmp_path / "log.jsonl",
            archive_dir=tmp_path / "archive",
        )


def _seed_wl(path, rows):
    import csv
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=WATCHLIST_COLUMNS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def test_record_protect_human_locked_does_not_overwrite(tmp_path):
    wl = tmp_path / "watchlist.csv"
    log = tmp_path / "log.jsonl"
    _seed_wl(wl, [_wrow("ITA", "USER_APPROVED")])

    result = record_human_signal_decision(
        "ITA", "USER_REJECTED",
        watchlist_path=wl, log_path=log, archive_dir=tmp_path / "archive",
        protect_human_locked=True,
    )
    assert result["human_locked"] is True
    assert result["new_status"] == "USER_APPROVED"  # unchanged
    rows = load_watchlist(wl)
    assert next(r for r in rows if r["ticker"] == "ITA")["status"] == "USER_APPROVED"


def test_record_log_has_safety_flags(tmp_path):
    wl = tmp_path / "watchlist.csv"
    log = tmp_path / "log.jsonl"
    _seed_wl(wl, [_wrow("ITA", "BUY_CANDIDATE")])

    record_human_signal_decision(
        "ITA", "USER_APPROVED",
        watchlist_path=wl, log_path=log, archive_dir=tmp_path / "archive",
        create_if_missing=True, protect_human_locked=True,
    )
    entries = [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines()]
    e = entries[-1]
    assert e["no_auto_trade"] is True
    assert e["no_order_quantity"] is True
    assert e["final_decision_by_human"] is True
