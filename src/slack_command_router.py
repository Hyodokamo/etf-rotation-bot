"""Phase 7.1: Slack Command Router — read-only /etf slash commands.

Dispatches /etf subcommands to whitelist-only Python functions.
No shell execution, no subprocess, no eval, no exec.
No file writes: never updates watchlist.csv / ai_sleeve_state.csv /
etf_master.csv / signal_history.csv / any log file.

Supported subcommands:
  help     — usage and command list
  status   — AI検証枠 state summary (watchlist counts + last scheduler run)
  signals  — latest Signal Digest (read-only)
  overdue  — watchlist items overdue / due today / due soon
  review   — full watchlist review list

Security design:
  - SLACK_ALLOWED_USER_IDS allowlist enforced before any dispatch
  - Subcommand whitelist dict (_COMMAND_HANDLERS); unknown → help
  - No API key / token / secret / prompt / raw_response in responses
  - _FORBIDDEN_WORDS guard on every CommandResult
  - Responses are always ephemeral (only the requester sees them)

Advisory only: no auto-trade, no order quantity, no brokerage, no NISA automation.
USER_APPROVED = 候補として確認済み (NOT a buy order).
Final decisions always by a human.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone, timedelta
from pathlib import Path

_JST = timezone(timedelta(hours=9))
_STALE_HOURS = 24

from src.logger import logger

_SAFETY_NOTICE = (
    "自動売買なし / 注文数量計算なし / 証券口座連携なし。最終判断は人間が行います。"
)

_FORBIDDEN_WORDS = ["買え", "売れ", "注文実行", "購入実行", "売却実行", "自動売買実行"]

_SENSITIVE_SUBSTRINGS = (
    "api_key", "apikey", "secret", "password", "token",
    "prompt", "raw_response", "signing", "credential",
)

# Ticker validation: 1-12 chars, uppercase alphanumeric + ^ . -
_VALID_TICKER_RE = re.compile(r"^[A-Z0-9^.\-]{1,12}$")
# Job ID validation: UUID (36 chars) or short prefix (8 chars minimum)
_VALID_JOB_ID_RE = re.compile(r"^[0-9a-f\-]{8,36}$")

_STATUS_LABEL: dict[str, str] = {
    "BUY_CANDIDATE":           "買い候補条件を満たした",
    "HIGH_PRIORITY_CANDIDATE": "優先確認候補",
    "USER_APPROVED":           "候補として確認済み",
    "USER_REJECTED":           "人間が却下済み",
    "WATCH":                   "WATCH監視中",
    "HOLD_OFF":                "様子見（HOLD_OFF）",
    "REJECT_FOR_NOW":          "除外（REJECT）",
    "NO_ACTION":               "シグナルなし",
}


# ── Result type ───────────────────────────────────────────────────────────────

@dataclass
class CommandResult:
    ok: bool
    text: str
    ephemeral: bool = True                  # always ephemeral (only the requestor sees it)
    blocks: list | None = field(default=None, compare=False)  # Block Kit (interactive buttons)

    def __post_init__(self) -> None:
        _guard_forbidden(self.text)
        _guard_sensitive(self.text)


def _guard_forbidden(text: str) -> None:
    for word in _FORBIDDEN_WORDS:
        if word in text:
            logger.warning(f"[command_router] forbidden word in response: {word!r}")


def _guard_sensitive(text: str) -> None:
    lower = text.lower()
    for sub in _SENSITIVE_SUBSTRINGS:
        if sub in lower:
            logger.warning(f"[command_router] possible sensitive substring in response: {sub!r}")


# ── Individual command handlers (all read-only; never write files) ─────────────

def _cmd_help(**_) -> str:
    return (
        "*ETF Rotation Bot — /etf コマンド一覧*\n"
        "\n"
        "読取コマンド（即時応答）:\n"
        "  • `/etf help`                — このヘルプを表示\n"
        "  • `/etf status`              — AI検証枠の現在状態\n"
        "  • `/etf signals`             — 最新シグナルダイジェスト\n"
        "  • `/etf overdue`             — 確認期限超過・本日確認・近日確認\n"
        "  • `/etf review`              — Watchlist一覧（インタラクティブボタン付き）\n"
        "\n"
        "非同期ジョブコマンド（キューに追加 → 別プロセスが実行）:\n"
        "  • `/etf candidate SYMBOL`    — 候補レビュー実行 (dry-run)\n"
        "  • `/etf signal SYMBOL`       — 個別シグナル確認 (dry-run)\n"
        "  • `/etf update-market-data`  — market data更新\n"
        "  • `/etf run daily`           — 日次シグナルチェック (--no-slack, skip-market-data)\n"
        "  • `/etf job JOB_ID`          — ジョブの実行状態確認\n"
        "\n"
        "注意: キュー実行には job_runner プロセスが起動している必要があります。\n"
        "      --allow-watchlist-update はSlackから実行不可。\n"
        "\n"
        + _SAFETY_NOTICE
    )


def _cmd_status(
    *,
    watchlist_path: str = "data/watchlist.csv",
    scheduler_log_path: str = "logs/scheduler_run_log.jsonl",
    **_,
) -> str:
    """AI検証枠 status summary — read-only, no file writes."""
    from src.signals.watchlist_store import load_watchlist

    lines: list[str] = ["*AI検証枠 — 現在状態*", ""]

    # Watchlist counts by status (load_watchlist is read-only)
    try:
        rows = load_watchlist(watchlist_path)
        counts: dict[str, int] = {}
        for r in rows:
            s = r.get("status", "UNKNOWN")
            counts[s] = counts.get(s, 0) + 1

        lines.append(f"Watchlistアイテム: {len(rows)}件")
        for status, cnt in sorted(counts.items(), key=lambda x: -x[1]):
            label = _STATUS_LABEL.get(status, status)
            lines.append(f"  • {label}: {cnt}件")
    except FileNotFoundError:
        lines.append("（watchlist.csv が見つかりません）")
    except Exception as exc:
        logger.warning(f"[cmd_status] watchlist read error: {exc}")
        lines.append("（watchlist 読取エラー）")

    lines.append("")

    # Latest scheduler run summary (read-only)
    try:
        raw_lines = Path(scheduler_log_path).read_text(encoding="utf-8").splitlines()
        non_empty = [l for l in raw_lines if l.strip()]
        if non_empty:
            last = json.loads(non_empty[-1])
            status_val  = last.get("status", "UNKNOWN")
            started_at  = (last.get("started_at") or "")[:16]
            target      = last.get("committee_target_count", 0)
            global_skip = last.get("global_only_skipped_count", 0)
            no_trade    = last.get("no_auto_trade", True)
            no_qty      = last.get("no_order_quantity", True)
            lines.append(f"直近実行: {started_at} — {status_val}")
            lines.append(f"  Committee実行: {target}件 / グローバルのみスキップ: {global_skip}件")
            lines.append(f"  no_auto_trade={no_trade} / no_order_quantity={no_qty}")
        else:
            lines.append("（scheduler_run_log.jsonl が空です）")
    except FileNotFoundError:
        lines.append("（scheduler_run_log.jsonl が見つかりません。先に daily run を実行してください）")
    except Exception as exc:
        logger.warning(f"[cmd_status] scheduler log read error: {exc}")
        lines.append("（scheduler log 読取エラー）")

    lines.append("")
    lines.append(_SAFETY_NOTICE)
    return "\n".join(lines)


def _report_freshness(report_path: str) -> tuple[str, bool]:
    """Return (display_line, is_stale) based on the report file's mtime.

    is_stale = True when the file is older than _STALE_HOURS hours.
    Returns ("", False) on any error.
    """
    try:
        mtime = Path(report_path).stat().st_mtime
        dt = datetime.fromtimestamp(mtime, tz=_JST)
        display = f"Signal report: {dt.strftime('%Y-%m-%d %H:%M')} (JST) 生成"
        is_stale = (datetime.now(_JST) - dt).total_seconds() > _STALE_HOURS * 3600
        return display, is_stale
    except Exception:
        return "", False


def _cmd_signals(
    *,
    watchlist_path: str = "data/watchlist.csv",
    signal_report_path: str = "reports/daily_signal_report.md",
    **_,
) -> str:
    """Latest signal digest — read-only, no file writes, no daily run triggered."""
    # Report not found: return a safe message (no fallback run, no file generation)
    try:
        report = Path(signal_report_path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return (
            "Signal report がまだ生成されていません。\n"
            "先に `python scripts/daily_signal_check.py --no-slack` を実行してください。\n"
            + _SAFETY_NOTICE
        )
    except Exception as exc:
        logger.warning(f"[cmd_signals] report read error: {exc}")
        return "（シグナル情報読取中にエラーが発生しました）\n" + _SAFETY_NOTICE

    # Freshness display
    freshness_line, is_stale = _report_freshness(signal_report_path)

    # Trim to Slack's practical limit (~2600 chars before footer)
    if len(report) > 2600:
        report = report[:2600] + "\n…（詳細は reports/daily_signal_report.md 参照）"

    footer_parts: list[str] = []
    if freshness_line:
        footer_parts.append(f"_{freshness_line}_")
    if is_stale:
        footer_parts.append(
            "最新レポートではない可能性があります。"
            "必要なら daily run を実行してください。"
        )
    footer = "\n" + "\n".join(footer_parts) if footer_parts else ""
    return report.rstrip() + footer + "\n\n" + _SAFETY_NOTICE


def _cmd_overdue(
    *,
    watchlist_path: str = "data/watchlist.csv",
    **_,
) -> str:
    """Overdue / due-today / due-soon items — read-only, no file writes."""
    from src.signals.signal_review_due import DueCategory, load_and_classify
    from src.signals.slack_signal_digest import build_due_slack_section

    try:
        due_cats = [DueCategory.OVERDUE, DueCategory.DUE_TODAY, DueCategory.DUE_SOON]
        items = load_and_classify(watchlist_path, due_categories=due_cats)
    except FileNotFoundError:
        return (
            "（watchlist.csv が見つかりません）\n"
            "先に `python scripts/daily_signal_check.py --no-slack` を実行してください。\n"
            + _SAFETY_NOTICE
        )
    except Exception as exc:
        logger.warning(f"[cmd_overdue] error: {exc}")
        return "（期限確認中にエラーが発生しました）\n" + _SAFETY_NOTICE

    header = f"*Watchlist 確認期限 — {date.today().isoformat()}*\n\n"
    if not items:
        return header + "（期限超過・本日確認・近日確認のアイテムはありません）\n" + _SAFETY_NOTICE

    section = build_due_slack_section(items)
    return header + (section or "（なし）") + "\n" + _SAFETY_NOTICE


def _cmd_review(
    *,
    watchlist_path: str = "data/watchlist.csv",
    **_,
) -> "CommandResult | str":
    """Watchlist review list with interactive decision buttons — read-only, no file writes."""
    from src.signals.slack_signal_digest import build_review_slack_digest
    from src.signals.signal_review import load_watchlist_review_items
    from src.slack_signal_actions import build_signal_review_blocks

    try:
        items = load_watchlist_review_items(watchlist_path)
        text = build_review_slack_digest(items, as_of_date=date.today().isoformat())
        blocks = build_signal_review_blocks(items)
        return CommandResult(ok=True, text=text, blocks=blocks)
    except FileNotFoundError:
        return (
            "（watchlist.csv が見つかりません）\n"
            "先に `python scripts/daily_signal_check.py --no-slack` を実行してください。\n"
            + _SAFETY_NOTICE
        )
    except Exception as exc:
        logger.warning(f"[cmd_review] error: {exc}")
        return "（Watchlist 読取中にエラーが発生しました）\n" + _SAFETY_NOTICE


def _cmd_candidate(
    *,
    user_id: str = "",
    parts: list[str] | None = None,
    job_queue_path: str = "logs/job_queue.jsonl",
    job_status_path: str = "logs/job_status.jsonl",
    **_,
) -> str:
    """Enqueue a candidate_review job (dry-run) — never allow-watchlist-update."""
    from src.job_store import MARKET_REFERENCE_SYMBOLS, enqueue_job

    if not parts or len(parts) < 2:
        return (
            "使い方: `/etf candidate SYMBOL`\n"
            "例: `/etf candidate ITA`\n"
            "\n" + _SAFETY_NOTICE
        )
    raw_symbol = parts[1].strip().upper()
    if not _VALID_TICKER_RE.match(raw_symbol):
        return (
            f"無効なシンボル: {raw_symbol!r}\n"
            "シンボルは 1-12 文字の英大文字・数字・^ . - で入力してください。\n"
            + _SAFETY_NOTICE
        )
    if raw_symbol in MARKET_REFERENCE_SYMBOLS:
        return (
            f"`{raw_symbol}` はマーケットリファレンスシンボルのため"
            "候補レビューの対象外です。\n"
            + _SAFETY_NOTICE
        )
    try:
        job = enqueue_job(
            "candidate_review", user_id,
            args={"symbol": raw_symbol},
            queue_path=job_queue_path,
            status_path=job_status_path,
        )
    except Exception as exc:
        logger.error(f"[cmd_candidate] enqueue error: {exc}")
        return f"ジョブのキュー追加に失敗しました: {exc}\n" + _SAFETY_NOTICE

    job_id_short = job["job_id"][:8]
    status = job.get("status", "QUEUED")
    verb = "キューに追加しました" if status == "QUEUED" else f"既存ジョブ ({status})"
    return (
        f"candidate_review ジョブを{verb}: `{raw_symbol}`\n"
        f"Job ID: `{job['job_id']}`\n"
        f"ステータス確認: `/etf job {job_id_short}`\n"
        "dry-run モード / watchlist自動更新なし\n"
        "\n" + _SAFETY_NOTICE
    )


def _cmd_signal(
    *,
    user_id: str = "",
    parts: list[str] | None = None,
    job_queue_path: str = "logs/job_queue.jsonl",
    job_status_path: str = "logs/job_status.jsonl",
    **_,
) -> str:
    """Enqueue a signal_check job (dry-run) for a specific symbol."""
    from src.job_store import enqueue_job

    if not parts or len(parts) < 2:
        return (
            "使い方: `/etf signal SYMBOL`\n"
            "例: `/etf signal ITA`\n"
            "\n" + _SAFETY_NOTICE
        )
    raw_symbol = parts[1].strip().upper()
    if not _VALID_TICKER_RE.match(raw_symbol):
        return (
            f"無効なシンボル: {raw_symbol!r}\n"
            "シンボルは 1-12 文字の英大文字・数字・^ . - で入力してください。\n"
            + _SAFETY_NOTICE
        )
    try:
        job = enqueue_job(
            "signal_check", user_id,
            args={"symbol": raw_symbol},
            queue_path=job_queue_path,
            status_path=job_status_path,
        )
    except Exception as exc:
        logger.error(f"[cmd_signal] enqueue error: {exc}")
        return f"ジョブのキュー追加に失敗しました: {exc}\n" + _SAFETY_NOTICE

    job_id_short = job["job_id"][:8]
    status = job.get("status", "QUEUED")
    verb = "キューに追加しました" if status == "QUEUED" else f"既存ジョブ ({status})"
    return (
        f"signal_check ジョブを{verb}: `{raw_symbol}`\n"
        f"Job ID: `{job['job_id']}`\n"
        f"ステータス確認: `/etf job {job_id_short}`\n"
        "dry-run モード\n"
        "\n" + _SAFETY_NOTICE
    )


def _cmd_update_market_data(
    *,
    user_id: str = "",
    job_queue_path: str = "logs/job_queue.jsonl",
    job_status_path: str = "logs/job_status.jsonl",
    **_,
) -> str:
    """Enqueue an update_market_data job."""
    from src.job_store import enqueue_job

    try:
        job = enqueue_job(
            "update_market_data", user_id,
            args={},
            queue_path=job_queue_path,
            status_path=job_status_path,
        )
    except Exception as exc:
        logger.error(f"[cmd_update_market_data] enqueue error: {exc}")
        return f"ジョブのキュー追加に失敗しました: {exc}\n" + _SAFETY_NOTICE

    job_id_short = job["job_id"][:8]
    status = job.get("status", "QUEUED")
    verb = "キューに追加しました" if status == "QUEUED" else f"既存ジョブ ({status})"
    return (
        f"update_market_data ジョブを{verb}。\n"
        f"Job ID: `{job['job_id']}`\n"
        f"ステータス確認: `/etf job {job_id_short}`\n"
        "\n" + _SAFETY_NOTICE
    )


def _cmd_run(
    *,
    user_id: str = "",
    parts: list[str] | None = None,
    job_queue_path: str = "logs/job_queue.jsonl",
    job_status_path: str = "logs/job_status.jsonl",
    **_,
) -> str:
    """Enqueue a daily_signal_check job (--no-slack, --skip-market-data).

    Only 'daily' is a valid argument. --allow-watchlist-update is NEVER passed.
    """
    from src.job_store import enqueue_job

    subarg = (parts[1].lower() if parts and len(parts) >= 2 else "").strip()
    if subarg != "daily":
        return (
            "使い方: `/etf run daily`\n"
            "現在は `daily` のみサポートしています。\n"
            "--allow-watchlist-update は Slack から実行不可です。\n"
            "\n" + _SAFETY_NOTICE
        )
    try:
        job = enqueue_job(
            "daily_signal_check", user_id,
            args={},
            queue_path=job_queue_path,
            status_path=job_status_path,
        )
    except Exception as exc:
        logger.error(f"[cmd_run] enqueue error: {exc}")
        return f"ジョブのキュー追加に失敗しました: {exc}\n" + _SAFETY_NOTICE

    job_id_short = job["job_id"][:8]
    status = job.get("status", "QUEUED")
    verb = "キューに追加しました" if status == "QUEUED" else f"既存ジョブ ({status})"
    return (
        f"daily_signal_check ジョブを{verb}。\n"
        f"Job ID: `{job['job_id']}`\n"
        f"ステータス確認: `/etf job {job_id_short}`\n"
        "Slackなし / market-dataスキップ / watchlist自動更新なし\n"
        "\n" + _SAFETY_NOTICE
    )


def _cmd_job(
    *,
    parts: list[str] | None = None,
    job_queue_path: str = "logs/job_queue.jsonl",
    job_status_path: str = "logs/job_status.jsonl",
    **_,
) -> str:
    """Show the current status of a job by its Job ID (or 8-char prefix)."""
    from src.job_store import get_job_status, _build_job_map, _read_log

    if not parts or len(parts) < 2:
        return (
            "使い方: `/etf job JOB_ID`\n"
            "例: `/etf job a1b2c3d4`\n"
            "\n" + _SAFETY_NOTICE
        )
    raw_id = parts[1].strip().lower()
    if not _VALID_JOB_ID_RE.match(raw_id):
        return (
            f"無効なJob ID: {parts[1]!r}\n"
            "Job IDは8-36文字の英数字とハイフンで構成されます。\n"
            + _SAFETY_NOTICE
        )

    # Try exact match first (full UUID), then prefix match (8+ chars)
    job = get_job_status(raw_id, queue_path=job_queue_path, status_path=job_status_path)
    if job is None and len(raw_id) >= 8:
        all_jobs = _build_job_map(job_queue_path, job_status_path)
        matches = [j for jid, j in all_jobs.items() if jid.startswith(raw_id)]
        if len(matches) == 1:
            job = matches[0]
        elif len(matches) > 1:
            return (
                f"Job IDプレフィックス `{raw_id}` に複数ジョブがマッチしました。"
                "フルJob IDを指定してください。\n"
                + _SAFETY_NOTICE
            )

    if job is None:
        return (
            f"Job ID `{parts[1]}` が見つかりません。\n"
            "ジョブが古すぎるか、IDが正しくない可能性があります。\n"
            + _SAFETY_NOTICE
        )

    status   = job.get("status", "UNKNOWN")
    jtype    = job.get("job_type", "UNKNOWN")
    symbol   = (job.get("args") or {}).get("symbol", "")
    created  = (job.get("created_at") or "")[:16]
    started  = (job.get("started_at") or "")[:16]
    finished = (job.get("finished_at") or "")[:16]
    result   = job.get("result_summary") or ""
    error    = job.get("error_summary")  or ""

    lines = [
        f"*Job Status: `{job['job_id'][:8]}…`*",
        f"  種別: `{jtype}`" + (f" / シンボル: `{symbol}`" if symbol else ""),
        f"  状態: `{status}`",
        f"  作成: {created}  開始: {started or '—'}  完了: {finished or '—'}",
    ]
    if result:
        snippet = result[:400] + ("…" if len(result) > 400 else "")
        lines.append(f"  結果: {snippet}")
    if error:
        snippet = error[:400] + ("…" if len(error) > 400 else "")
        lines.append(f"  エラー: {snippet}")
    lines.append("")
    lines.append(_SAFETY_NOTICE)
    return "\n".join(lines)


# ── Whitelist dispatch table ───────────────────────────────────────────────────
# ONLY these subcommands are dispatched to Python functions.
# Unknown subcommands return help — never executed.
# No shell invocations, no subprocess, no eval, no exec in this module.

_COMMAND_HANDLERS: dict[str, object] = {
    "help":               _cmd_help,
    "status":             _cmd_status,
    "signals":            _cmd_signals,
    "overdue":            _cmd_overdue,
    "review":             _cmd_review,
    "candidate":          _cmd_candidate,
    "signal":             _cmd_signal,
    "update-market-data": _cmd_update_market_data,
    "run":                _cmd_run,
    "job":                _cmd_job,
}

KNOWN_SUBCOMMANDS: frozenset[str] = frozenset(_COMMAND_HANDLERS)


# ── Public entry point ────────────────────────────────────────────────────────

def handle_etf_command(
    text: str,
    user_id: str,
    *,
    allowed_users: list[str] | None = None,
    watchlist_path: str = "data/watchlist.csv",
    scheduler_log_path: str = "logs/scheduler_run_log.jsonl",
    signal_report_path: str = "reports/daily_signal_report.md",
    job_queue_path: str = "logs/job_queue.jsonl",
    job_status_path: str = "logs/job_status.jsonl",
) -> CommandResult:
    """Route a /etf slash command to the appropriate handler.

    Read-only subcommands (help/status/signals/overdue/review) return results immediately.
    Job subcommands (candidate/signal/update-market-data/run/job) enqueue to the async
    job store and return a job ID — no subprocess is ever called from this function.

    Safety invariants:
        - allowed_users check before any dispatch
        - Only whitelisted subcommands are dispatched (unknown → help)
        - No shell/subprocess/eval/exec anywhere in this module
        - Symbol validated against _VALID_TICKER_RE before enqueue
        - --allow-watchlist-update never passed to any job
        - No API key/token/secret in response text
    """
    # 1. User allowlist — enforced before any processing
    if allowed_users is not None and user_id not in allowed_users:
        logger.warning(f"[command_router] blocked user: {user_id}")
        return CommandResult(ok=False, text="この操作は許可されていません。")

    # 2. Parse subcommand — all parts retained for symbol/arg extraction by handlers
    parts = (text or "").strip().split()
    subcommand = parts[0].lower() if parts else "help"

    # 3. Whitelist dispatch — unknown subcommands silently fall through to help
    handler = _COMMAND_HANDLERS.get(subcommand)
    if handler is None:
        logger.info(f"[command_router] unknown subcommand {subcommand!r} -> help")
        handler = _COMMAND_HANDLERS["help"]

    # 4. Execute handler
    try:
        raw = handler(
            watchlist_path=watchlist_path,
            scheduler_log_path=scheduler_log_path,
            signal_report_path=signal_report_path,
            job_queue_path=job_queue_path,
            job_status_path=job_status_path,
            user_id=user_id,
            parts=parts,
        )
    except Exception as exc:
        logger.error(
            f"[command_router] handler error ({subcommand}): {type(exc).__name__}: {exc}"
        )
        return CommandResult(ok=False, text="コマンド処理中にエラーが発生しました。")

    # Handlers may return CommandResult directly (e.g. _cmd_review with blocks)
    # or a plain str (all other handlers)
    if isinstance(raw, CommandResult):
        return raw
    return CommandResult(ok=True, text=raw)
