# Synology DS224+ セットアップガイド

ETF Rotation Bot を Synology DS224+ NAS 上で直接実行（Docker 不使用）するための手順です。

## 前提条件

| 項目 | 要件 |
|---|---|
| DSM バージョン | DSM 7.x |
| Python | 3.8.x 以上（`python3 --version` で確認） |
| ストレージ | プロジェクト用に 1 GB 以上の空き容量 |
| ネットワーク | yfinance / Anthropic API への外部アクセス |

> **注意:** Synology DS224+ の標準 Python は 3.8.x です。本リポジトリは  
> `from __future__ import annotations` と `eval_type_backport` により  
> Python 3.8 互換で動作します。

---

## 1. プロジェクト配置

```bash
# SSH でログイン
ssh HyodoAdmin@<NAS-IP>

# アプリ用ディレクトリ作成
mkdir -p /var/services/homes/HyodoAdmin/apps
cd /var/services/homes/HyodoAdmin/apps

# リポジトリをクローン（またはファイル転送）
git clone https://github.com/<your-repo>/etf-rotation-bot.git
cd etf-rotation-bot
```

標準 APP_DIR:
```
/var/services/homes/HyodoAdmin/apps/etf-rotation-bot
```

---

## 2. 仮想環境の作成

```bash
cd /var/services/homes/HyodoAdmin/apps/etf-rotation-bot

# 仮想環境作成
python3 -m venv .venv

# アクティベート
source .venv/bin/activate

# Python バージョン確認
python --version
```

---

## 3. パッケージインストール

```bash
# セットアップスクリプトを実行（Python 3.8 互換パッケージも含む）
bash scripts/setup_synology.sh
```

手動でインストールする場合:

```bash
pip install --upgrade pip
pip install eval_type_backport       # Pydantic v2 / Python 3.8 対応
pip install "multitasking<0.0.12"   # yfinance 互換制約
pip install -r requirements.txt
```

---

## 4. 環境変数の設定

```bash
cp .env.example .env
vi .env   # または nano .env
```

必須設定項目:

```env
# AI 監査 (任意 — --no-ai-audit で省略可)
ANTHROPIC_API_KEY=sk-ant-...

# Slack Bot (任意 — Slack 連携を使う場合)
SLACK_BOT_TOKEN=xoxb-...
SLACK_APP_TOKEN=xapp-...
SLACK_ALLOWED_USER_IDS=U1234567,U7654321

# タイムゾーン
TZ=Asia/Tokyo
```

> `.env` は `.gitignore` で管理対象外です。ファイルをリポジトリに含めないでください。

---

## 5. 動作確認

```bash
source .venv/bin/activate
bash scripts/check_synology.sh
```

全項目 OK になれば実行可能です。

---

## 6. 手動実行

```bash
source .venv/bin/activate
cd /var/services/homes/HyodoAdmin/apps/etf-rotation-bot

# 基本実行（Slack 通知あり）
bash scripts/run_synology.sh

# Slack 通知なし
bash scripts/run_synology.sh --no-slack

# AI 監査なし
bash scripts/run_synology.sh --no-slack --no-ai-audit

# 市場データ取得をスキップ（高速再実行）
bash scripts/run_synology.sh --no-slack --skip-market-data
```

出力ファイル:
- `outputs/report_YYYY-MM-DD.md` — シグナルレポート
- `logs/daily_signal_check_console.log` — コンソールログ（追記）
- `logs/scheduler_run_log.jsonl` — 実行ログ

---

## 7. Synology タスクスケジューラ登録（自動実行）

DSM の「タスクスケジューラ」でデイリー実行を設定します。

### 設定手順

1. DSM → コントロールパネル → タスクスケジューラ → 「作成」→「スケジュールタスク」→「ユーザー定義スクリプト」
2. 以下を設定:

| 項目 | 値 |
|---|---|
| タスク名 | ETF Rotation Bot Daily |
| ユーザー | HyodoAdmin |
| スケジュール | 毎日 06:30（市場開場前） |
| 通知 | 異常終了時にメール通知（任意） |

3. スクリプト欄に入力:

```bash
source /var/services/homes/HyodoAdmin/apps/etf-rotation-bot/.venv/bin/activate
bash /var/services/homes/HyodoAdmin/apps/etf-rotation-bot/scripts/run_synology.sh --no-slack
```

> Slack 通知を使う場合は `--no-slack` を省いてください。

---

## 8. ログ確認

```bash
# コンソールログ（直近の実行）
tail -50 logs/daily_signal_check_console.log

# スケジューラ実行履歴
cat logs/scheduler_run_log.jsonl | python3 -m json.tool | tail -30

# 最新レポート
ls -lt outputs/*.md | head -5
```

---

## 9. Python 3.8 互換性について

本リポジトリは以下の対応を行っています:

| 対応内容 | 理由 |
|---|---|
| 全 `.py` ファイルに `from __future__ import annotations` を追加 | `list[str]`・`str \| None` 等の型ヒントを Python 3.8 で利用可能にする |
| `eval_type_backport` をインストール | Pydantic v2 が内部で利用する `typing.get_type_hints()` を 3.8 で動作させる |
| `multitasking<0.0.12` をインストール | yfinance が依存する `multitasking` の Python 3.8 非互換バージョンを回避 |

Python 3.10 以降では上記の対応は影響せず、通常通り動作します。

---

## 10. トラブルシューティング

### `TypeError: 'type' object is not subscriptable`

`from __future__ import annotations` が不足しているファイルがある場合に発生します。

```bash
python3 -c "import src.config_loader" 2>&1
```

### `ModuleNotFoundError: No module named 'eval_type_backport'`

```bash
source .venv/bin/activate
pip install eval_type_backport
```

### `multitasking` 関連エラー

```bash
pip install "multitasking<0.0.12"
```

### `yfinance` でデータ取得失敗

NAS の外部ネットワーク設定を確認してください。ファイアウォールで `443/TCP` への外部アクセスが必要です。

### `.env` が読み込まれない

```bash
ls -la .env   # ファイルが存在するか確認
head -3 .env  # 内容確認（秘密情報に注意）
```

---

## 安全性確認

| 確認項目 | 状態 |
|---|---|
| 自動売買 | **なし** — 推奨のみ |
| 注文数量計算 | **なし** |
| 証券口座連携 | **なし** |
| watchlist.csv の自動更新 | **なし**（デフォルト dry-run） |
| NISA 使用判断の自動化 | **なし** |
| 秘密鍵のリポジトリ保存 | **なし**（.gitignore で除外） |
