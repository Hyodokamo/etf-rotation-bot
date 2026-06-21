#!/usr/bin/env bash
# ============================================================
# ETF Rotation Bot -- Synology DS224+ 実行スクリプト
#
# Synology DSM 上でデイリーシグナルチェックを実行します。
# Docker を使わずに直接 Python で実行する場合に使用します。
#
# 標準 APP_DIR:
#   /var/services/homes/HyodoAdmin/apps/etf-rotation-bot
#
# 使用方法:
#   bash scripts/run_synology.sh                   # Slack 通知あり
#   bash scripts/run_synology.sh --no-slack        # Slack 通知なし
#   bash scripts/run_synology.sh --skip-market-data --no-slack
#
# Synology タスクスケジューラ登録例:
#   ユーザー: HyodoAdmin
#   スクリプト:
#     source /var/services/homes/HyodoAdmin/apps/etf-rotation-bot/.venv/bin/activate
#     bash /var/services/homes/HyodoAdmin/apps/etf-rotation-bot/scripts/run_synology.sh --no-slack
#
# 安全性:
#   - 自動売買なし / 注文数量計算なし / 証券口座連携なし
#   - デフォルトでは watchlist.csv を更新しない (--dry-run)
#   - --allow-watchlist-update は意図的な手動実行時のみ使用
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${PROJECT_ROOT}"

PYTHON="${PYTHON:-python3}"
mkdir -p logs

CONSOLE_LOG="logs/daily_signal_check_console.log"
TIMESTAMP="$(date '+%Y-%m-%d %H:%M:%S')"
DIVIDER="============================================================"

{
    echo "${DIVIDER}"
    echo "[${TIMESTAMP}] [run_synology] Daily Signal Check starting"
    echo "Python       : $("${PYTHON}" --version 2>&1)"
    echo "Project root : ${PROJECT_ROOT}"
    echo "Arguments    : ${*:-<none>}"
    echo "Advisory only: 自動売買なし / 注文数量計算なし / 証券口座連携なし"
    echo "${DIVIDER}"
} | tee -a "${CONSOLE_LOG}"

"${PYTHON}" scripts/daily_signal_check.py "$@" 2>&1 | tee -a "${CONSOLE_LOG}"
EXIT_CODE="${PIPESTATUS[0]}"

{
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [run_synology] finished (exit=${EXIT_CODE})"
    echo "${DIVIDER}"
} | tee -a "${CONSOLE_LOG}"

exit "${EXIT_CODE}"
