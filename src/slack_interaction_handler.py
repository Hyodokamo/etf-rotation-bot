"""Phase 4.1: Slack Interactivity — Socket Mode listener.

Receives Slack button presses, acks immediately, routes them to an append-only
human-decision record, and replies with a short confirmation. Records decisions
only — no allocation change, no order quantity, no auto-trading, no brokerage.

Usage:
    python src/slack_interaction_handler.py [--socket-mode] [--dry-run]

Socket Mode is the default. ``--dry-run`` validates a sample payload without any
network/Slack dependency. ``slack_bolt`` is imported lazily so this module (and
the test suite) load without the SDK installed.

Env:
    SLACK_BOT_TOKEN, SLACK_APP_TOKEN  — required for Socket Mode
    SLACK_SIGNING_SECRET              — for future HTTP endpoint mode
    SLACK_ALLOWED_USER_IDS            — comma-separated allowlist (optional)
"""
from __future__ import annotations

import argparse
import json
import os
import sys

# Allow `python src/slack_interaction_handler.py` (script run) to import `src.*`.
if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

from src.logger import logger
from src.slack_action_router import route_action, route_note_submission
from src.slack_actions import build_action_value, extract_action, is_note_action
from src.slack_command_router import handle_etf_command
from src.slack_modals import build_note_modal_from_action_value, extract_human_note
from src.slack_signal_actions import (
    SIGNAL_NOTE_ACTION,
    SIGNAL_NOTE_MODAL_CALLBACK_ID,
    build_signal_note_modal_from_value,
    route_signal_action,
    route_signal_note_submission,
)


def interactivity_enabled() -> bool:
    """True only when the Socket Mode tokens are present (disabled by default)."""
    return bool(
        os.environ.get("SLACK_BOT_TOKEN", "").strip()
        and os.environ.get("SLACK_APP_TOKEN", "").strip()
    )


def get_allowed_users() -> list[str] | None:
    raw = os.environ.get("SLACK_ALLOWED_USER_IDS", "").strip()
    if not raw:
        return None
    return [u.strip() for u in raw.split(",") if u.strip()]


def handle_payload(payload: dict, *, allowed_users: list[str] | None = None) -> dict:
    """Pure handler: extract -> route -> result dict (used by Socket Mode + tests)."""
    try:
        action_id, value, user_id = extract_action(payload)
    except ValueError as e:
        return {"ok": False, "message": f"payload error: {e}"}
    result = route_action(action_id, value, user_id, allowed_users=allowed_users)
    return result.to_dict() | {"message": result.message}


def handle_view_submission(
    view: dict,
    user_id: str,
    *,
    allowed_users: list[str] | None = None,
    monthly_log_path: str | None = None,
    candidate_log_path: str | None = None,
) -> dict:
    """Pure handler for the note modal submission (used by Socket Mode + tests)."""
    metadata = view.get("private_metadata", "")
    note = extract_human_note(view)
    kwargs: dict = {"allowed_users": allowed_users}
    if monthly_log_path is not None:
        kwargs["monthly_log_path"] = monthly_log_path
    if candidate_log_path is not None:
        kwargs["candidate_log_path"] = candidate_log_path
    result = route_note_submission(metadata, note, user_id, **kwargs)
    return result.to_dict() | {"message": result.message}


def _run_dry_run() -> None:
    sample_value = build_action_value(
        source_type="candidate_review", review_id="demo-review",
        candidate_symbol="GRID", candidate_stability="UNSTABLE",
        recommended_handling="REVIEW_BEFORE_ACTION",
    )
    payload = {
        "user": {"id": "U_DEMO"},
        "actions": [{"action_id": "candidate_small_test_candidate", "value": sample_value}],
    }
    result = handle_payload(payload, allowed_users=get_allowed_users())
    print("Dry-run payload validation:")
    print(json.dumps(result, ensure_ascii=False, indent=2))


