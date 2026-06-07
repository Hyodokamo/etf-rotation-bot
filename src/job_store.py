"""Phase 7.3: Job Store — append-only queue and status for async Slack jobs.

All jobs are advisory operations only. No auto-trade, no order quantity,
no brokerage connection, no NISA automation. Jobs call existing read-only
or write-safe Python functions or fixed subprocess command lists.

File design:
  logs/job_queue.jsonl   — one entry per enqueued job (append-only)
  logs/job_status.jsonl  — status updates per job_id (append-only; latest = authoritative)

Idempotency: if a QUEUED or RUNNING job with the same job_type + symbol already
exists, enqueue_job() returns the existing job instead of creating a duplicate.

No secrets (api_key / token / secret / prompt / raw_response) are stored in
either log file — args are scrubbed before writing.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

from src.logger import logger

_JST = timezone(timedelta(hours=9))

DEFAULT_JOB_QUEUE_PATH = "logs/job_queue.jsonl"
DEFAULT_JOB_STATUS_PATH = "logs/job_status.jsonl"

# Whitelisted job types — only these may be enqueued
JOB_TYPES: frozenset[str] = frozenset({
    "candidate_review",
    "signal_check",
    "update_market_data",
    "daily_signal_check",
})

JOB_STATUSES: frozenset[str] = frozenset({
    "QUEUED", "RUNNING", "SUCCESS", "FAILED", "SKIPPED",
})

# Market reference symbols may not be used as candidate_review targets
MARKET_REFERENCE_SYMBOLS: frozenset[str] = frozenset({
    "SPY", "QQQ", "SOXX", "SMH", "VIX", "US10Y", "DXY",
    "^VIX", "^TNX", "DX-Y.NYB",
})

_SENSITIVE_SUBSTRINGS = (
    "api_key", "apikey", "secret", "password", "token",
    "prompt", "raw_response", "signing", "credential",
)


def _now_jst() -> str:
    return datetime.now(_JST).isoformat()


def _scrub_args(args: dict) -> dict:
    """Remove any arg whose key contains a sensitive substring before storing."""
    return {
        k: v for k, v in args.items()
        if not any(s in str(k).lower() for s in _SENSITIVE_SUBSTRINGS)
    }


def _append_to_log(entry: dict, path: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    # Double-scrub: defensive drop of any sensitive key that slipped through
    safe = {
        k: v for k, v in entry.items()
        if not any(s in str(k).lower() for s in _SENSITIVE_SUBSTRINGS)
    }
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(safe, ensure_ascii=False) + "\n")


def _read_log(path: str) -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    entries: list[dict] = []
    for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            logger.warning(f"[job_store] corrupt log line {i} in {p} — skipped")
    return entries


def _build_job_map(
    queue_path: str,
    status_path: str,
) -> dict[str, dict]:
    """Build a merged view of all jobs (queue + latest status)."""
    jobs: dict[str, dict] = {}
    for entry in _read_log(queue_path):
        jid = entry.get("job_id")
        if jid:
            jobs[jid] = dict(entry)
    for entry in _read_log(status_path):
        jid = entry.get("job_id")
        if jid and jid in jobs:
            for key in ("status", "started_at", "finished_at", "result_summary", "error_summary"):
                if entry.get(key) is not None:
                    jobs[jid][key] = entry[key]
    return jobs


def _find_active_job(
    job_type: str,
    symbol: str,
    queue_path: str,
    status_path: str,
) -> dict | None:
    """Return an existing QUEUED or RUNNING job with same type + symbol, or None."""
    active = {"QUEUED", "RUNNING"}
    for job in _build_job_map(queue_path, status_path).values():
        if (
            job.get("job_type") == job_type
            and job.get("status") in active
            and (job.get("args") or {}).get("symbol", "").upper() == symbol.upper()
        ):
            return job
    return None


# ── Public API ────────────────────────────────────────────────────────────────

def enqueue_job(
    job_type: str,
    requested_by: str,
    args: dict | None = None,
    *,
    queue_path: str = DEFAULT_JOB_QUEUE_PATH,
    status_path: str = DEFAULT_JOB_STATUS_PATH,
    check_duplicate: bool = True,
) -> dict:
    """Enqueue a new job and return its job dict (with job_id and status=QUEUED).

    If check_duplicate=True (default) and an identical QUEUED/RUNNING job already
    exists (same job_type + symbol), the existing job is returned without creating
    a new entry (idempotent).

    Raises ValueError for unknown job_type.
    """
    if job_type not in JOB_TYPES:
        raise ValueError(
            f"Unknown job_type: {job_type!r}. Must be one of: {sorted(JOB_TYPES)}"
        )

    safe_args = _scrub_args(args or {})
    symbol = safe_args.get("symbol", "")

    # Idempotency: return existing job if still QUEUED/RUNNING
    if check_duplicate:
        existing = _find_active_job(job_type, symbol, queue_path, status_path)
        if existing:
            logger.info(
                f"[job_store] duplicate enqueue skipped: {job_type} {symbol!r} "
                f"— returning existing job {existing['job_id']}"
            )
            return existing

    job_id = str(uuid.uuid4())
    now = _now_jst()
    job = {
        "job_id": job_id,
        "job_type": job_type,
        "requested_by": requested_by,
        "requested_from": "slack",
        "created_at": now,
        "started_at": None,
        "finished_at": None,
        "status": "QUEUED",
        "args": safe_args,
        "result_summary": None,
        "error_summary": None,
        "no_auto_trade": True,
        "no_order_quantity": True,
        "brokerage_connection": False,
    }
    _append_to_log(job, queue_path)
    _append_to_log(job, status_path)
    logger.info(f"[job_store] enqueued: {job_type} {symbol!r} → {job_id}")
    return job


def update_job_status(
    job_id: str,
    status: str,
    *,
    started_at: str | None = None,
    finished_at: str | None = None,
    result_summary: str | None = None,
    error_summary: str | None = None,
    status_path: str = DEFAULT_JOB_STATUS_PATH,
) -> dict:
    """Append a status update for the given job (append-only).

    Raises ValueError for invalid status.
    """
    if status not in JOB_STATUSES:
        raise ValueError(f"Invalid status: {status!r}. Must be one of: {sorted(JOB_STATUSES)}")

    now = _now_jst()
    entry = {
        "job_id": job_id,
        "status": status,
        "updated_at": now,
        "started_at": started_at,
        "finished_at": finished_at,
        "result_summary": (result_summary or "")[:2000] if result_summary else None,
        "error_summary":  (error_summary  or "")[:2000] if error_summary  else None,
        "no_auto_trade": True,
        "no_order_quantity": True,
    }
    _append_to_log(entry, status_path)
    logger.info(f"[job_store] status update: {job_id} → {status}")
    return entry


def get_job_status(
    job_id: str,
    *,
    queue_path: str = DEFAULT_JOB_QUEUE_PATH,
    status_path: str = DEFAULT_JOB_STATUS_PATH,
) -> dict | None:
    """Return the latest merged state for a job, or None if job_id not found."""
    jobs = _build_job_map(queue_path, status_path)
    return jobs.get(job_id)


def get_queued_jobs(
    *,
    queue_path: str = DEFAULT_JOB_QUEUE_PATH,
    status_path: str = DEFAULT_JOB_STATUS_PATH,
) -> list[dict]:
    """Return all jobs currently in QUEUED status, in creation order."""
    all_jobs = _build_job_map(queue_path, status_path)
    # Preserve creation order from queue log
    order = [e["job_id"] for e in _read_log(queue_path) if "job_id" in e]
    return [
        all_jobs[jid]
        for jid in order
        if jid in all_jobs and all_jobs[jid].get("status") == "QUEUED"
    ]
