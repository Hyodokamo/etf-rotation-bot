#!/usr/bin/env bash
# ============================================================
# ETF Rotation Bot -- Synology DS224+ セットアップスクリプト
#
# Synology DSM の標準 Python は 3.8 系のため、以下を行います:
#   1. pip を最新化
#   2. eval_type_backport (Pydantic v2 on Python 3.8 対応)
#   3. multitasking<0.0.12 (yfinance との互換制約)
#   4. requirements.txt の残りパッケージ
#
# 実行前に:
#   python3 --version  # 3.8.x を確認
#   python3 -m venv /var/services/homes/HyodoAdmin/apps/etf-rotation-bot/.venv
#   source /var/services/homes/HyodoAdmin/apps/etf-rotation-bot/.venv/bin/activate
#
# 使用方法:
#   bash scripts/setup_synology.sh
#
# 安全性:
#   - 自動売買なし / 注文数量計算なし / 証券口座連携なし
#   - .env はリポジトリに含めない (cp .env.example .env して設定)
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${PROJECT_ROOT}"

PYTHON="${PYTHON:-python3}"
echo "[setup_synology] Python: $("${PYTHON}" --version)"
echo "[setup_synology] Project root: ${PROJECT_ROOT}"

# pip を最新化
"${PYTHON}" -m pip install --upgrade pip

# Python 3.8 互換パッケージを先にインストール
echo "[setup_synology] Installing Python 3.8 compatibility packages..."
"${PYTHON}" -m pip install "eval_type_backport"
"${PYTHON}" -m pip install "multitasking<0.0.12"

# 残りを requirements.txt からインストール
echo "[setup_synology] Installing requirements.txt..."
"${PYTHON}" -m pip install -r requirements.txt

# .env チェック
if [ ! -f ".env" ]; then
    echo ""
    echo "[setup_synology] WARNING: .env が見つかりません。"
    echo "  cp .env.example .env"
    echo "  # ANTHROPIC_API_KEY / SLACK_BOT_TOKEN / SLACK_APP_TOKEN 等を設定してください"
fi

echo ""
echo "[setup_synology] セットアップ完了。次のコマンドで動作確認してください:"
echo "  bash scripts/check_synology.sh"
