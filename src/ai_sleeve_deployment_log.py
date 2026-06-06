"""Phase 5.0.7: AI Sleeve 投入記録 (deployment log).

Append-only monthly log (``data/ai_sleeve_state_YYYYMM.csv``) recording when
the human manually notes a deployment decision for the ¥1M AI sleeve.

Design constraints:
- ``consideration_jpy`` is the human-entered JPY amount of intent — **NOT** an
  order quantity, NOT a brokerage API call, NOT a share-count calculation.
  The field name and validators explicitly prevent quantity/shares/order fields.
- No auto-deploy from Candidate Review; recording is always manual.
- Updates ``data/ai_sleeve_state.csv`` (the source of truth) after each append.
- Monthly logs are gitignored (``data/ai_sleeve_state_*.csv``).
- Archive copies go to ``data/archive/ai_sleeve_state_*.csv`` (also gitignored).
"""
from __future__ import annotations

import csv
import shutil
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, field_validator

from src.logger import logger

# ── paths ─────────────────────────────────────────────────────────────────────

DEFAULT_STATE_PATH = "data/ai_sleeve_state.csv"
DEFAULT_LOG_DIR = "data"
DEFAULT_ARCHIVE_DIR = "data/archive"

LOG_COLUMNS = [
    "as_of_date",
    "action",
    "symbol",
    "theme",
    "consideration_jpy",   # NOT order amount; human-entered JPY intent
    "account",
    "resulting_cash_jpy",
    "resulting_invested_jpy",
    "notes",
]

STATE_COLUMNS = [
    "as_of_date",
    "sleeve_name",
    "total_budget_jpy",
    "cash_jpy",
    "invested_jpy",
    "default_account",
    "notes",
]


# ── models ────────────────────────────────────────────────────────────────────


class DeploymentAction(str, Enum):
    DEPLOY = "deploy"    # cash -> invested (human chose to deploy)
    REDUCE = "reduce"    # invested -> cash (human chose to reduce)
    NOTE = "note"        # freeform audit note; no amount change
    CORRECT = "correct"  # manual state correction (e.g. dividend reinvestment)


class DeploymentLogEntry(BaseModel):
    """One row in the monthly deployment log.

    ``consideration_jpy`` is a human-entered JPY amount of intent.
    No order quantity, no share count, no brokerage API is involved.
    """

    as_of_date: str
    action: DeploymentAction = DeploymentAction.DEPLOY
    symbol: str = ""
    theme: str = ""
    consideration_jpy: float = 0.0
    account: str = "taxable"
    resulting_cash_jpy: float = 0.0
    resulting_invested_jpy: float = 0.0
    notes: str = ""

    @field_validator("consideration_jpy", mode="before")
    @classmethod
    def _parse_jpy(cls, v):
        if v is None or v == "":
            return 0.0
        try:
            return float(str(v).replace(",", "").replace("円", "").strip())
        except (TypeError, ValueError):
            return 0.0

    @field_validator("resulting_cash_jpy", "resulting_invested_jpy", mode="before")
    @classmethod
    def _parse_result(cls, v):
        if v is None or v == "":
            return 0.0
        try:
            return float(str(v).replace(",", "").replace("円", "").strip())
        except (TypeError, ValueError):
            return 0.0


# ── path helpers ──────────────────────────────────────────────────────────────


def get_monthly_log_path(month: str, base_dir: str | Path = DEFAULT_LOG_DIR) -> Path:
    """Return ``data/ai_sleeve_state_YYYYMM.csv`` for the given YYYY-MM month."""
    ym = month.replace("-", "")
    return Path(base_dir) / f"ai_sleeve_state_{ym}.csv"


# ── I/O ───────────────────────────────────────────────────────────────────────


def read_deployment_log(path: str | Path) -> list[dict]:
    """Read all entries from a monthly log CSV. Missing file -> ``[]``."""
    p = Path(path)
    if not p.exists():
        return []
    try:
        with open(p, encoding="utf-8-sig", newline="") as f:
            return list(csv.DictReader(f))
    except OSError as e:
        logger.warning(f"deployment log read failed: {e}")
        return []


