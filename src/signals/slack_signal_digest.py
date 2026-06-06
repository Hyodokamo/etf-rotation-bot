"""Phase 5.4: Slack Signal Digest for Crash Signal results.

Formats Crash Signal check results as a Slack-ready advisory digest.
Framing: candidacy notification only — NOT a buy order or trade instruction.

Safety invariants (always enforced):
- No order quantity calculation
- No auto-trade
- No brokerage integration
- USER_APPROVED can only be set by a human via signal_review; Slack posting never sets it
- watchlist.csv never modified by this module
- signal_history.csv never modified by this module
- ai_sleeve_state.csv / etf_master.csv never modified
"""
from __future__ import annotations

from datetime import date

from src.logger import logger

FORBIDDEN_WORDS = ["買え", "売れ", "注文実行", "購入実行", "売却実行", "自動売買実行"]

DEFAULT_REFERENCE_SYMBOLS: tuple[str, ...] = ("SPY", "QQQ", "SOXX", "SMH")

_DISPLAY_LABEL: dict[str, str] = {
    "HIGH_PRIORITY_CANDIDATE": "優先確認候補",
    "BUY_CANDIDATE": "買い候補条件を満たした",
    "WATCH": "WATCH監視中",
    "HOLD_OFF": "様子見（HOLD_OFF）",
    "REJECT_FOR_NOW": "除外（REJECT）",
    "NO_ACTION": "シグナルなし",
    "USER_APPROVED": "候補として確認済み",
    "USER_REJECTED": "人間が却下済み",
}

_SAFETY_FOOTER = (
    "自動売買なし。注文数量計算なし。証券口座連携なし。最終判断は人間。"
)


def _is_reference_only_result(result) -> bool:
    """True if the result was marked as a market_reference symbol."""
    return any("reference_symbol_not_candidate" in f for f in result.risk_flags)


def _short_reason(result) -> str:
    if result.trigger_labels:
        return result.trigger_labels[0][:48]
    if result.positive_reasons:
        return result.positive_reasons[0][:48]
    if result.risk_flags:
        return result.risk_flags[0][:48]
    return f"score={result.total_score:+d}, conf={result.confidence:.0%}"


def _check_forbidden(text: str) -> None:
    for word in FORBIDDEN_WORDS:
        if word in text:
            logger.warning(f"Signal Digest contains forbidden word: '{word}'")


def _macro_line(market_data: dict[str, dict]) -> str | None:
    """Extract VIX / US10Y / DXY from any available market data row."""
    for sym in ("SPY", "QQQ", "SOXX", "SMH", "ITA"):
        row = market_data.get(sym, {})
        if not row:
            continue
        vix = row.get("vix_level", "")
        us10y = row.get("us10y_yield", "")
        dxy = row.get("dxy_level", "")
        parts = []
        if vix:
            parts.append(f"VIX: {vix}")
        if us10y:
            parts.append(f"US10Y: {us10y}%")
        if dxy:
            parts.append(f"DXY: {dxy}")
        if parts:
            return " / ".join(parts)
    return None


