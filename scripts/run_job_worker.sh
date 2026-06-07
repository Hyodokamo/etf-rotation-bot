#!/usr/bin/env bash
# Phase 7.4: run_job_worker.sh — Start the async job runner directly (non-Docker).
#
# Usage:
#   bash scripts/run_job_worker.sh                    # one-shot (process current queue)
#   bash scripts/run_job_worker.sh --loop             # continuous polling (10 s interval)
#   bash scripts/run_job_worker.sh --loop --interval 30
#
# Safety:
#   - No auto-trade, no order quantity, no brokerage connection
#   - All job commands use fixed whitelisted command lists (no shell=True)
#   - --allow-watchlist-update is NEVER passed to any job
#   - Lock file (logs/.job_runner.lock) prevents concurrent runners
#
# Recommended usage: Use docker-compose (docker compose up -d job-runner) instead.
# This script is for direct / development runs without Docker.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

# Load .env if present
if [ -f ".env" ]; then
    # shellcheck disable=SC2046
    export $(grep -v '^#' .env | grep -v '^$' | xargs)
fi

# Ensure logs directory exists
mkdir -p logs

echo "[run_job_worker.sh] Starting job worker..."
echo "[run_job_worker.sh] No auto-trade / No order quantity / No brokerage connection"
echo "[run_job_worker.sh] --allow-watchlist-update is never passed to any job"

exec python scripts/run_job_worker.py "$@"
