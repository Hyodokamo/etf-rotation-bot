"""Phase 7.3: Job Worker CLI — processes jobs from the async queue.

Usage:
    python scripts/run_job_worker.py           # process all queued jobs once
    python scripts/run_job_worker.py --loop    # poll continuously (blocks)
    python scripts/run_job_worker.py --loop --interval 30

Safety:
    - No auto-trade, no order quantity, no brokerage connection.
    - --allow-watchlist-update is never passed to any job.
    - All subprocess calls use fixed whitelisted command lists (no shell=True).
"""
from __future__ import annotations

import argparse
import os
import sys

# Allow `python scripts/run_job_worker.py` to import `src.*`.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.job_runner import (
    DEFAULT_LOCK_PATH,
    DEFAULT_RUN_INTERVAL_SECONDS,
    run_loop,
    run_once,
)
from src.logger import logger


def main() -> None:
    parser = argparse.ArgumentParser(
        description="ETF Rotation Bot — async job worker (Phase 7.3)"
    )
    parser.add_argument(
        "--loop",
        action="store_true",
        default=False,
        help="Run continuously, polling for new jobs every --interval seconds.",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=DEFAULT_RUN_INTERVAL_SECONDS,
        help=f"Poll interval in seconds when --loop is active (default: {DEFAULT_RUN_INTERVAL_SECONDS}).",
    )
    parser.add_argument(
        "--queue-path",
        default="logs/job_queue.jsonl",
        help="Path to job_queue.jsonl (default: logs/job_queue.jsonl).",
    )
    parser.add_argument(
        "--status-path",
        default="logs/job_status.jsonl",
        help="Path to job_status.jsonl (default: logs/job_status.jsonl).",
    )
    parser.add_argument(
        "--lock-path",
        default=DEFAULT_LOCK_PATH,
        help=f"Path to lock file (default: {DEFAULT_LOCK_PATH}).",
    )
    args = parser.parse_args()

    if args.loop:
        logger.info(
            f"[job_worker] starting loop mode (interval={args.interval}s, "
            f"lock={args.lock_path})"
        )
        run_loop(
            queue_path=args.queue_path,
            status_path=args.status_path,
            lock_path=args.lock_path,
            interval_seconds=args.interval,
        )
    else:
        count = run_once(
            queue_path=args.queue_path,
            status_path=args.status_path,
            lock_path=args.lock_path,
        )
        logger.info(f"[job_worker] run_once: processed {count} job(s)")
        print(f"Processed {count} job(s).")


if __name__ == "__main__":
    main()
