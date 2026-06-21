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

# Phase: mobile UX (MVP-1). Per-state phone header content.
# Each entry: icon / 1行結論（急ぎ度込み） / 推奨アクション。
# Wording avoids 買う・売る・購入・注文・利確・損切り・今が買い時・必ず上がる.
_MOBILE_HEADER: dict[str, dict[str, str]] = {
    "PASS": {
        "icon": "✅",
        "conclusion": "制約クリア。確認のみでよく、急ぎの対応はありません。",
        "action": "内容を確認し、時間のある時に「確認済み」を記録してください。",
    },
    "PASS_WITH_CAUTION": {
        "icon": "⚠️",
        "conclusion": "おおむね問題なし。軽微な注意はありますが、急ぎの対応は不要です。",
        "action": "注意点を確認し、確認済み／見送り／再レビューのいずれかを記録してください。",
    },
    "REVIEW_REQUIRED": {
        "icon": "🔶",
        "conclusion": "注意ポイントあり。記録の前に内容の確認をおすすめします（緊急ではありません）。",
        "action": "注意点を確認し、確認済み／見送り／再レビューのいずれかを記録してください。",
    },
    "FAIL": {
        "icon": "❌",
        "conclusion": "売買前チェックの制約に抵触。このままの採用は推奨しません（要対応）。",
        "action": "見送り、または設定を見直して再レビューしてください（採用する場合は理由メモが必須）。",
    },
    "N/A": {
        "icon": "ℹ️",
        "conclusion": "今回の判定情報は限定的です。内容を確認してください。",
        "action": "内容を確認し、必要に応じて判断を記録してください。",
    },
}

# Always-on safety notice. Avoids the banned promotional tokens while conveying
# 自動売買しない／数量計算しない／最終判断は人間 を毎回明示する.
_MOBILE_SAFETY_NOTE = (
    "🛡️ 自動売買は行いません。売買数量の計算もしません。最終判断は人間が行います。"
)


def build_mobile_summary_header(
    gate_status: str | None,
    run_date: str | None = None,
) -> str:
    """Phone-first header: 1行結論・推奨アクション・急ぎ度・自動売買しない注記.

    Placed at the very top of the Slack message so the first 3 lines answer
    「今月の結論／急ぎ度／やること」. Records decisions only — never trades,
    never sizes orders. ``gate_status`` accepts PASS / PASS_WITH_CAUTION /
    REVIEW_REQUIRED / FAIL; unknown or None falls back to the N/A header.
    """
    spec = _MOBILE_HEADER.get(gate_status or "N/A", _MOBILE_HEADER["N/A"])
    month = ""
    if run_date and len(run_date) >= 7:
        month = f"（{run_date[:7]}）"

    return "\n".join([
        f"{spec['icon']} ETF Rotation Bot 月次レビュー{month}",
        f"今月の結論: {spec['conclusion']}",
        f"推奨アクション: {spec['action']}",
        _MOBILE_SAFETY_NOTE,
    ])


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
    # Phone-first導線: スマホでは確認のみ。記録は後でPC/NASで実行する旨を明示。
    record_intro = "📝 スマホでは内容の確認のみ。判断の記録は後でPC/NASで次のいずれかを実行:"

    if gate == "FAIL":
        lines.append(f"{gate_icon} Pre-Trade Gate: FAIL — 以下の制約に抵触しています")
        for f in failures[:5]:
            lines.append(f"  - {f}")
        lines.append("推奨：見送り、または再レビューしてください。")
        lines.append("自動売買は行いません。最終判断は人間が行います。")
        lines.append(record_intro + "（採用する場合はコメント必須）")
        lines.append(f"  見送り → {_cli_hint('SKIP_THIS_MONTH')}")
        lines.append(f"  再レビュー → {_cli_hint('REQUEST_RERUN')}")
        lines.append(f"  理由を理解して採用 → {_cli_hint('MANUAL_OVERRIDE', '理由')}")

    elif gate in ("PASS_WITH_CAUTION", "REVIEW_REQUIRED"):
        lines.append(f"{gate_icon} Pre-Trade Gate: {gate} — 注意あり")
        lines.append("自動売買は行いません。最終判断は人間が行います。")
        lines.append(record_intro)
        lines.append(f"  確認済み → {_cli_hint('REVIEW_CONFIRMED')}")
        lines.append(f"  見送り → {_cli_hint('SKIP_THIS_MONTH')}")
        lines.append(f"  再レビュー → {_cli_hint('REQUEST_RERUN')}")
        lines.append(f"  理由を理解して採用 → {_cli_hint('MANUAL_OVERRIDE', '理由')}")

    else:
        # PASS or N/A
        if gate == "PASS":
            lines.append(f"{gate_icon} Pre-Trade Gate: PASS")
        lines.append("自動売買は行いません。最終判断は人間が行います。")
        lines.append(record_intro)
        lines.append(f"  確認済み → {_cli_hint('REVIEW_CONFIRMED')}")
        lines.append(f"  見送り → {_cli_hint('SKIP_THIS_MONTH')}")
        lines.append(f"  再レビュー → {_cli_hint('REQUEST_RERUN')}")

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

    Phase 4.1.1 visibility gating:
      - Blocked (UNSTABLE / HUMAN_REVIEW_REQUIRED / DO_NOT_ACT_YET): hide both
        小額検討候補 and Watchlist入り; show only 様子見 / 見送り / 再レビュー /
        メモ追加 plus a warning.
      - STABLE or OK_FOR_WATCHLIST: show the full set (incl. 小額検討候補).
      - Otherwise: show Watchlist入り + 様子見/見送り/再レビュー/メモ追加 (no 小額検討候補).
    The router still blocks 小額検討候補 at press time as a last line of defense.
    """
    from src.slack_actions import (
        BLOCK_SMALL_TEST_HANDLING,
        BLOCK_SMALL_TEST_STABILITY,
        CANDIDATE_BUTTONS,
    )

    blocked = (
        candidate_stability in BLOCK_SMALL_TEST_STABILITY
        or recommended_handling in BLOCK_SMALL_TEST_HANDLING
    )
    full = (not blocked) and (
        candidate_stability == "STABLE" or recommended_handling == "OK_FOR_WATCHLIST"
    )

    def _visible(aid: str) -> bool:
        if aid == "candidate_small_test_candidate":
            return full
        if aid == "candidate_watchlist":
            return not blocked
        return True  # 様子見 / 見送り / 再レビュー / メモ追加 are always available

    elements = [_button(aid, label, value) for aid, label in CANDIDATE_BUTTONS if _visible(aid)]

    note = "ボタンは*判断の記録*です（売買承認・注文ではありません）。"
    if blocked:
        reason = candidate_stability if candidate_stability in BLOCK_SMALL_TEST_STABILITY else recommended_handling
        note += (
            f" 判定が不安定/要再確認（{reason}）なため、小額検討候補にはできません。"
            "再レビューまたは様子見を選択してください。"
        )
    return [
        {"type": "divider"},
        {"type": "context", "elements": [{"type": "mrkdwn", "text": note}]},
        {"type": "actions", "elements": elements},
    ]
