"""Phase 3: Slack review decision section builder.

Generates text sections and Block Kit blocks for the monthly review decision
prompt that appears in Slack after each bot run.

Interactive button callbacks are NOT implemented here — this module only
builds the display payload. CLI recording (--record-decision) is the
primary input method.
"""
from __future__ import annotations

from src.decision_logger import DECISION_LABELS

# Map decision values to display icons
_DECISION_ICONS: dict[str, str] = {
    "REVIEW_CONFIRMED": "✅",
    "SKIP_THIS_MONTH": "⏸",
    "REQUEST_RERUN": "🔁",
    "MANUAL_OVERRIDE": "⚠️",
}

_GATE_ICONS: dict[str, str] = {
    "PASS": "✅",
    "PASS_WITH_CAUTION": "⚠️",
    "REVIEW_REQUIRED": "🔶",
    "FAIL": "❌",
}


def _cli_hint(decision: str, comment: str = "") -> str:
    c = f' --decision-comment "{comment}"' if comment else ""
    return f"python main.py --record-decision {decision}{c}"


def build_review_decision_section(
    pre_trade_gate_status: str | None,
    pre_trade_gate_failures: list[str] | None = None,
    run_date: str | None = None,
) -> str:
    """Return the review decision section as plain text for Slack Incoming Webhook.

    Pre-Trade Gate PASS:
        Shows REVIEW_CONFIRMED, SKIP_THIS_MONTH, REQUEST_RERUN options.

    Pre-Trade Gate PASS_WITH_CAUTION / REVIEW_REQUIRED:
        Shows all four options with a caution header.

    Pre-Trade Gate FAIL:
        Omits REVIEW_CONFIRMED. Shows warning. Requires comment for MANUAL_OVERRIDE.
    """
    failures = pre_trade_gate_failures or []
    gate = pre_trade_gate_status or "N/A"
    gate_icon = _GATE_ICONS.get(gate, "")

    lines: list[str] = ["", "*月次レビュー判断*"]

    if gate == "FAIL":
        lines.append(f"{gate_icon} Pre-Trade Gate: FAIL — 以下の制約に抵触しています")
        for f in failures[:5]:
            lines.append(f"  - {f}")
        lines.append("推奨：見送り、または再レビューしてください。")
        lines.append("⚠️ 自動売買は行いません。")
        lines.append("")
        lines.append("判断を記録するには以下のCLIを実行してください（コメント必須）:")
        lines.append(f"  {_cli_hint('SKIP_THIS_MONTH', '今月は見送り')}")
        lines.append(f"  {_cli_hint('REQUEST_RERUN', '設定を見直して再実行')}")
        lines.append(f"  {_cli_hint('MANUAL_OVERRIDE', '警告を理解したうえで採用（理由を記述）')}")

    elif gate in ("PASS_WITH_CAUTION", "REVIEW_REQUIRED"):
        lines.append(f"{gate_icon} Pre-Trade Gate: {gate} — 注意あり")
        lines.append("⚠️ 自動売買は行いません。")
        lines.append("")
        lines.append("判断を記録するには以下のCLIを実行してください:")
        lines.append(f"  {_cli_hint('REVIEW_CONFIRMED')}")
        lines.append(f"  {_cli_hint('SKIP_THIS_MONTH')}")
        lines.append(f"  {_cli_hint('REQUEST_RERUN')}")
        lines.append(f"  {_cli_hint('MANUAL_OVERRIDE', '警告を理解したうえで採用（理由を記述）')}")

    else:
        # PASS or N/A
        if gate == "PASS":
            lines.append(f"{gate_icon} Pre-Trade Gate: PASS")
        lines.append("自動売買は行いません。")
        lines.append("")
        lines.append("判断を記録するには以下のCLIを実行してください:")
        lines.append(f"  {_cli_hint('REVIEW_CONFIRMED')}")
        lines.append(f"  {_cli_hint('SKIP_THIS_MONTH')}")
        lines.append(f"  {_cli_hint('REQUEST_RERUN')}")

    return "\n".join(lines)