def build_signal_digest_text(
    results: list,
    watchlist_items: list[dict] | None = None,
    market_data: dict[str, dict] | None = None,
    reference_symbols: list[str] | None = None,
    as_of_date: str | None = None,
    committee_target_count: int = 0,
    skipped_count: int = 0,
    global_only_skipped_count: int = 0,
    global_triggers: list[str] | None = None,
) -> str:
    """Build a Slack-ready Signal Digest from Crash Signal results.

    Framing: candidacy notification only. Not a buy order.
    No forbidden order words. No order quantity. No auto-trade.

    Args:
        results: list of SignalResult from aggregate_signal().
        watchlist_items: current watchlist rows (for USER_APPROVED / USER_REJECTED display).
        market_data: {symbol: row_dict} for macro values (VIX/US10Y/DXY).
        reference_symbols: symbols that are market_reference (default: SPY/QQQ/SOXX/SMH).
        as_of_date: display date string (YYYY-MM-DD).
        committee_target_count: number of symbols that had LLM committee run today.
        skipped_count: number of symbols skipped (no triggers at all).
        global_only_skipped_count: symbols skipped because only global market triggers fired.
        global_triggers: global market trigger labels that fired today (e.g. SPY急落).

    Returns:
        Slack mrkdwn-ready digest string. Never empty.
    """
    from src.signals.signal_models import FinalSignal

    today = as_of_date or date.today().isoformat()
    market_data = market_data or {}
    watchlist_items = watchlist_items or []

    # explicit reference symbols + auto-detected from risk_flags
    ref_syms: set[str] = set(s.upper() for s in (reference_symbols or DEFAULT_REFERENCE_SYMBOLS))
    for r in results:
        if _is_reference_only_result(r):
            ref_syms.add(r.symbol.upper())

    # Split into reference-only vs candidate results
    cand_results = [r for r in results if not _is_reference_only_result(r)]

    high_priority = [r for r in cand_results if r.final_signal == FinalSignal.HIGH_PRIORITY_CANDIDATE]
    buy_candidates = [r for r in cand_results if r.final_signal == FinalSignal.BUY_CANDIDATE]
    watch_list = [r for r in cand_results if r.final_signal == FinalSignal.WATCH]
    hold_off = [r for r in cand_results if r.final_signal == FinalSignal.HOLD_OFF]
    rejects = [r for r in cand_results if r.final_signal == FinalSignal.REJECT_FOR_NOW]

    # Human decisions from watchlist
    approved = [row for row in watchlist_items if row.get("status") == "USER_APPROVED"]
    rejected_by_human = [row for row in watchlist_items if row.get("status") == "USER_REJECTED"]

    lines: list[str] = []

    # ── Header ────────────────────────────────────────────────────────────────
    lines.append(f"*AI検証枠 Signal Digest — {today}*")
    lines.append("")

    # ── Committee execution summary (Phase 6.1 / 6.1.1) ──────────────────────
    total_processed = committee_target_count + skipped_count + global_only_skipped_count
    if total_processed > 0:
        lines.append(
            f"本日Committee実行対象: {committee_target_count}件 / "
            f"グローバルのみスキップ: {global_only_skipped_count}件 / "
            f"トリガーなしスキップ: {skipped_count}件"
        )
        if global_triggers:
            gt_str = " / ".join(global_triggers[:5])
            lines.append(f"グローバルトリガー: {gt_str}")
        lines.append("")

    # ── Summary conclusion ────────────────────────────────────────────────────
    lines.append("*本日の結論:*")
    if high_priority or buy_candidates:
        lines.append("候補あり。ただし買付指示ではありません。")
    elif watch_list or hold_off:
        lines.append("注目シンボルあり。買い候補条件は未達。監視継続。")
    else:
        lines.append("急落シグナルなし。通常監視継続。")
    lines.append("")

    # ── HIGH_PRIORITY_CANDIDATE ────────────────────────────────────────────────
    if high_priority:
        lines.append("*優先確認候補（HIGH_PRIORITY_CANDIDATE）:*")
        for r in high_priority:
            reason = _short_reason(r)
            conf = f"{r.confidence:.0%}"
            nrd = getattr(r, "next_review_date", None) or ""
            lines.append(f"- *{r.symbol}*: {reason} （conf={conf}）{' 次回=' + nrd if nrd else ''}")
        lines.append("")

    # ── BUY_CANDIDATE ─────────────────────────────────────────────────────────
    if buy_candidates:
        lines.append("*買い候補条件を満たした（BUY_CANDIDATE）:*")
        for r in buy_candidates:
            reason = _short_reason(r)
            conf = f"{r.confidence:.0%}"
            nrd = getattr(r, "next_review_date", None) or ""
            lines.append(f"- *{r.symbol}*: {reason} （conf={conf}）{' 次回=' + nrd if nrd else ''}")
        lines.append("")

    # ── WATCH / HOLD_OFF ──────────────────────────────────────────────────────
    watch_and_hold = watch_list + hold_off
    if watch_and_hold:
        lines.append("*監視継続（WATCH / HOLD_OFF）:*")
        for r in watch_and_hold:
            label = "WATCH" if r.final_signal == FinalSignal.WATCH else "HOLD_OFF"
            reason = _short_reason(r)
            lines.append(f"- {r.symbol} [{label}]: {reason}")
        lines.append("")

    # ── REJECT_FOR_NOW (brief, only significant ones) ─────────────────────────
    sig_rejects = [r for r in rejects if r.risk_flags or r.veto_flags]
    if sig_rejects:
        lines.append("*今回除外（REJECT_FOR_NOW）:*")
        for r in sig_rejects[:3]:
            flag = (r.risk_flags + r.veto_flags + ["重大リスクあり"])[0][:60]
            lines.append(f"- {r.symbol}: {flag}")
        lines.append("")

    # ── Reference indicators ──────────────────────────────────────────────────
    lines.append("*参照指標（市場判定用）:*")
    ref_sym_str = " / ".join(sorted(ref_syms)) if ref_syms else "SPY / QQQ / SOXX / SMH"
    lines.append(f"{ref_sym_str} は市場レジーム・ストレス判定用シンボルです。")
    lines.append("これらは買い候補化対象ではありません。")
    macro = _macro_line(market_data)
    if macro:
        lines.append(macro)
    lines.append("")

    # ── Human decision state ──────────────────────────────────────────────────
    if approved or rejected_by_human:
        lines.append("*人間判断の状態:*")
        for row in approved:
            ticker = row.get("ticker", "")
            nrd = row.get("next_review_date", "")
            lines.append(
                f"- {ticker}: 候補として確認済み（USER_APPROVED）"
                + (f" 次回確認={nrd}" if nrd else "")
            )
        for row in rejected_by_human:
            ticker = row.get("ticker", "")
            lines.append(f"- {ticker}: 人間が却下済み（USER_REJECTED）")
        lines.append("")

    # ── Safety footer ─────────────────────────────────────────────────────────
    lines.append("*安全注記:*")
    lines.append(_SAFETY_FOOTER)

    text = "\n".join(lines)
    _check_forbidden(text)
    return text


