"""Phase 7.2: Tests for Slack Signal Human Decision Actions.

All tests are hermetic: they use tmp_path fixtures and never touch the real
watchlist.csv / signal_human_decision_log.jsonl / ai_sleeve_state.csv.

Safety invariants verified:
- Allowlist enforcement before any dispatch
- All 5 decision types recorded correctly in the official log
- Backup created on decision (save_watchlist creates archive)
- signal_history.csv / ai_sleeve_state.csv / etf_master.csv never written
- no_order_quantity=True / no_auto_trade=True in every result
- No forbidden order-execution words in any message or button label
- Idempotency: same user+action+symbol not double-recorded
- No shell/subprocess/eval/exec in source
- /etf review response includes Block Kit blocks with decision buttons
"""
from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

from src.slack_signal_actions import (
    SIGNAL_ACTIONS,
    SIGNAL_DECISION_LABELS,
    SIGNAL_NOTE_ACTION,
    SIGNAL_NOTE_MODAL_CALLBACK_ID,
    SignalActionResult,
    build_signal_action_value,
    build_signal_note_modal_view,
    build_signal_review_blocks,
    parse_signal_action_value,
    route_signal_action,
    route_signal_note_submission,
)

_ALLOWED = ["U_ALICE", "U_BOB"]

_MINIMAL_WATCHLIST = (
    "ticker,symbol,status,signal_side,confidence,next_review_date,reason_summary,"
    "updated_by,last_reviewed_at\n"
    "ITA,ITA,BUY_CANDIDATE,BUY,0.84,2026-06-15,pullback signal,ai,2026-06-07T03:00:00\n"
    "XLU,XLU,WATCH,BUY,0.72,2026-06-14,rate watch,ai,2026-06-07T03:00:00\n"
)

_FORBIDDEN_WORDS = [
    "買え", "売れ", "注文実行", "購入実行", "売却実行", "自動売買実行", "買付承認",
]


def _make_watchlist(tmp_path: Path) -> str:
    p = tmp_path / "watchlist.csv"
    p.write_text(_MINIMAL_WATCHLIST, encoding="utf-8")
    return str(p)


def _make_log(tmp_path: Path) -> str:
    d = tmp_path / "logs"
    d.mkdir(exist_ok=True)
    return str(d / "signal_human_decision_log.jsonl")


def _make_idempotency_log(tmp_path: Path) -> str:
    d = tmp_path / "logs"
    d.mkdir(exist_ok=True)
    return str(d / "slack_signal_idempotency.jsonl")


def _make_archive(tmp_path: Path) -> str:
    d = tmp_path / "archive"
    d.mkdir(exist_ok=True)
    return str(d)


def _action_value(symbol: str = "ITA") -> str:
    return build_signal_action_value(symbol=symbol)


def _do_route(
    action_id: str,
    symbol: str,
    tmp_path: Path,
    user_id: str = "U_ALICE",
    allowed_users: list[str] | None = None,
) -> SignalActionResult:
    if allowed_users is None:
        allowed_users = _ALLOWED
    return route_signal_action(
        action_id,
        _action_value(symbol),
        user_id,
        allowed_users=allowed_users,
        watchlist_path=_make_watchlist(tmp_path),
        log_path=_make_log(tmp_path),
        idempotency_log=_make_idempotency_log(tmp_path),
    )


# ── Block Kit builder tests ───────────────────────────────────────────────────

def test_slack_signal_action_builds_review_buttons():
    items = [
        {"ticker": "ITA", "status": "BUY_CANDIDATE", "confidence": "0.84",
         "next_review_date": "2026-06-15"},
        {"ticker": "XLU", "status": "WATCH", "confidence": "0.72",
         "next_review_date": "2026-06-14"},
    ]
    blocks = build_signal_review_blocks(items)

    # Must have a header block
    types = [b["type"] for b in blocks]
    assert "header" in types

    # Each symbol must have an actions block with 6 buttons
    action_blocks = [b for b in blocks if b["type"] == "actions"]
    assert len(action_blocks) == 2  # one per symbol

    for ab in action_blocks:
        button_ids = [el["action_id"] for el in ab["elements"]]
        # All 6 signal actions must be present
        for action_id in SIGNAL_ACTIONS:
            assert action_id in button_ids, f"Missing button: {action_id}"
        assert len(ab["elements"]) == len(SIGNAL_ACTIONS)

    # Safety notice must appear in context block
    context_texts = [
        el["text"]
        for b in blocks if b["type"] == "context"
        for el in b.get("elements", [])
    ]
    combined = " ".join(context_texts)
    assert "自動売買なし" in combined or "最終判断" in combined


