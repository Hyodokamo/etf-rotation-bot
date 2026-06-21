#!/usr/bin/env bash
# ============================================================
# ETF Rotation Bot -- Synology DS224+ 動作確認スクリプト
#
# Python バージョン・主要パッケージ・設定ファイルを確認し、
# --no-ai-audit でのドライランで基本動作を検証します。
#
# 使用方法:
#   bash scripts/check_synology.sh
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${PROJECT_ROOT}"

PYTHON="${PYTHON:-python3}"
OK=0
FAIL=0

pass() { echo "  [OK]  $1"; OK=$((OK + 1)); }
fail() { echo "  [NG]  $1"; FAIL=$((FAIL + 1)); }

echo "============================================================"
echo "  ETF Rotation Bot — Synology 動作確認"
echo "  $(date '+%Y-%m-%d %H:%M:%S')"
echo "============================================================"

# Python バージョン
PYVER="$("${PYTHON}" --version 2>&1)"
echo ""
echo "--- Python ---"
echo "  ${PYVER}"
MAJOR_MINOR="$(echo "${PYVER}" | grep -oP '\d+\.\d+' | head -1)"
if python3 -c "import sys; sys.exit(0 if sys.version_info >= (3,8) else 1)" 2>/dev/null; then
    pass "Python >= 3.8"
else
    fail "Python < 3.8 (非対応)"
fi

# 必須パッケージ
echo ""
echo "--- パッケージ確認 ---"
check_pkg() {
    if "${PYTHON}" -c "import $1" 2>/dev/null; then
        pass "$1"
    else
        fail "$1 (未インストール)"
    fi
}
check_pkg pandas
check_pkg numpy
check_pkg yfinance
check_pkg yaml
check_pkg pydantic
check_pkg dotenv
check_pkg anthropic
check_pkg openai

# eval_type_backport (Python 3.8 互換用)
if "${PYTHON}" -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" 2>/dev/null; then
    pass "eval_type_backport (Python 3.10+: 不要)"
elif "${PYTHON}" -c "import eval_type_backport" 2>/dev/null; then
    pass "eval_type_backport"
else
    fail "eval_type_backport (Python 3.8/3.9 で必須: pip install eval_type_backport)"
fi

# 設定ファイル
echo ""
echo "--- 設定ファイル確認 ---"
for f in config.yaml .env; do
    if [ -f "${f}" ]; then
        pass "${f}"
    else
        fail "${f} (存在しない — cp .env.example .env して設定)"
    fi
done

# データディレクトリ
echo ""
echo "--- ディレクトリ確認 ---"
for d in data logs outputs reports; do
    mkdir -p "${d}"
    pass "${d}/ (作成済み or 既存)"
done

# 構文チェック (Python import のみ)
echo ""
echo "--- 構文チェック (import) ---"
if "${PYTHON}" -c "import src.config_loader, src.data_fetcher, src.signals.crash_detector" 2>/dev/null; then
    pass "主要モジュール import"
else
    fail "主要モジュール import 失敗 (詳細: python3 -c 'import src.config_loader')"
fi

# スモーク確認 (オフライン): --help は全モジュールを import するため、
# Python 3.8 での型ヒント / pydantic / eval_type_backport 互換を実データ取得なしで検証できる。
echo ""
echo "--- スモーク確認 (main.py import) ---"
if "${PYTHON}" main.py --help >/dev/null 2>&1; then
    pass "main.py --help (全モジュール import OK)"
else
    fail "main.py --help 失敗 (import / Python 3.8 互換の問題: python3 main.py --help で詳細確認)"
fi

# 結果
echo ""
echo "============================================================"
echo "  結果: OK=${OK}  NG=${FAIL}"
if [ "${FAIL}" -eq 0 ]; then
    echo "  全チェック通過。run_synology.sh で実行できます。"
else
    echo "  NG 項目を確認してください。"
    echo "  セットアップ: bash scripts/setup_synology.sh"
fi
echo "============================================================"

exit "${FAIL}"
