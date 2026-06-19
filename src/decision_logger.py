"""Phase 3: Monthly review decision logger.

Records the human reviewer's judgment after inspecting the monthly report.
This is NOT a trade approval system — no orders are generated.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from enum import Enum
from pathlib import Path

from src.logger import logger


class ReviewDecision(str, Enum):
    REVIEW_CONFIRMED = "REVIEW_CONFIRMED"
    SKIP_THIS_MONTH = "SKIP_THIS_MONTH"
    REQUEST_RERUN = "REQUEST_RERUN"
    MANUAL_OVERRIDE = "MANUAL_OVERRIDE"


DECISION_LABELS: dict[str, str] = {
    "REVIEW_CONFIRMED": "レビュー確認済み",
    "SKIP_THIS_MONTH": "今月は見送り",
    "REQUEST_RERUN": "再レビュー",
    "MANUAL_OVERRIDE": "手動判断で採用",
}

_JST = timezone(timedelta(hours=9))


@dataclass
class DecisionLog:
    run_date: str
    decision: ReviewDecision
    comment: str
    decided_by: str
    strategy_variant: str | None
    pre_trade_gate_status: str | None
    pre_trade_gate_failures: list[str]
    ai_audit_status: str | None
    final_allocation: dict[str, float]
    decision_id: str = field(default="")
    decided_at: str = field(default="")

    def __post_init__(self) -> None:
        if not self.decided_at:
            now = datetime.now(_JST)
            self.decided_at = now.isoformat()
        if not self.decision_id:
            ts = self.decided_at[:19].replace(":", "-").replace("T", "T")
            self.decision_id = ts

    def to_dict(self) -> dict:
        return {
            "run_date": self.run_date,
            "decision_id": self.decision_id,
            "decision": self.decision.value if isinstance(self.decision, ReviewDecision) else self.decision,
            "decision_label": DECISION_LABELS.get(
                self.decision.value if isinstance(self.decision, ReviewDecision) else self.decision, ""
            ),
            "decided_at": self.decided_at,
            "decided_by": self.decided_by,
            "comment": self.comment,
            "strategy_variant": self.strategy_variant,
            "pre_trade_gate_status": self.pre_trade_gate_status,
            "pre_trade_gate_failures": self.pre_trade_gate_failures,
            "ai_audit_status": self.ai_audit_status,
            "final_allocation": {t: round(w, 4) for t, w in self.final_allocation.items()},
            "auto_trade": False,
            "order_generated": False,
        }

    def to_markdown(self) -> str:
        decision_val = self.decision.value if isinstance(self.decision, ReviewDecision) else self.decision
        label = DECISION_LABELS.get(decision_val, decision_val)
        decided_at_display = self.decided_at[:16].replace("T", " ")
        gate_status = self.pre_trade_gate_status or "N/A"
        audit_status = self.ai_audit_status or "N/A"

        lines: list[str] = [
            "# 月次レビュー判断ログ",
            "",
            f"- 実行日：{self.run_date}",
            f"- 判断：{label}",
            f"- 判断者：{self.decided_by}",
            f"- 判断日時：{decided_at_display}",
            f"- Strategy Variant：{self.strategy_variant or 'N/A'}",
            f"- Pre-Trade Gate：{gate_status}",
            f"- AI監査：{audit_status}",
            "",
            "## コメント",
            "",
            self.comment if self.comment else "（コメントなし）",
            "",
            "## 最終配分案",
            "",
            "| ETF | 配分 |",
            "|---|---:|",
        ]
        for ticker, w in sorted(self.final_allocation.items(), key=lambda x: -x[1]):
            lines.append(f"| {ticker} | {w:.1%} |")

        if self.pre_trade_gate_failures:
            lines += [
                "",
                "## Pre-Trade Gate 指摘",
                "",
            ]
            for f in self.pre_trade_gate_failures:
                lines.append(f"- {f}")

        lines += [
            "",
            "## 注意",
            "",
            "このログは月次レビュー判断の記録です。",
            "自動売買は行いません。",
            "注文数量は生成していません。",
        ]

        if decision_val == "MANUAL_OVERRIDE":
            lines += [
                "",
                "> ⚠️ MANUAL_OVERRIDE：警告を理解したうえで人間判断として採用することを選択しました。",
                "> 自動売買・注文生成は行いません。",
            ]

        return "\n".join(lines)


def validate_decision(
    decision: ReviewDecision | str,
    comment: str,
    pre_trade_gate_status: str | None,
    *,
    allow_manual_override: bool = True,
    require_comment_on_manual_override: bool = True,
    require_comment_on_fail_gate: bool = True,
) -> None:
    """Raise ValueError if the decision violates business rules."""
    decision_val = decision.value if isinstance(decision, ReviewDecision) else decision

    if decision_val == "MANUAL_OVERRIDE":
        if not allow_manual_override:
            raise ValueError("MANUAL_OVERRIDE は設定で無効化されています。")
        if require_comment_on_manual_override and not comment.strip():
            raise ValueError("MANUAL_OVERRIDE にはコメントが必須です。--decision-comment を指定してください。")

    if pre_trade_gate_status == "FAIL" and require_comment_on_fail_gate and not comment.strip():
        raise ValueError(
            "Pre-Trade Gate が FAIL のため、判断記録にはコメントが必須です。"
            " --decision-comment を指定してください。"
        )


def create_decision_log(
    run_date: str,
    decision: ReviewDecision | str,
    comment: str,
    decided_by: str,
    strategy_variant: str | None,
    pre_trade_gate_status: str | None,
    pre_trade_gate_failures: list[str],
    ai_audit_status: str | None,
    final_allocation: dict[str, float],
) -> DecisionLog:
    if isinstance(decision, str):
        decision = ReviewDecision(decision)
    return DecisionLog(
        run_date=run_date,
        decision=decision,
        comment=comment,
        decided_by=decided_by,
        strategy_variant=strategy_variant,
        pre_trade_gate_status=pre_trade_gate_status,
        pre_trade_gate_failures=pre_trade_gate_failures,
        ai_audit_status=ai_audit_status,
        final_allocation=final_allocation,
    )


def save_decision_log(log: DecisionLog, output_dir: str) -> tuple[str, str]:
    """Save decision_log.json and decision_log.md. Returns (json_path, md_path)."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    json_path = out / "decision_log.json"
    json_path.write_text(
        json.dumps(log.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    md_path = out / "decision_log.md"
    md_path.write_text(log.to_markdown(), encoding="utf-8")

    logger.info(f"Decision log saved: {json_path}")
    logger.info(f"Decision log (md) saved: {md_path}")
    return str(json_path), str(md_path)


def update_run_log_with_decision(
    run_log_path: str,
    log: DecisionLog,
    json_path: str,
) -> None:
    """Append decision metadata to an existing run_log.json without overwriting it."""
    p = Path(run_log_path)
    if not p.exists():
        logger.warning(f"run_log.json not found at {run_log_path} — skipping update")
        return

    existing = json.loads(p.read_text(encoding="utf-8"))
    decision_val = log.decision.value if isinstance(log.decision, ReviewDecision) else log.decision
    existing.update({
        "review_decision": decision_val,
        "review_decision_logged": True,
        "review_decision_file": json_path,
        "review_decision_at": log.decided_at,
    })
    p.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"run_log.json updated with review decision: {run_log_path}")


def load_latest_run_context(run_date_str: str, output_dir: str = "outputs") -> dict:
    """Load run_log.json, pre_trade_gate_result.json, and audit_result.json for the given month."""
    ym = run_date_str[:7]  # YYYY-MM
    month_dir = Path(output_dir) / ym

    ctx: dict = {
        "run_log": None,
        "run_log_path": None,
        "pre_trade_gate": None,
        "ai_audit_status": None,
    }

    run_log_path = month_dir / "run_log.json"
    if run_log_path.exists():
        ctx["run_log"] = json.loads(run_log_path.read_text(encoding="utf-8"))
        ctx["run_log_path"] = str(run_log_path)
    else:
        raise FileNotFoundError(
            f"run_log.json が見つかりません: {run_log_path}\n"
            "先に python main.py --no-ai-audit を実行してください。"
        )

    gate_path = month_dir / "pre_trade_gate_result.json"
    if gate_path.exists():
        ctx["pre_trade_gate"] = json.loads(gate_path.read_text(encoding="utf-8"))

    audit_path = month_dir / "audit_result.json"
    if audit_path.exists():
        audit_raw = json.loads(audit_path.read_text(encoding="utf-8"))
        ctx["ai_audit_status"] = audit_raw.get("status")

    return ctx