def test_slack_signal_action_value_has_no_secrets():
    value = build_signal_action_value(symbol="ITA")
    data = json.loads(value)

    _SENSITIVE = ("api_key", "apikey", "secret", "password", "token",
                  "prompt", "raw_response", "signing", "credential")
    for key in data:
        for sub in _SENSITIVE:
            assert sub not in str(key).lower(), f"Sensitive key found: {key!r}"

    for v in data.values():
        if isinstance(v, str):
            for sub in _SENSITIVE:
                assert sub not in v.lower(), f"Sensitive value found: {v!r}"

    assert data["symbol"] == "ITA"
    assert data["source_type"] == "signal_review"


# ── Decision recording tests (one per decision type) ─────────────────────────

def _check_decision(tmp_path: Path, action_id: str, expected_decision: str) -> None:
    result = _do_route(action_id, "ITA", tmp_path)
    assert result.ok is True, f"Expected ok=True, got: {result.message}"
    assert result.recorded is True
    assert result.human_decision == expected_decision
    assert result.symbol == "ITA"
    assert result.no_order_quantity is True
    assert result.no_auto_trade is True
    # Confirm log was written
    log_path = Path(_make_log(tmp_path))
    assert log_path.exists()
    entries = [json.loads(l) for l in log_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert any(e.get("decision") == expected_decision and e.get("symbol") == "ITA"
               for e in entries)


def test_slack_signal_action_records_user_approved(tmp_path):
    _check_decision(tmp_path, "signal_approved", "USER_APPROVED")


def test_slack_signal_action_records_user_watch(tmp_path):
    _check_decision(tmp_path, "signal_watch", "USER_WATCH")


def test_slack_signal_action_records_user_hold_off(tmp_path):
    _check_decision(tmp_path, "signal_hold_off", "USER_HOLD_OFF")


def test_slack_signal_action_records_user_rejected(tmp_path):
    _check_decision(tmp_path, "signal_rejected", "USER_REJECTED")


def test_slack_signal_action_records_user_request_rerun(tmp_path):
    _check_decision(tmp_path, "signal_request_rerun", "USER_REQUEST_RERUN")


# ── Note modal tests ──────────────────────────────────────────────────────────

def test_slack_signal_action_note_modal():
    modal = build_signal_note_modal_view(symbol="ITA")
    assert modal["type"] == "modal"
    assert modal["callback_id"] == SIGNAL_NOTE_MODAL_CALLBACK_ID
    assert "ITA" in modal["private_metadata"]

    # Verify no sensitive keys in private_metadata
    meta = json.loads(modal["private_metadata"])
    _SENSITIVE = ("api_key", "apikey", "secret", "password", "token",
                  "prompt", "raw_response", "signing", "credential")
    for key in meta:
        for sub in _SENSITIVE:
            assert sub not in str(key).lower()

    # Submit / Close / Title must not have order language
    title = modal.get("title", {}).get("text", "")
    assert "買付" not in title
    assert "注文" not in title


def test_slack_signal_action_note_submission(tmp_path):
    wl = _make_watchlist(tmp_path)
    lg = _make_log(tmp_path)

    from src.slack_signal_actions import SIGNAL_NOTE_BLOCK_ID, SIGNAL_NOTE_INPUT_ACTION_ID

    view = {
        "private_metadata": json.dumps({"source_type": "signal_review", "symbol": "ITA",
                                         "action_id": SIGNAL_NOTE_ACTION}),
        "state": {
            "values": {
                SIGNAL_NOTE_BLOCK_ID: {
                    SIGNAL_NOTE_INPUT_ACTION_ID: {"value": "金利動向を確認してから判断"}
                }
            }
        },
    }
    result = route_signal_note_submission(
        view, "U_ALICE",
        allowed_users=_ALLOWED,
        watchlist_path=wl,
        log_path=lg,
    )
    assert result.ok is True
    assert result.recorded is True
    assert result.human_decision == "USER_NOTE_ONLY"
    assert result.symbol == "ITA"
    assert result.no_order_quantity is True
    assert result.no_auto_trade is True

    # Log must contain the note
    log_path = Path(lg)
    assert log_path.exists()
    entries = [json.loads(l) for l in log_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert any("金利動向" in (e.get("note") or "") for e in entries)


# ── Backup and log tests ──────────────────────────────────────────────────────

def test_slack_signal_action_creates_backup(tmp_path):
    """save_watchlist() creates a backup; verify archive directory gets a file."""
    wl = _make_watchlist(tmp_path)
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir(exist_ok=True)

    from src.signals.signal_review import record_human_signal_decision
    record_human_signal_decision(
        "ITA", "USER_WATCH",
        watchlist_path=wl,
        log_path=_make_log(tmp_path),
        archive_dir=str(archive_dir),
    )
    # archive_dir should now contain a backup file
    backups = list(archive_dir.glob("watchlist_*.csv"))
    assert backups, "No backup created in archive directory"


def test_slack_signal_action_appends_human_decision_log(tmp_path):
    log = _make_log(tmp_path)
    result = route_signal_action(
        "signal_watch", _action_value("XLU"), "U_BOB",
        allowed_users=_ALLOWED,
        watchlist_path=_make_watchlist(tmp_path),
        log_path=log,
        idempotency_log=_make_idempotency_log(tmp_path),
    )
    assert result.recorded is True
    log_path = Path(log)
    assert log_path.exists()
    lines = [l for l in log_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(lines) >= 1
    entry = json.loads(lines[-1])
    assert entry.get("symbol") == "XLU"
    assert entry.get("decision") == "USER_WATCH"


# ── File safety tests (signal_history / ai_sleeve_state / etf_master) ─────────

def _run_all_signal_actions(tmp_path: Path) -> None:
    wl = _make_watchlist(tmp_path)
    lg = _make_log(tmp_path)
    idm = _make_idempotency_log(tmp_path)
    for action_id in SIGNAL_ACTIONS:
        route_signal_action(
            action_id, _action_value("ITA"), "U_ALICE",
            allowed_users=_ALLOWED,
            watchlist_path=wl,
            log_path=lg,
            idempotency_log=idm,
        )


def test_slack_signal_action_does_not_update_signal_history(tmp_path):
    hist = tmp_path / "signal_history.csv"
    hist.write_text("date,symbol,signal\n", encoding="utf-8")
    original = hist.read_text(encoding="utf-8")
    _run_all_signal_actions(tmp_path)
    assert hist.read_text(encoding="utf-8") == original


def test_slack_signal_action_does_not_update_ai_sleeve_state(tmp_path):
    sleeve = tmp_path / "ai_sleeve_state.csv"
    sleeve.write_text("symbol,status\n", encoding="utf-8")
    original = sleeve.read_text(encoding="utf-8")
    _run_all_signal_actions(tmp_path)
    assert sleeve.read_text(encoding="utf-8") == original


def test_slack_signal_action_does_not_update_etf_master(tmp_path):
    master = tmp_path / "etf_master.csv"
    master.write_text("symbol,name\nITA,iShares US Aerospace\n", encoding="utf-8")
    original = master.read_text(encoding="utf-8")
    _run_all_signal_actions(tmp_path)
    assert master.read_text(encoding="utf-8") == original


# ── Safety invariant tests ────────────────────────────────────────────────────

def test_slack_signal_action_no_order_quantity(tmp_path):
    for action_id in SIGNAL_ACTIONS:
        if SIGNAL_ACTIONS[action_id] is None:
            continue  # skip note action
        result = _do_route(action_id, "ITA", tmp_path)
        assert result.no_order_quantity is True, f"no_order_quantity not True for {action_id}"


def test_slack_signal_action_no_auto_trade(tmp_path):
    for action_id in SIGNAL_ACTIONS:
        if SIGNAL_ACTIONS[action_id] is None:
            continue
        result = _do_route(action_id, "ITA", tmp_path)
        assert result.no_auto_trade is True, f"no_auto_trade not True for {action_id}"


def test_slack_signal_action_no_forbidden_order_words(tmp_path):
    """No decision message or button label may contain forbidden order-execution words."""
    # Check button labels
    items = [{"ticker": "ITA", "status": "BUY_CANDIDATE", "confidence": "0.84",
              "next_review_date": "2026-06-15"}]
    blocks = build_signal_review_blocks(items)
    all_text = json.dumps(blocks, ensure_ascii=False)
    for word in _FORBIDDEN_WORDS:
        assert word not in all_text, f"Forbidden word {word!r} found in blocks"

    # Check decision labels
    for label in SIGNAL_DECISION_LABELS.values():
        for word in _FORBIDDEN_WORDS:
            assert word not in label, f"Forbidden word {word!r} in label {label!r}"

    # Check route_signal_action messages
    for action_id, decision in SIGNAL_ACTIONS.items():
        if decision is None:
            continue
        result = _do_route(action_id, "ITA", tmp_path)
        for word in _FORBIDDEN_WORDS:
            assert word not in result.message, (
                f"Forbidden word {word!r} in {action_id} message: {result.message!r}"
            )


def test_slack_signal_action_rejects_unallowed_user(tmp_path):
    result = route_signal_action(
        "signal_watch", _action_value("ITA"), "U_EVIL",
        allowed_users=_ALLOWED,
        watchlist_path=_make_watchlist(tmp_path),
        log_path=_make_log(tmp_path),
        idempotency_log=_make_idempotency_log(tmp_path),
    )
    assert result.ok is False
    assert "許可" in result.message or "unauthorized" in result.message.lower()


# ── Idempotency test ──────────────────────────────────────────────────────────

def test_slack_signal_action_idempotency(tmp_path):
    wl = _make_watchlist(tmp_path)
    lg = _make_log(tmp_path)
    idm = _make_idempotency_log(tmp_path)

    # First press
    r1 = route_signal_action(
        "signal_watch", _action_value("ITA"), "U_ALICE",
        allowed_users=_ALLOWED,
        watchlist_path=wl, log_path=lg, idempotency_log=idm,
    )
    assert r1.recorded is True
    assert r1.duplicate is False

    # Second press — same user + same action + same symbol
    r2 = route_signal_action(
        "signal_watch", _action_value("ITA"), "U_ALICE",
        allowed_users=_ALLOWED,
        watchlist_path=wl, log_path=lg, idempotency_log=idm,
    )
    assert r2.duplicate is True
    assert r2.recorded is False
    assert r2.ok is True  # Duplicate is a graceful "already recorded", not an error

    # Different user may still press (no cross-user idempotency)
    r3 = route_signal_action(
        "signal_watch", _action_value("ITA"), "U_BOB",
        allowed_users=_ALLOWED,
        watchlist_path=wl, log_path=lg, idempotency_log=idm,
    )
    assert r3.duplicate is False


# ── Source inspection: no shell / subprocess / eval / exec ───────────────────

def _signal_source() -> str:
    import src.slack_signal_actions as _mod
    return inspect.getsource(_mod)


def test_slack_signal_action_does_not_use_shell_or_subprocess():
    src = _signal_source()
    code_lines = [
        line for line in src.splitlines()
        if not line.strip().startswith("#") and not line.strip().startswith('"""')
    ]
    code = "\n".join(code_lines)
    assert "import subprocess" not in code
    assert "subprocess.run" not in code
    assert "subprocess.Popen" not in code
    assert "os.system(" not in code
    # eval/exec: check without catching docstring comments
    assert "\neval(" not in code and "    eval(" not in code
    assert "\nexec(" not in code and "    exec(" not in code


# ── /etf review includes decision buttons ─────────────────────────────────────

def test_etf_review_command_includes_decision_buttons(tmp_path):
    """handle_etf_command('review', ...) must return CommandResult.blocks with buttons."""
    from src.slack_command_router import handle_etf_command

    wl_path = tmp_path / "watchlist.csv"
    wl_path.write_text(_MINIMAL_WATCHLIST, encoding="utf-8")

    result = handle_etf_command(
        "review", "U_ALICE",
        allowed_users=_ALLOWED,
        watchlist_path=str(wl_path),
        scheduler_log_path=str(tmp_path / "nonexistent.jsonl"),
        signal_report_path=str(tmp_path / "nonexistent.md"),
    )
    assert result.ok is True
    assert result.blocks is not None, "/etf review must return blocks for interactive buttons"
    assert isinstance(result.blocks, list)
    assert len(result.blocks) > 0

    # At least one actions block with signal buttons
    action_blocks = [b for b in result.blocks if b.get("type") == "actions"]
    assert action_blocks, "No actions blocks found in /etf review response"

    # All signal action_ids must appear in buttons
    all_button_ids = [
        el["action_id"]
        for b in action_blocks
        for el in b.get("elements", [])
    ]
    for action_id in SIGNAL_ACTIONS:
        assert action_id in all_button_ids, f"Missing button: {action_id}"
