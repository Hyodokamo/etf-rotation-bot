"""Phase 7.3: Tests for job_store — append-only queue and status store.

All tests are hermetic (tmp_path fixtures; never touch real log files).

Safety invariants verified:
  - Enqueued jobs always carry no_auto_trade=True, no_order_quantity=True,
    brokerage_connection=False
  - Sensitive keys (api_key / token / secret / ...) are scrubbed from stored args
  - Unknown job_type raises ValueError
  - Invalid status raises ValueError
  - Duplicate active jobs are not re-enqueued (idempotency)
  - get_queued_jobs returns only QUEUED jobs in creation order
  - update_job_status is append-only; latest entry wins
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.job_store import (
    DEFAULT_JOB_QUEUE_PATH,
    DEFAULT_JOB_STATUS_PATH,
    JOB_TYPES,
    MARKET_REFERENCE_SYMBOLS,
    enqueue_job,
    get_job_status,
    get_queued_jobs,
    update_job_status,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _qp(tmp_path: Path) -> str:
    return str(tmp_path / "logs" / "job_queue.jsonl")


def _sp(tmp_path: Path) -> str:
    return str(tmp_path / "logs" / "job_status.jsonl")


def _enqueue(tmp_path: Path, job_type: str = "update_market_data", **kw) -> dict:
    return enqueue_job(
        job_type,
        kw.pop("requested_by", "U_TEST"),
        kw.pop("args", {}),
        queue_path=_qp(tmp_path),
        status_path=_sp(tmp_path),
        **kw,
    )


# ── JOB_TYPES / MARKET_REFERENCE_SYMBOLS constants ───────────────────────────

def test_job_types_whitelist():
    assert "candidate_review"   in JOB_TYPES
    assert "signal_check"       in JOB_TYPES
    assert "update_market_data" in JOB_TYPES
    assert "daily_signal_check" in JOB_TYPES
    # No unknown types
    assert "exec_trade" not in JOB_TYPES
    assert "buy_etf"    not in JOB_TYPES


def test_market_reference_symbols_present():
    for sym in ("SPY", "QQQ", "VIX", "DXY", "US10Y"):
        assert sym in MARKET_REFERENCE_SYMBOLS


# ── enqueue_job ───────────────────────────────────────────────────────────────

def test_enqueue_job_returns_queued_job(tmp_path):
    job = _enqueue(tmp_path, "update_market_data")
    assert job["status"] == "QUEUED"
    assert job["job_type"] == "update_market_data"
    assert "job_id" in job
    assert job["no_auto_trade"] is True
    assert job["no_order_quantity"] is True
    assert job["brokerage_connection"] is False


def test_enqueue_job_writes_queue_and_status_files(tmp_path):
    job = _enqueue(tmp_path, "update_market_data")
    qp = Path(_qp(tmp_path))
    sp = Path(_sp(tmp_path))
    assert qp.exists()
    assert sp.exists()
    # Both files contain the job_id
    assert job["job_id"] in qp.read_text(encoding="utf-8")
    assert job["job_id"] in sp.read_text(encoding="utf-8")


def test_enqueue_job_unknown_type_raises(tmp_path):
    with pytest.raises(ValueError, match="Unknown job_type"):
        _enqueue(tmp_path, "exec_trade")


def test_enqueue_job_scrubs_sensitive_args(tmp_path):
    job = _enqueue(tmp_path, "update_market_data", args={
        "symbol": "ITA",
        "api_key": "sk-secret",
        "token": "bearer-abc",
        "safe_param": "ok",
    })
    stored_args = job["args"]
    assert "api_key" not in stored_args
    assert "token"   not in stored_args
    assert stored_args.get("safe_param") == "ok"


def test_enqueue_job_sensitive_not_in_log_file(tmp_path):
    _enqueue(tmp_path, "update_market_data", args={"api_key": "sk-real-secret"})
    qp_text = Path(_qp(tmp_path)).read_text(encoding="utf-8")
    sp_text = Path(_sp(tmp_path)).read_text(encoding="utf-8")
    assert "sk-real-secret" not in qp_text
    assert "sk-real-secret" not in sp_text
    assert "api_key" not in qp_text
    assert "api_key" not in sp_text


def test_enqueue_job_idempotency_same_type_and_symbol(tmp_path):
    """Second enqueue with same type+symbol returns existing QUEUED job (no new entry)."""
    job1 = _enqueue(tmp_path, "candidate_review", args={"symbol": "ITA"})
    job2 = _enqueue(tmp_path, "candidate_review", args={"symbol": "ITA"})
    assert job1["job_id"] == job2["job_id"]
    # Only one entry in queue log
    lines = [
        l for l in Path(_qp(tmp_path)).read_text(encoding="utf-8").splitlines() if l.strip()
    ]
    assert len(lines) == 1


def test_enqueue_job_no_idempotency_for_different_symbols(tmp_path):
    job1 = _enqueue(tmp_path, "candidate_review", args={"symbol": "ITA"})
    job2 = _enqueue(tmp_path, "candidate_review", args={"symbol": "QTUM"})
    assert job1["job_id"] != job2["job_id"]


def test_enqueue_job_idempotency_skipped_after_completion(tmp_path):
    """After job finishes (SUCCESS/FAILED), same type+symbol can be re-enqueued."""
    job1 = _enqueue(tmp_path, "candidate_review", args={"symbol": "ITA"})
    update_job_status(job1["job_id"], "SUCCESS", status_path=_sp(tmp_path))
    job2 = _enqueue(tmp_path, "candidate_review", args={"symbol": "ITA"})
    assert job1["job_id"] != job2["job_id"]  # new job created


def test_enqueue_job_no_auto_trade_field_present(tmp_path):
    job = _enqueue(tmp_path, "daily_signal_check")
    # Check the JSON in the queue file
    lines = [
        l for l in Path(_qp(tmp_path)).read_text(encoding="utf-8").splitlines() if l.strip()
    ]
    stored = json.loads(lines[0])
    assert stored.get("no_auto_trade") is True
    assert stored.get("no_order_quantity") is True
    assert stored.get("brokerage_connection") is False


# ── update_job_status ─────────────────────────────────────────────────────────

def test_update_job_status_valid(tmp_path):
    job = _enqueue(tmp_path, "update_market_data")
    update_job_status(
        job["job_id"], "RUNNING",
        started_at="2026-06-06T10:00:00+09:00",
        status_path=_sp(tmp_path),
    )
    result = get_job_status(
        job["job_id"],
        queue_path=_qp(tmp_path),
        status_path=_sp(tmp_path),
    )
    assert result["status"] == "RUNNING"
    assert result["started_at"] == "2026-06-06T10:00:00+09:00"


def test_update_job_status_invalid_raises(tmp_path):
    job = _enqueue(tmp_path, "update_market_data")
    with pytest.raises(ValueError, match="Invalid status"):
        update_job_status(job["job_id"], "BUY_NOW", status_path=_sp(tmp_path))


def test_update_job_status_latest_wins(tmp_path):
    """Latest status entry is authoritative (append-only log)."""
    job = _enqueue(tmp_path, "update_market_data")
    jid = job["job_id"]
    sp = _sp(tmp_path)
    update_job_status(jid, "RUNNING",  status_path=sp)
    update_job_status(jid, "SUCCESS", result_summary="done", status_path=sp)
    update_job_status(jid, "FAILED",  error_summary="oops", status_path=sp)

    result = get_job_status(jid, queue_path=_qp(tmp_path), status_path=sp)
    # FAILED is the latest
    assert result["status"] == "FAILED"
    assert result["error_summary"] == "oops"


def test_update_job_status_no_auto_trade_in_log(tmp_path):
    job = _enqueue(tmp_path, "update_market_data")
    update_job_status(job["job_id"], "SUCCESS", status_path=_sp(tmp_path))
    sp_text = Path(_sp(tmp_path)).read_text(encoding="utf-8")
    entries = [json.loads(l) for l in sp_text.splitlines() if l.strip()]
    # Every status entry must carry the safety flags
    for entry in entries:
        assert entry.get("no_auto_trade") is True
        assert entry.get("no_order_quantity") is True


# ── get_job_status ────────────────────────────────────────────────────────────

def test_get_job_status_returns_none_for_unknown(tmp_path):
    result = get_job_status(
        "00000000-0000-0000-0000-000000000000",
        queue_path=_qp(tmp_path),
        status_path=_sp(tmp_path),
    )
    assert result is None


def test_get_job_status_returns_queued(tmp_path):
    job = _enqueue(tmp_path, "update_market_data")
    result = get_job_status(
        job["job_id"],
        queue_path=_qp(tmp_path),
        status_path=_sp(tmp_path),
    )
    assert result is not None
    assert result["status"] == "QUEUED"
    assert result["no_auto_trade"] is True


# ── get_queued_jobs ───────────────────────────────────────────────────────────

def test_get_queued_jobs_returns_only_queued(tmp_path):
    j1 = _enqueue(tmp_path, "update_market_data")
    j2 = _enqueue(tmp_path, "daily_signal_check", check_duplicate=False)
    update_job_status(j1["job_id"], "SUCCESS", status_path=_sp(tmp_path))

    queued = get_queued_jobs(queue_path=_qp(tmp_path), status_path=_sp(tmp_path))
    ids = [j["job_id"] for j in queued]
    assert j1["job_id"] not in ids
    assert j2["job_id"] in ids


def test_get_queued_jobs_creation_order(tmp_path):
    ids = []
    for _ in range(3):
        j = _enqueue(
            tmp_path, "update_market_data",
            check_duplicate=False,
        )
        ids.append(j["job_id"])

    queued = get_queued_jobs(queue_path=_qp(tmp_path), status_path=_sp(tmp_path))
    returned_ids = [j["job_id"] for j in queued]
    assert returned_ids == ids


def test_get_queued_jobs_empty_when_none(tmp_path):
    queued = get_queued_jobs(queue_path=_qp(tmp_path), status_path=_sp(tmp_path))
    assert queued == []


def test_get_queued_jobs_skips_running_and_finished(tmp_path):
    j_running = _enqueue(tmp_path, "update_market_data", check_duplicate=False)
    j_success = _enqueue(tmp_path, "daily_signal_check", check_duplicate=False)
    j_queued  = _enqueue(tmp_path, "candidate_review", args={"symbol": "ITA"})

    sp = _sp(tmp_path)
    update_job_status(j_running["job_id"], "RUNNING", status_path=sp)
    update_job_status(j_success["job_id"], "SUCCESS", status_path=sp)

    queued = get_queued_jobs(queue_path=_qp(tmp_path), status_path=sp)
    ids = [j["job_id"] for j in queued]
    assert j_running["job_id"] not in ids
    assert j_success["job_id"] not in ids
    assert j_queued["job_id"] in ids