def build_due_slack_section(due_items: list) -> str:
    """Build a Slack mrkdwn section for review-due items (Phase 5.5).

    Args:
        due_items: list[ReviewDueItem] from signal_review_due.classify_watchlist().

    Returns empty string if no due items.
    Groups by DueCategory: overdue, due_today, due_soon, locked.
    """
    if not due_items:
        return ""

    from src.signals.signal_review_due import DueCategory

    _SLACK_SECTION_TITLES = {
        DueCategory.OVERDUE: "期限超過（overdue）",
        DueCategory.DUE_TODAY: "本日確認（due_today）",
        DueCategory.DUE_SOON: "近日確認（due_soon）",
        DueCategory.NO_DATE: "確認日未設定",
        DueCategory.LOCKED: "人間判断済み locked",
        DueCategory.ON_SCHEDULE: "スケジュール通り",
    }

    # Show categories in priority order, skip ON_SCHEDULE (not actionable)
    show_cats = [
        DueCategory.OVERDUE,
        DueCategory.DUE_TODAY,
        DueCategory.DUE_SOON,
        DueCategory.LOCKED,
        DueCategory.NO_DATE,
    ]

    grouped: dict = {}
    for item in due_items:
        grouped.setdefault(item.due_category, []).append(item)

    lines: list[str] = []
    for cat in show_cats:
        cat_items = grouped.get(cat, [])
        if not cat_items:
            continue
        section_title = _SLACK_SECTION_TITLES.get(cat, str(cat))
        lines.append(f"*{section_title}:*")
        for item in cat_items:
            lock_mark = " [locked]" if item.is_locked else ""
            nrd = item.next_review_date or "(未設定)"
            lines.append(
                f"- *{item.ticker}* [{item.status}{lock_mark}]"
                f" {nrd} — {item.recommendation}"
            )
        lines.append("")

    return "\n".join(lines)


def build_review_slack_digest(
    items: list[dict],
    due_items: list | None = None,
    as_of_date: str | None = None,
) -> str:
    """Build a Slack-ready Signal Review digest from watchlist items.

    Shows current watchlist status including human decisions.
    When due_items (list[ReviewDueItem]) is provided, adds due sections
    (期限超過 / 本日確認 / 近日確認 / 人間判断済み locked).

    No order execution language. No order quantity.
    """
    today = as_of_date or date.today().isoformat()
    lines: list[str] = []

    lines.append(f"*AI検証枠 Signal Review — {today}*")
    lines.append("")

    if not items:
        lines.append("レビュー対象のシグナルはありません。")
        lines.append("")
    else:
        lines.append(f"Watchlistアイテム: {len(items)}件")
        lines.append("")
        for row in items:
            ticker = row.get("ticker", "")
            status = row.get("status", "")
            conf = row.get("confidence", "")
            nrd = row.get("next_review_date", "")
            label = _status_label(status)
            conf_part = f" conf={conf}" if conf else ""
            nrd_part = f" 次回確認={nrd}" if nrd else ""
            lines.append(f"- *{ticker}* [{label}]{conf_part}{nrd_part}")
        lines.append("")

    # Phase 5.5: due sections
    if due_items:
        due_section = build_due_slack_section(due_items)
        if due_section:
            lines.append(due_section)

    lines.append("*安全注記:*")
    lines.append(_SAFETY_FOOTER)

    text = "\n".join(lines)
    _check_forbidden(text)
    return text


def _status_label(status: str) -> str:
    return _DISPLAY_LABEL.get(status, status)


def post_signal_digest(text: str) -> bool:
    """Post Signal Digest to Slack via Incoming Webhook.

    Returns True on success, False if webhook URL is missing or call fails.
    Never raises. No order execution. No brokerage call.
    watchlist.csv / signal_history.csv never modified.
    """
    try:
        from src.slack_client import post_to_slack
        return post_to_slack(text)
    except Exception as e:
        logger.error(f"Signal Digest Slack post failed: {e}")
        return False
