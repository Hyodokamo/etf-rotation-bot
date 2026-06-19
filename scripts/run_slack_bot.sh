#!/usr/bin/env bash
# Phase 7.4: run_slack_bot.sh — Start the Slack Socket Mode listener directly (non-Docker).
#
# Usage:
#   bash scripts/run_slack_bot.sh            # start normally
#   bash scripts/run_slack_bot.sh --dry-run  # validate payload without connecting
#
# Requires: SLACK_BOT_TOKEN and SLACK_APP_TOKEN in .env
#
# Safety:
#   - No auto-trade, no order quantity, no brokerage connection
#   - Shell=True is never used inside the handler
#   - --allow-watchlist-update cannot be triggered via Slack commands
#
# Recommended usage: Use docker-compose (docker compose up -d slack-bot) instead.
# This script is for direct / development runs without Docker.

set -euo pipefail

# Resolve project root (parent of this script's directory)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

# Load .env if present
if [ -f ".env" ]; then
    # shellcheck disable=SC2046
    export $(grep -v '^#' .env | grep -v '^$' | xargs)
fi

# Validate required tokens
if [ -z "${SLACK_BOT_TOKEN:-}" ] || [ -z "${SLACK_APP_TOKEN:-}" ]; then
    echo "[run_slack_bot.sh] ERROR: SLACK_BOT_TOKEN and SLACK_APP_TOKEN must be set in .env"
    echo "  cp .env.example .env && nano .env"
    exit 1
fi

echo "[run_slack_bot.sh] Starting Slack Socket Mode listener..."
echo "[run_slack_bot.sh] No auto-trade / No order quantity / No brokerage connection"

exec python src/slack_interaction_handler.py "$@"
