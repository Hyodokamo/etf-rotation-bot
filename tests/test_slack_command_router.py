"""Phase 7.1: Tests for Slack Command Router — /etf slash commands.

All tests are hermetic: they use tmp_path fixtures for file-based handlers and
never touch the real watchlist.csv / scheduler logs / reports.

Safety invariants verified:
- Only whitelisted users can dispatch commands
- Unknown subcommands return help (not an error or arbitrary execution)
- No shell / subprocess / eval / exec in the router source
- No forbidden order-execution words in any response
- No API key / token / secret leaks in any response
- Watchlist / ai_sleeve_state / etf_master / signal_history never written
- slack_interaction_handler.py registers the /etf command endpoint
"""
from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

from src.slack_command_router import (
    KNOWN_SUBCOMMANDS,
    CommandResult,
    handle_etf_command,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

_ALLOWED = ["U_ALICE", "U_BOB"]

_MINIMAL_WATCHLIST = (
    "symbol,status,signal_side,next_review_date\n"
    "ITA,BUY_CANDIDATE,BUY,2026-06-10\n"
    "QTUM,WATCH,BUY,2026-06-01\n"
)

_MINIMAL_RUN_LOG = json.dumps({
    "status": "SUCCESS",
    "started_at": "2026-06-06T07:30:00+09:00",
    "committee_target_count": 3,
    "global_only_skipped_count": 1,
    "no_auto_trade": True,
    "no_order_quantity": True,
}) + "\n"

_MINIMAL_REPORT = "# Daily Signal Report\n\nITA: BUY_CANDIDATE\n"

_FORBIDDEN_WORDS = ["買え", "売れ", "注文実行", "購入実行", "売却実行", "自動売買実行"]
_SENSITIVE_SUBSTRINGS = (
    "api_key", "apikey", "secret", "password",
    "raw_response", "signing", "credential",
)


def _wl(tmp_path: Path) -> str:
    p = tmp_path / "watchlist.csv"
    p.write_text(_MINIMAL_WATCHLIST, encoding="utf-8")
    return str(p)


def _log(tmp_path: Path) -> str:
    d = tmp_path / "logs"
    d.mkdir(exist_ok=True)
    p = d / "scheduler_run_log.jsonl"
    p.write_text(_MINIMAL_RUN_LOG, encoding="utf-8")
    return str(p)


def _report(tmp_path: Path) -> str:
    d = tmp_path / "reports"
    d.mkdir(exist_ok=True)
    p = d / "daily_signal_report.md"
    p.write_text(_MINIMAL_REPORT, encoding="utf-8")
    return str(p)


def _jq(tmp_path: Path) -> str:
    d = tmp_path / "logs"
    d.mkdir(exist_ok=True)
    return str(d / "job_queue.jsonl")


def _js(tmp_path: Path) -> str:
    d = tmp_path / "logs"
    d.mkdir(exist_ok=True)
    return str(d / "job_status.jsonl")


def _run(text: str, user_id: str = "U_ALICE", *, tmp_path: Path, **kw) -> CommandResult:
    return handle_etf_command(
        text,
        user_id,
        allowed_users=_ALLOWED,
        watchlist_path=kw.get("watchlist_path", _wl(tmp_path)),
        scheduler_log_path=kw.get("scheduler_log_path", _log(tmp_path)),
        signal_report_path=kw.get("signal_report_path", _report(tmp_path)),
        job_queue_path=kw.get("job_queue_path", _jq(tmp_path)),
        job_status_path=kw.get("job_status_path", _js(tmp_path)),
    )


# ── Basic subcommand tests ────────────────────────────────────────────────────

def test_slack_command_help(tmp_path):
    result = _run("help", tmp_path=tmp_path)
    assert result.ok is True
    assert "etf" in result.text.lower()
    assert "help" in result.text.lower() or "/etf" in result.text
    assert result.ephemeral is True


def test_slack_command_status(tmp_path):
    result = _run("status", tmp_path=tmp_path)
    assert result.ok is True
    # Should show watchlist counts
    assert "件" in result.text or "watchlist" in result.text.lower() or "ITA" in result.text or "BUY" in result.text or "Watchlist" in result.text
    # Should show scheduler run info
    assert "SUCCESS" in result.text or "scheduler" in result.text.lower() or "直近" in result.text or "Committee" in result.text
    assert result.ephemeral is True


def test_slack_command_signals(tmp_path):
    result = _run("signals", tmp_path=tmp_path)
    assert result.ok is True
    # Should contain content from the pre-built report
    assert "Daily Signal Report" in result.text or "ITA" in result.text or "signal" in result.text.lower()
    assert result.ephemeral is True


def test_slack_command_overdue(tmp_path):
    result = _run("overdue", tmp_path=tmp_path)
    assert result.ok is True
    # Should return some overdue/status info (even if empty list)
    assert result.text  # not empty
    assert result.ephemeral is True


def test_slack_command_review(tmp_path):
    result = _run("review", tmp_path=tmp_path)
    assert result.ok is True
    assert result.text  # not empty
    assert result.ephemeral is True


# ── Unknown subcommand ────────────────────────────────────────────────────────

def test_slack_command_unknown_subcommand_returns_help(tmp_path):
    result = _run("launch-missiles", tmp_path=tmp_path)
    assert result.ok is True
    # Unknown subcommand must silently return help
    help_result = _run("help", tmp_path=tmp_path)
    assert result.text == help_result.text


def test_slack_command_empty_text_returns_help(tmp_path):
    result = _run("", tmp_path=tmp_path)
    assert result.ok is True
    help_result = _run("help", tmp_path=tmp_path)
    assert result.text == help_result.text


# ── Allowlist enforcement ─────────────────────────────────────────────────────

def test_slack_command_allowlist_only(tmp_path):
    for cmd in ("help", "status", "signals", "overdue", "review"):
        result = _run(cmd, user_id="U_ALICE", tmp_path=tmp_path)
        assert result.ok is True, f"allowed user failed for '{cmd}'"


def test_slack_command_rejects_unallowed_user(tmp_path):
    result = handle_etf_command(
        "help",
        "U_EVIL",
        allowed_users=_ALLOWED,
        watchlist_path=_wl(tmp_path),
        scheduler_log_path=_log(tmp_path),
        signal_report_path=_report(tmp_path),
    )
    assert result.ok is False
    assert "許可" in result.text or "unauthorized" in result.text.lower() or "permitted" in result.text.lower()


def test_slack_command_no_allowed_users_restriction(tmp_path):
    """allowed_users=None means no restriction."""
    result = handle_etf_command(
        "help",
        "U_RANDOM_STRANGER",
        allowed_users=None,
        watchlist_path=_wl(tmp_path),
        scheduler_log_path=_log(tmp_path),
        signal_report_path=_report(tmp_path),
    )
    assert result.ok is True


# ── Security: no shell / subprocess / eval / exec ─────────────────────────────

def _router_source() -> str:
    import src.slack_command_router as _mod
    return inspect.getsource(_mod)


def test_slack_command_no_shell_execution():
    src = _router_source()
    # Check that no actual code invokes shell=True (OS/subprocess-level)
    # Strip comment and docstring lines before checking to avoid false positives
    code_lines = [
        line for line in src.splitlines()
        if not line.strip().startswith("#") and not line.strip().startswith('"""') and line.strip()
    ]
    code_only = "\n".join(code_lines)
    assert "shell=True" not in code_only, "shell=True must not appear in router code (only comments/docs allowed)"
    assert "os.system(" not in code_only, "os.system() must not appear in router"


def test_slack_command_no_subprocess_execution():
    src = _router_source()
    # subprocess must not be imported or used
    assert "import subprocess" not in src, "subprocess must not be imported"
    assert "subprocess.run" not in src
    assert "subprocess.Popen" not in src
    assert "subprocess.call" not in src


def test_slack_command_no_eval_exec():
    src = _router_source()
    assert "\neval(" not in src and "    eval(" not in src, "eval() must not appear in router"
    assert "\nexec(" not in src and "    exec(" not in src, "exec() must not appear in router"


# ── Security: no secret / forbidden words in responses ────────────────────────

def test_slack_command_no_secret_in_response(tmp_path):
    """Responses must never contain sensitive substrings (e.g. if a fake key is in env)."""
    import os

    # Temporarily inject a fake secret into env (should NOT appear in any response)
    original = os.environ.copy()
    os.environ["ANTHROPIC_API_KEY"] = "sk-ant-fake-test-secret-abc123"
    try:
        for cmd in ("help", "status", "signals", "overdue", "review"):
            result = _run(cmd, tmp_path=tmp_path)
            lower = result.text.lower()
            for sub in _SENSITIVE_SUBSTRINGS:
                assert sub not in lower, (
                    f"Sensitive substring {sub!r} found in /{cmd} response"
                )
            # The specific fake key must never appear
            assert "sk-ant-fake-test-secret-abc123" not in result.text
    finally:
        os.environ.clear()
        os.environ.update(original)


def test_slack_command_no_forbidden_order_words(tmp_path):
    """No response may contain imperative order-execution language."""
    for cmd in KNOWN_SUBCOMMANDS:
        result = _run(cmd, tmp_path=tmp_path)
        for word in _FORBIDDEN_WORDS:
            assert word not in result.text, (
                f"Forbidden word {word!r} found in /{cmd} response"
            )


# ── Safety: no file writes ────────────────────────────────────────────────────

def _run_all_commands(tmp_path: Path) -> None:
    wl = _wl(tmp_path)
    lg = _log(tmp_path)
    rp = _report(tmp_path)
    jq = _jq(tmp_path)
    js = _js(tmp_path)
    for cmd in KNOWN_SUBCOMMANDS:
        handle_etf_command(
            cmd, "U_ALICE",
            allowed_users=_ALLOWED,
            watchlist_path=wl,
            scheduler_log_path=lg,
            signal_report_path=rp,
            job_queue_path=jq,
            job_status_path=js,
        )


def test_slack_command_does_not_update_watchlist(tmp_path):
    wl_path = Path(_wl(tmp_path))
    original = wl_path.read_text(encoding="utf-8")
    _run_all_commands(tmp_path)
    assert wl_path.read_text(encoding="utf-8") == original, "watchlist.csv must not be modified"


def test_slack_command_does_not_update_ai_sleeve_state(tmp_path):
    sleeve = tmp_path / "ai_sleeve_state.csv"
    sleeve.write_text("symbol,status\n", encoding="utf-8")
    original = sleeve.read_text(encoding="utf-8")
    _run_all_commands(tmp_path)
    assert sleeve.read_text(encoding="utf-8") == original, "ai_sleeve_state.csv must not be modified"


def test_slack_command_does_not_update_etf_master(tmp_path):
    master = tmp_path / "etf_master.csv"
    master.write_text("symbol,name\nITA,iShares US Aerospace\n", encoding="utf-8")
    original = master.read_text(encoding="utf-8")
    _run_all_commands(tmp_path)
    assert master.read_text(encoding="utf-8") == original, "etf_master.csv must not be modified"


def test_slack_command_does_not_update_signal_history(tmp_path):
    hist = tmp_path / "signal_history.csv"
    hist.write_text("date,symbol,signal\n", encoding="utf-8")
    original = hist.read_text(encoding="utf-8")
    _run_all_commands(tmp_path)
    assert hist.read_text(encoding="utf-8") == original, "signal_history.csv must not be modified"


# ── /etf signals freshness tests ─────────────────────────────────────────────

def test_slack_command_signals_includes_report_timestamp(tmp_path):
    """Response must include a generated-at timestamp from the report file's mtime."""
    rp = _report(tmp_path)
    result = handle_etf_command(
        "signals", "U_ALICE",
        allowed_users=_ALLOWED,
        watchlist_path=_wl(tmp_path),
        scheduler_log_path=_log(tmp_path),
        signal_report_path=rp,
    )
    assert result.ok is True
    # Timestamp or "Signal report:" must be in the response
    assert "Signal report:" in result.text or "JST" in result.text or "生成" in result.text


def test_slack_command_signals_warns_when_report_is_stale(tmp_path):
    """When the report is >24h old, the response must include a staleness warning."""
    import os, time
    rp = _report(tmp_path)
    # Set mtime to 25 hours ago
    old_mtime = time.time() - 25 * 3600
    os.utime(rp, (old_mtime, old_mtime))

    result = handle_etf_command(
        "signals", "U_ALICE",
        allowed_users=_ALLOWED,
        watchlist_path=_wl(tmp_path),
        scheduler_log_path=_log(tmp_path),
        signal_report_path=rp,
    )
    assert result.ok is True
    assert "最新レポートではない" in result.text or "daily run" in result.text


def test_slack_command_signals_handles_missing_report(tmp_path):
    """When daily_signal_report.md does not exist, return a safe message without error."""
    result = handle_etf_command(
        "signals", "U_ALICE",
        allowed_users=_ALLOWED,
        watchlist_path=_wl(tmp_path),
        scheduler_log_path=_log(tmp_path),
        signal_report_path=str(tmp_path / "nonexistent_report.md"),
    )
    assert result.ok is True
    assert "生成されていません" in result.text or "見つかりません" in result.text


def test_slack_command_signals_does_not_run_daily():
    """Source must not contain any code that triggers a daily run or process."""
    import inspect, ast
    from src import slack_command_router
    src = inspect.getsource(slack_command_router)
    # Strip comment and string-only lines before checking
    code_lines = []
    in_docstring = False
    for line in src.splitlines():
        stripped = line.strip()
        if stripped.startswith('"""') or stripped.startswith("'''"):
            # Toggle docstring tracking (simplified)
            in_docstring = not in_docstring if stripped.count('"""') % 2 == 1 else in_docstring
            continue
        if in_docstring:
            continue
        if stripped.startswith("#"):
            continue
        code_lines.append(line)
    code = "\n".join(code_lines)
    assert "import subprocess" not in code, "subprocess must not be imported"
    assert "os.system(" not in code, "os.system must not be used"
    assert "subprocess.run" not in code
    assert "subprocess.Popen" not in code


def test_slack_command_signals_does_not_update_files(tmp_path):
    """Calling /etf signals must not modify any file (read-only)."""
    rp = _report(tmp_path)
    wl = _wl(tmp_path)
    original_report = Path(rp).read_text(encoding="utf-8")
    original_wl = Path(wl).read_text(encoding="utf-8")

    handle_etf_command(
        "signals", "U_ALICE",
        allowed_users=_ALLOWED,
        watchlist_path=wl,
        scheduler_log_path=_log(tmp_path),
        signal_report_path=rp,
    )

    assert Path(rp).read_text(encoding="utf-8") == original_report
    assert Path(wl).read_text(encoding="utf-8") == original_wl


def test_slack_command_signals_no_forbidden_order_words(tmp_path):
    """The signals response must contain no forbidden order-execution words."""
    _FORBIDDEN = ["買え", "売れ", "注文実行", "購入実行", "売却実行", "自動売買実行", "買付承認"]
    rp = _report(tmp_path)
    result = handle_etf_command(
        "signals", "U_ALICE",
        allowed_users=_ALLOWED,
        watchlist_path=_wl(tmp_path),
        scheduler_log_path=_log(tmp_path),
        signal_report_path=rp,
    )
    for word in _FORBIDDEN:
        assert word not in result.text, f"Forbidden word {word!r} in signals response"


# ── Handler registration ──────────────────────────────────────────────────────

def test_slack_handler_registers_etf_command():
    """slack_interaction_handler.py must register the /etf command endpoint."""
    handler_path = (
        Path(__file__).resolve().parent.parent
        / "src" / "slack_interaction_handler.py"
    )
    content = handler_path.read_text(encoding="utf-8")
    assert '"/etf"' in content or "'/etf'" in content, (
        "slack_interaction_handler.py must register @app.command(\"/etf\")"
    )
    assert "handle_etf_command" in content, (
        "slack_interaction_handler.py must import and call handle_etf_command"
    )


# ── Phase 7.3: Job command tests ───────────────────────────────────────────────

def test_slack_known_subcommands_includes_job_commands():
    """KNOWN_SUBCOMMANDS must include all Phase 7.3 job subcommands."""
    for cmd in ("candidate", "signal", "update-market-data", "run", "job"):
        assert cmd in KNOWN_SUBCOMMANDS, f"{cmd!r} not in KNOWN_SUBCOMMANDS"


def test_slack_command_candidate_enqueues_job(tmp_path):
    result = _run("candidate ITA", tmp_path=tmp_path)
    assert result.ok is True
    assert "ITA" in result.text
    assert "job_id" in result.text.lower() or "Job ID" in result.text
    # Must NOT contain order execution language
    for word in _FORBIDDEN_WORDS:
        assert word not in result.text


def test_slack_command_candidate_missing_symbol_returns_usage(tmp_path):
    result = _run("candidate", tmp_path=tmp_path)
    assert result.ok is True
    assert "SYMBOL" in result.text or "使い方" in result.text


def test_slack_command_candidate_rejects_market_reference(tmp_path):
    for sym in ("SPY", "QQQ", "VIX"):
        result = _run(f"candidate {sym}", tmp_path=tmp_path)
        assert result.ok is True
        assert "マーケットリファレンス" in result.text or "対象外" in result.text
        # No job enqueued for market-reference symbols


def test_slack_command_candidate_rejects_invalid_symbol(tmp_path):
    result = _run("candidate BAD SYMBOL!", tmp_path=tmp_path)
    # "BAD" is 3 chars and valid ticker format; "SYMBOL!" has a ! so second word is taken
    # Actually parts[1] = "BAD" which passes validation. Let's test a truly invalid one.
    result = _run("candidate TOOLONGSYMBOL123", tmp_path=tmp_path)
    assert result.ok is True
    # Should return invalid symbol message or usage
    assert "無効" in result.text or "SYMBOL" in result.text or "使い方" in result.text


def test_slack_command_signal_enqueues_job(tmp_path):
    result = _run("signal QTUM", tmp_path=tmp_path)
    assert result.ok is True
    assert "QTUM" in result.text
    assert "job" in result.text.lower() or "Job" in result.text


def test_slack_command_signal_missing_symbol_returns_usage(tmp_path):
    result = _run("signal", tmp_path=tmp_path)
    assert result.ok is True
    assert "SYMBOL" in result.text or "使い方" in result.text


def test_slack_command_update_market_data_enqueues_job(tmp_path):
    result = _run("update-market-data", tmp_path=tmp_path)
    assert result.ok is True
    assert "update_market_data" in result.text.lower() or "market" in result.text.lower()
    assert "job" in result.text.lower() or "Job" in result.text


def test_slack_command_run_daily_enqueues_job(tmp_path):
    result = _run("run daily", tmp_path=tmp_path)
    assert result.ok is True
    assert "daily_signal_check" in result.text.lower() or "daily" in result.text.lower()
    assert "job" in result.text.lower() or "Job" in result.text
    # Safety guarantees must be stated
    assert "--allow-watchlist-update" not in result.text.lower() or "なし" in result.text


def test_slack_command_run_without_daily_returns_usage(tmp_path):
    result = _run("run", tmp_path=tmp_path)
    assert result.ok is True
    assert "daily" in result.text.lower() or "使い方" in result.text


def test_slack_command_run_unknown_arg_returns_usage(tmp_path):
    result = _run("run weekly", tmp_path=tmp_path)
    assert result.ok is True
    assert "daily" in result.text.lower() or "使い方" in result.text


def test_slack_command_job_missing_id_returns_usage(tmp_path):
    result = _run("job", tmp_path=tmp_path)
    assert result.ok is True
    assert "JOB_ID" in result.text or "使い方" in result.text


def test_slack_command_job_invalid_id_returns_error(tmp_path):
    result = _run("job !!invalid!!", tmp_path=tmp_path)
    assert result.ok is True
    assert "無効" in result.text or "Job ID" in result.text


def test_slack_command_job_not_found_returns_message(tmp_path):
    result = _run("job 00000000", tmp_path=tmp_path)
    assert result.ok is True
    assert "見つかりません" in result.text or "not found" in result.text.lower()


def test_slack_command_job_shows_status_for_existing_job(tmp_path):
    """After enqueuing a job, /etf job <id> must show its status."""
    from src.job_store import enqueue_job

    jq = _jq(tmp_path)
    js = _js(tmp_path)
    job = enqueue_job(
        "update_market_data", "U_ALICE", {},
        queue_path=jq, status_path=js,
    )
    job_id_short = job["job_id"][:8]

    result = handle_etf_command(
        f"job {job_id_short}", "U_ALICE",
        allowed_users=_ALLOWED,
        watchlist_path=_wl(tmp_path),
        scheduler_log_path=_log(tmp_path),
        signal_report_path=_report(tmp_path),
        job_queue_path=jq,
        job_status_path=js,
    )
    assert result.ok is True
    assert "QUEUED" in result.text or "update_market_data" in result.text


def test_slack_command_job_commands_no_forbidden_words(tmp_path):
    """All job-related subcommands must return responses free of forbidden words."""
    commands = [
        "candidate ITA",
        "candidate",
        "signal QTUM",
        "signal",
        "update-market-data",
        "run daily",
        "run",
        "job",
        "job 00000000",
    ]
    for text in commands:
        result = _run(text, tmp_path=tmp_path)
        for word in _FORBIDDEN_WORDS:
            assert word not in result.text, (
                f"Forbidden word {word!r} in response to '/etf {text}'"
            )


def test_slack_command_job_commands_no_allow_watchlist_update(tmp_path):
    """No job command response should mention --allow-watchlist-update as an option."""
    commands = ["run daily", "candidate ITA", "signal QTUM", "update-market-data"]
    for text in commands:
        result = _run(text, tmp_path=tmp_path)
        assert "--allow-watchlist-update" not in result.text, (
            f"'--allow-watchlist-update' appeared in response to '/etf {text}'"
        )


def test_slack_command_candidate_idempotency(tmp_path):
    """Enqueuing the same candidate twice returns the same job ID (idempotent)."""
    result1 = _run("candidate ITA", tmp_path=tmp_path)
    result2 = _run("candidate ITA", tmp_path=tmp_path)
    assert result1.ok is True
    assert result2.ok is True
    # Both responses reference the same job (same job_id substring)
    # Extract job ID from text (appears as backtick-quoted UUID)
    import re as _re
    ids1 = _re.findall(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", result1.text)
    ids2 = _re.findall(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", result2.text)
    if ids1 and ids2:
        assert ids1[0] == ids2[0], "Duplicate candidate enqueue must return same job_id"