def _run_socket_mode() -> None:  # pragma: no cover - requires slack_bolt + network
    if not interactivity_enabled():
        print("SLACK_BOT_TOKEN / SLACK_APP_TOKEN not set — Socket Mode disabled.")
        return
    try:
        from slack_bolt import App
        from slack_bolt.adapter.socket_mode import SocketModeHandler
    except ImportError:
        print("slack_bolt is not installed. Install with: pip install slack_bolt")
        return

    import re

    from src.slack_modals import NOTE_MODAL_CALLBACK_ID

    app = App(token=os.environ["SLACK_BOT_TOKEN"])
    allowed_users = get_allowed_users()

    # All our buttons use action_id starting with monthly_ or candidate_.
    @app.action(re.compile(r"^(monthly_|candidate_)"))
    def _on_action(ack, body, client, respond):
        ack()  # ack immediately
        try:
            action_id, value, user_id = extract_action(body)
            if is_note_action(action_id):
                # メモ追加 -> open the note modal
                view = build_note_modal_from_action_value(value, action_id, user_id)
                client.views_open(trigger_id=body["trigger_id"], view=view)
                return
            original_blocks = (body.get("message") or {}).get("blocks")
            result = route_action(
                action_id, value, user_id, allowed_users=allowed_users,
                original_blocks=original_blocks,
                message_updater=lambda p: client.chat_update(**p),
            )
            respond(text=result.message, response_type="ephemeral", replace_original=False)
        except Exception as e:  # never crash the listener
            logger.error(f"Slack action handling error: {type(e).__name__}: {e}")
            respond(text="処理中にエラーが発生しました。", response_type="ephemeral")

    @app.action(re.compile(r"^signal_"))
    def _on_signal_action(ack, body, client, respond):
        ack()  # ack immediately
        try:
            actions = body.get("actions") or []
            action = actions[0] if actions else {}
            action_id = action.get("action_id", "")
            value = action.get("value", "")
            user_id = body.get("user_id") or (body.get("user") or {}).get("id", "")

            if action_id == SIGNAL_NOTE_ACTION:
                modal = build_signal_note_modal_from_value(value, user_id=user_id)
                client.views_open(trigger_id=body["trigger_id"], view=modal)
                return

            result = route_signal_action(
                action_id, value, user_id, allowed_users=allowed_users
            )
            respond(text=result.message, response_type="ephemeral", replace_original=False)
        except Exception as e:  # never crash the listener
            logger.error(f"Slack signal action error: {type(e).__name__}: {e}")
            respond(text="処理中にエラーが発生しました。", response_type="ephemeral")

    @app.view(SIGNAL_NOTE_MODAL_CALLBACK_ID)
    def _on_signal_note_submit(ack, body, view):
        ack()
        try:
            user_id = (body.get("user") or {}).get("id", "")
            result = route_signal_note_submission(view, user_id, allowed_users=allowed_users)
            logger.info(f"Signal note submission: {result.message}")
        except Exception as e:  # never crash the listener
            logger.error(f"Slack signal note submission error: {type(e).__name__}: {e}")

    @app.command("/etf")
    def _on_etf_command(ack, body, respond):
        ack()  # ack immediately — Slack requires < 3 s
        try:
            user_id = body.get("user_id") or (body.get("user") or {}).get("id", "")
            text = body.get("text", "")
            result = handle_etf_command(text, user_id, allowed_users=allowed_users)
            if result.blocks:
                respond(blocks=result.blocks, text=result.text, response_type="ephemeral")
            else:
                respond(text=result.text, response_type="ephemeral")
        except Exception as e:  # never crash the listener
            logger.error(f"Slack /etf command error: {type(e).__name__}: {e}")
            respond(text="コマンド処理中にエラーが発生しました。", response_type="ephemeral")

    @app.view(NOTE_MODAL_CALLBACK_ID)
    def _on_note_submit(ack, body, view):
        ack()
        try:
            user_id = (body.get("user") or {}).get("id", "")
            result = handle_view_submission(view, user_id, allowed_users=allowed_users)
            logger.info(f"Note submission: {result.get('message')}")
        except Exception as e:  # never crash the listener
            logger.error(f"Slack note submission error: {type(e).__name__}: {e}")

    logger.info("Starting Slack Socket Mode listener…")
    SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"]).start()


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Slack Interactivity listener (Phase 4.1)")
    parser.add_argument("--socket-mode", action="store_true", default=True,
                        help="Run the Socket Mode listener (default).")
    parser.add_argument("--dry-run", action="store_true", default=False,
                        help="Validate a sample payload without connecting to Slack.")
    args = parser.parse_args()

    if args.dry_run:
        _run_dry_run()
        return
    _run_socket_mode()


if __name__ == "__main__":
    main()