def build_review_decision_blocks(
    pre_trade_gate_status: str | None,
    pre_trade_gate_failures: list[str] | None = None,
) -> list[dict]:
    """Return Block Kit blocks for the review decision section.

    These blocks are display-only when sent via Incoming Webhook
    (buttons are not interactive without a Slack app with Interactivity enabled).
    """
    failures = pre_trade_gate_failures or []
    gate = pre_trade_gate_status or "N/A"
    gate_icon = _GATE_ICONS.get(gate, "")

    blocks: list[dict] = [
        {"type": "divider"},
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*月次レビュー判断* {gate_icon} Pre-Trade Gate: {gate}",
            },
        },
    ]

    if gate == "FAIL" and failures:
        failure_text = "\n".join(f"• {f}" for f in failures[:5])
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"以下の制約に抵触しています:\n{failure_text}\n⚠️ 自動売買は行いません。",
            },
        })

    # Determine which decisions to show
    if gate == "FAIL":
        decisions = ["SKIP_THIS_MONTH", "REQUEST_RERUN", "MANUAL_OVERRIDE"]
    elif gate in ("PASS_WITH_CAUTION", "REVIEW_REQUIRED"):
        decisions = ["REVIEW_CONFIRMED", "SKIP_THIS_MONTH", "REQUEST_RERUN", "MANUAL_OVERRIDE"]
    else:
        decisions = ["REVIEW_CONFIRMED", "SKIP_THIS_MONTH", "REQUEST_RERUN"]

    elements = []
    for d in decisions:
        icon = _DECISION_ICONS.get(d, "")
        label = DECISION_LABELS.get(d, d)
        elements.append({
            "type": "button",
            "text": {"type": "plain_text", "text": f"{icon} {label}", "emoji": True},
            "value": d,
            "action_id": f"review_decision_{d.lower()}",
        })

    # Block Kit actions are limited to 5 elements per block
    blocks.append({"type": "actions", "elements": elements})

    blocks.append({
        "type": "context",
        "elements": [
            {
                "type": "mrkdwn",
                "text": "CLIでも記録可能: `python main.py --record-decision <DECISION> --decision-comment \"...\"`",
            }
        ],
    })

    return blocks


# ── Phase 4.1: interactive action buttons (action_id + safe JSON value) ───────


def _button(action_id: str, label: str, value: str) -> dict:
    return {
        "type": "button",
        "text": {"type": "plain_text", "text": label, "emoji": True},
        "action_id": action_id,
        "value": value,
    }


def build_monthly_action_blocks(value: str) -> list[dict]:
    """Block Kit actions for the monthly review (records a decision, not an order)."""
    from src.slack_actions import MONTHLY_BUTTONS

    return [
        {"type": "divider"},
        {
            "type": "context",
            "elements": [{
                "type": "mrkdwn",
                "text": "ボタンは*判断の記録*です（売買承認ではありません）。自動売買は行いません。",
            }],
        },
        {
            "type": "actions",
            "elements": [_button(aid, label, value) for aid, label in MONTHLY_BUTTONS],
        },
    ]


def build_candidate_action_blocks(
    value: str,
    candidate_stability: str | None = None,
    recommended_handling: str | None = None,
) -> list[dict]:
    """Block Kit actions for a candidate (records a decision, not an order).

    The 小額検討候補 button is omitted when the candidate is UNSTABLE /
    HUMAN_REVIEW_REQUIRED / DO_NOT_ACT_YET (the router also rejects it defensively).
    """
    from src.slack_actions import (
        BLOCK_SMALL_TEST_HANDLING,
        BLOCK_SMALL_TEST_STABILITY,
        CANDIDATE_BUTTONS,
    )

    block_small = (
        candidate_stability in BLOCK_SMALL_TEST_STABILITY
        or recommended_handling in BLOCK_SMALL_TEST_HANDLING
    )
    elements = [
        _button(aid, label, value)
        for aid, label in CANDIDATE_BUTTONS
        if not (aid == "candidate_small_test_candidate" and block_small)
    ]
    note = "ボタンは*判断の記録*です（売買承認・注文ではありません）。"
    if block_small:
        note += f"（{candidate_stability or recommended_handling} のため小額検討候補は無効）"
    return [
        {"type": "divider"},
        {"type": "context", "elements": [{"type": "mrkdwn", "text": note}]},
        {"type": "actions", "elements": elements},
    ]
