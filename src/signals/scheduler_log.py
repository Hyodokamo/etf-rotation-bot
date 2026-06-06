"""Phase 6.0: Scheduler run log — append-only JSONL.

Records Daily Signal Run results for audit and monitoring.
No order quantity, no auto-trade, no brokerage.
No secrets stored (stdout/stderr are summaries, not raw output).
"""
from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path

from src.logger import logger

DEFAULT_SCHEDULER_LOG_PATH = "logs/scheduler_run_log.jsonl"

# Lines containing these keywords are filtered from stdout/stderr summaries
_SECRET_KEYWORDS = ("token", "secret", "password", "key=", "api_key", "webhook")


class RunStatus(str, Enum):
    SUCCESS = "SUCCESS"
    PARTIAL_FAILURE = "PARTIAL_FAILURE"
    FAILED = "FAILED"


@dataclass
class StepLog:
    """Result of one command step in the daily run."""
    command: str
    exit_code: int
    duration_seconds: float
    stdout_summary: str = ""
    stderr_summary: str = ""

    @property
    def succeeded(self) -> bool:
        return self.exit_code == 0


@dataclass
class RunLog:
    """Full record of one daily signal run.

    Invariants:
    - no_auto_trade is always True
    - no_order_quantity is always True
    - steps is an append-only list of StepLog objects
    """
    run_id: str
    started_at: str
    finished_at: str
    status: RunStatus
    steps: list[StepLog]
    no_auto_trade: bool = True
    no_order_quantity: bool = True
    committee_target_count: int = 0  # symbols that had LLM committee run
    skipped_count: int = 0           # symbols skipped (no triggers, trigger-gate mode)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["status"] = self.status.value if isinstance(self.status, RunStatus) else self.status
        return d


def make_run_id() -> str:
    """Generate a unique run ID: run_YYYYMMDD_HHMMSS_XXXXXXXX."""
    return datetime.now().strftime("run_%Y%m%d_%H%M%S_") + str(uuid.uuid4())[:8]


def sanitize_summary(text: str, max_chars: int = 500) -> str:
    """Truncate and sanitize text for safe log storage.

    Filters lines that look like tokens/secrets.
    Never stores raw API keys, webhook URLs, or passwords.
    """
    if not text:
        return ""
    safe_lines = [
        line for line in text.splitlines()
        if not any(kw in line.lower() for kw in _SECRET_KEYWORDS)
    ]
    return "\n".join(safe_lines)[:max_chars]


def append_run_log(
    log: RunLog,
    path: str | Path = DEFAULT_SCHEDULER_LOG_PATH,
) -> None:
    """Append one RunLog entry to the scheduler JSONL log.

    Append-only: never overwrites existing log entries.
    no_auto_trade and no_order_quantity are always written as True.

    Safety:
    - stdout/stderr summaries are already sanitized (no secrets)
    - Never modifies watchlist / signal_history / ai_sleeve_state / etf_master
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    entry = log.to_dict()
    # Double-check safety invariants before writing
    entry["no_auto_trade"] = True
    entry["no_order_quantity"] = True
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    logger.info(f"Scheduler run log appended: {log.run_id} status={log.status.value}")
