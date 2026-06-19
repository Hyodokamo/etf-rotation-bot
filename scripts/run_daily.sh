#!/usr/bin/env bash
# ============================================================
# ETF Rotation Bot -- Daily Signal Check (Linux / NAS)
# Docker / Synology / cron entry point
#
# Advisory only: no orders, no auto-trade, no brokerage.
# Default mode: watchlist.csv is NOT updated (--dry-run).
# Use --allow-watchlist-update only for deliberate manual runs.
#
# Usage:
#   bash scripts/run_daily.sh                               # full run with Slack
#   bash scripts/run_daily.sh --no-slack                    # suppress Slack
#   bash scripts/run_daily.sh --skip-market-data --no-slack # fast re-run
#   bash scripts/run_daily.sh --allow-watchlist-update      # CAUTION: updates watchlist.csv
# ============================================================
set -euo pipefail

# Move to project root (parent of scripts/)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${PROJECT_ROOT}"

# Ensure logs directory exists
mkdir -p logs

# Combined rolling console log (appended on every run)
CONSOLE_LOG="logs/daily_signal_check_console.log"

TIMESTAMP="$(date '+%Y-%m-%d %H:%M:%S')"
DIVIDER="============================================================"

{
  echo "${DIVIDER}"
  echo "[${TIMESTAMP}] Daily Signal Check starting"
  echo "Project root : ${PROJECT_ROOT}"
  echo "Arguments    : $*"
  echo "Advisory only: no orders / no auto-trade / no brokerage"
  echo "${DIVIDER}"
} | tee -a "${CONSOLE_LOG}"

# Run the orchestrator, forwarding all arguments
# stdout + stderr both go to console AND are appended to the log
python scripts/daily_signal_check.py "$@" 2>&1 | tee -a "${CONSOLE_LOG}"
EXIT_CODE="${PIPESTATUS[0]}"

{
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] Daily Signal Check finished (exit=${EXIT_CODE})"
  echo "${DIVIDER}"
} | tee -a "${CONSOLE_LOG}"

exit "${EXIT_CODE}"