def append_deployment_entry(
    entry: DeploymentLogEntry,
    path: str | Path,
) -> None:
    """Append one entry to the monthly log CSV. Creates header if file is new."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    new_file = not p.exists() or p.stat().st_size == 0
    with open(p, "a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=LOG_COLUMNS)
        if new_file:
            writer.writeheader()
        writer.writerow({
            "as_of_date": entry.as_of_date,
            "action": entry.action.value,
            "symbol": entry.symbol,
            "theme": entry.theme,
            "consideration_jpy": entry.consideration_jpy,
            "account": entry.account,
            "resulting_cash_jpy": entry.resulting_cash_jpy,
            "resulting_invested_jpy": entry.resulting_invested_jpy,
            "notes": entry.notes,
        })
    logger.info(f"Deployment log entry appended: {entry.action.value} {entry.symbol} -> {p}")


def update_ai_sleeve_state_csv(
    state_path: str | Path,
    *,
    as_of_date: str,
    total_budget_jpy: float,
    new_cash_jpy: float,
    new_invested_jpy: float,
    default_account: str = "taxable",
    notes: str = "",
) -> None:
    """Overwrite ``ai_sleeve_state.csv`` with the latest state.

    This is the only mutation this module performs on the source-of-truth file.
    No order quantity, no shares, no brokerage call.
    """
    p = Path(state_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=STATE_COLUMNS)
        writer.writeheader()
        writer.writerow({
            "as_of_date": as_of_date,
            "sleeve_name": "ai_sleeve",
            "total_budget_jpy": total_budget_jpy,
            "cash_jpy": new_cash_jpy,
            "invested_jpy": new_invested_jpy,
            "default_account": default_account,
            "notes": notes,
        })
    logger.info(
        f"ai_sleeve_state.csv updated: cash={new_cash_jpy} invested={new_invested_jpy}"
    )


def archive_monthly_log(
    month: str,
    log_dir: str | Path = DEFAULT_LOG_DIR,
    archive_dir: str | Path = DEFAULT_ARCHIVE_DIR,
) -> str | None:
    """Copy the monthly log to ``data/archive/``. Returns archive path or None."""
    src = get_monthly_log_path(month, log_dir)
    if not src.exists():
        logger.warning(f"archive: source log not found: {src}")
        return None
    dst_dir = Path(archive_dir)
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / src.name
    shutil.copy2(src, dst)
    logger.info(f"Monthly log archived: {src} -> {dst}")
    return str(dst)


# ── state arithmetic ──────────────────────────────────────────────────────────


def compute_new_sleeve_state(
    current_cash: float,
    current_invested: float,
    total_budget: float,
    action: DeploymentAction,
    amount_jpy: float,
) -> tuple[float, float]:
    """Return ``(new_cash, new_invested)`` after applying the action.

    ``amount_jpy`` is a consideration amount in JPY — NOT a share count.
    NOTE/CORRECT leave the cash/invested unchanged (caller updates notes only).
    """
    if action == DeploymentAction.DEPLOY:
        new_invested = min(current_invested + amount_jpy, total_budget)
        new_cash = max(total_budget - new_invested, 0.0)
    elif action == DeploymentAction.REDUCE:
        new_invested = max(current_invested - amount_jpy, 0.0)
        new_cash = min(current_cash + amount_jpy, total_budget)
    else:
        # NOTE or CORRECT: no automatic amount change
        new_cash = current_cash
        new_invested = current_invested
    return round(new_cash, 2), round(new_invested, 2)


# ── high-level record ─────────────────────────────────────────────────────────


def record_sleeve_deployment(
    *,
    as_of_date: str,
    action: str | DeploymentAction = DeploymentAction.DEPLOY,
    symbol: str = "",
    theme: str = "",
    consideration_jpy: float = 0.0,
    account: str = "taxable",
    notes: str = "",
    state_path: str | Path = DEFAULT_STATE_PATH,
    log_dir: str | Path = DEFAULT_LOG_DIR,
    month: str | None = None,
) -> DeploymentLogEntry:
    """Record one deployment decision: update log + state CSV.

    Steps:
    1. Read current state from ``ai_sleeve_state.csv``.
    2. Compute new cash/invested from action + consideration_jpy.
    3. Append row to ``data/ai_sleeve_state_YYYYMM.csv``.
    4. Overwrite ``ai_sleeve_state.csv`` with new state.

    Returns the ``DeploymentLogEntry`` that was appended.
    No order quantity is computed; ``consideration_jpy`` is advisory JPY intent.
    """
    from src.portfolio_context import _num, load_ai_sleeve_state  # avoid circular at module top

    act = DeploymentAction(action) if isinstance(action, str) else action

    # Read current state
    raw = load_ai_sleeve_state(state_path) or {}
    total_budget = raw.get("total_budget_jpy", 1_000_000.0)
    current_cash = raw.get("current_cash_jpy", total_budget)
    current_invested = raw.get("current_invested_jpy", 0.0)
    default_account = raw.get("default_account", "taxable")

    new_cash, new_invested = compute_new_sleeve_state(
        current_cash, current_invested, total_budget, act, consideration_jpy
    )

    entry = DeploymentLogEntry(
        as_of_date=as_of_date,
        action=act,
        symbol=symbol,
        theme=theme,
        consideration_jpy=consideration_jpy,
        account=account,
        resulting_cash_jpy=new_cash,
        resulting_invested_jpy=new_invested,
        notes=notes,
    )

    # Determine month for log file
    log_month = month or as_of_date[:7]
    log_path = get_monthly_log_path(log_month, log_dir)

    append_deployment_entry(entry, log_path)
    update_ai_sleeve_state_csv(
        state_path,
        as_of_date=as_of_date,
        total_budget_jpy=total_budget,
        new_cash_jpy=new_cash,
        new_invested_jpy=new_invested,
        default_account=default_account,
        notes=notes or f"{act.value} {symbol} {consideration_jpy:.0f}jpy",
    )
    return entry
