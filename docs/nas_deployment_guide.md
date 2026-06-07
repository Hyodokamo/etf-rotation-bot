# ETF Rotation Bot — NAS Deployment Guide

**Phase 7.0 — NAS / Docker 実行基盤**

> 本Botはアドバイザリーのみ。実注文なし、注文数量計算なし、自動売買なし、証券口座連携なし。  
> 最終判断は常に人間が行います。

---

## 1. 概要

本ガイドでは、ETF Rotation Bot を **NAS（Synology / Linux）上のDockerコンテナ**で安全に常時/定期実行するための手順を説明します。

**方針:**
- NAS を実行基盤、Slack を通知・確認 UI として使う
- 個人データ・シークレットはコンテナ image に含めない
- `data/` `logs/` `reports/` `outputs/` はホストの bind-mount で永続化する
- watchlist.csv の自動更新はデフォルトで行わない（dry-run）

> **Phase 7.4 更新:**  
> Slack コマンドルーター (`/etf` slash commands) と Job Runner が実装されました（Phase 7.1–7.4）。  
> `docker compose up -d slack-bot job-runner` で常駐サービスとして起動できます。  
> 詳細は [セクション 12: 常駐サービス運用（Slack Bot + Job Runner）](#12-常駐サービス運用slack-bot--job-runner) を参照してください。

---

## 2. 前提条件

### 必要なもの

| 要件 | 内容 |
|------|------|
| Docker / Container Manager | Synology NAS: Container Manager アプリ / Linux: Docker Engine + Compose v2 |
| Python 3.11 以上 | Docker image 内に含まれるため NAS 側での別途インストール不要 |
| `.env` ファイル | プロジェクトルートに配置（後述） |
| 個人 CSV | `data/` 以下に配置（`total_portfolio_snapshot.csv` 等） |

### 必要な環境変数（`.env`）

```bash
# .env.example をコピーして値を設定する
cp .env.example .env
```

最低限必要なもの:
- `ANTHROPIC_API_KEY` — Claude AI 委員会・Crash Signal 評価に必要
- `SLACK_WEBHOOK_URL` — Slack 通知先（未設定時はコンソール出力のみ）

任意（Slack インタラクション機能・Phase 4.1〜）:
- `SLACK_BOT_TOKEN` / `SLACK_APP_TOKEN` — Socket Mode 用
- `SLACK_ALLOWED_USER_IDS` — 許可ユーザー ID（カンマ区切り）
- `SLACK_CHANNEL_ID` — ボタン付きメッセージ投稿先チャンネル

---

## 3. ディレクトリ構成と役割

```
etf-rotation-bot/          ← プロジェクトルート（Git 管理）
├── Dockerfile
├── docker-compose.yml
├── .env                   ← ホストに配置（Git 管理外）
├── .env.example
├── requirements.txt
├── main.py
├── scripts/
│   ├── daily_signal_check.py
│   └── run_daily.sh       ← Linux/NAS 用実行スクリプト
├── src/
├── config/
├── data/                  ← 【bind-mount】個人CSVとマスタデータ
│   ├── etf_master.csv     ← Git管理（設定マスタ）
│   ├── watchlist.csv      ← Git管理外（実行時state）
│   ├── market_data_latest.csv  ← Git管理外（日次キャッシュ）
│   ├── ai_sleeve_state.csv     ← Git管理外（個人）
│   ├── total_portfolio_snapshot.csv  ← Git管理外（個人）
│   └── archive/
├── logs/                  ← 【bind-mount】実行ログ（すべてGit管理外）
│   ├── scheduler_run_log.jsonl
│   ├── daily_signal_check_console.log
│   ├── signal_history.csv
│   └── signal_human_decision_log.jsonl
├── reports/               ← 【bind-mount】生成レポート（Git管理外）
│   └── daily_signal_report.md
└── outputs/               ← 【bind-mount】月次レポート・bot.log
```

| ディレクトリ | 役割 | NAS永続化 | Git管理 |
|-------------|------|----------|--------|
| `data/` | ETFマスタ・個人CSV・watchlist | ✅ 必須 | 一部のみ（etf_master.csv等） |
| `logs/` | 全実行ログ（追記専用） | ✅ 必須 | ❌ 除外 |
| `reports/` | 生成レポート（日次・月次） | ✅ 推奨 | ❌ 除外 |
| `outputs/` | 月次レポート・bot.log・committee JSON | ✅ 推奨 | ❌ 除外 |

---

## 4. Volume 設計

### なぜ bind-mount を使うか

Docker image は **不変（immutable）** にすることで安全性を確保します:
- `.env`・個人CSV・API key を image に含めない
- image を更新しても個人データが消えない
- 複数の実行（daily run / 手動 / job runner）が同じ `data/` `logs/` を共有

```
Host NAS                  Container
./data     ────mount────▶ /app/data
./logs     ────mount────▶ /app/logs
./reports  ────mount────▶ /app/reports
./outputs  ────mount────▶ /app/outputs
.env       ── env_file ──▶ 環境変数として注入（ファイルコピーなし）
```

---

## 5. セットアップ手順

### Step 1: リポジトリの準備

```bash
git clone <your-repo-url> etf-rotation-bot
cd etf-rotation-bot
```

### Step 2: .env の準備

```bash
cp .env.example .env
# エディタで .env を開いて値を設定する
nano .env
```

最低限設定する:
```
ANTHROPIC_API_KEY=sk-ant-...
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...
TZ=Asia/Tokyo
```

### Step 3: 個人 CSV の配置

Git 管理外の個人ファイルを手動で配置:

```bash
# 例: 別マシンから転送
scp total_portfolio_snapshot.csv nas:/path/to/etf-rotation-bot/data/
scp ai_sleeve_state.csv          nas:/path/to/etf-rotation-bot/data/
```

これらのファイルは `.gitignore` および `.dockerignore` で除外済みです。

### Step 4: Docker image のビルド

```bash
docker compose build
# または
docker build -t etf-bot .
```

**確認:** image に `.env` や個人 CSV が含まれていないこと:

```bash
docker run --rm etf-bot ls /app/data
# etf_master.csv と test_scenarios/ のみが表示されるべき
```

### Step 5: 設定の検証

```bash
docker compose config
# エラーが出ないことを確認
```

---

## 6. 手動実行例

### 基本実行（Slack なし・market data 更新なし）

```bash
docker compose run --rm etf-bot \
  python scripts/daily_signal_check.py --skip-market-data --no-slack
```

### フル実行（Slack あり）

```bash
docker compose run --rm etf-bot \
  python scripts/daily_signal_check.py
```

### Slack なしフル実行

```bash
docker compose run --rm etf-bot \
  python scripts/daily_signal_check.py --no-slack
```

### watchlist 更新あり（手動判断後のみ）

```bash
# CAUTION: data/watchlist.csv が更新されます。内容を確認してから使用。
docker compose run --rm etf-bot \
  python scripts/daily_signal_check.py --allow-watchlist-update --no-slack
```

### シングル ETF のシグナル確認

```bash
docker compose run --rm etf-bot \
  python main.py --crash-signal-check --signal-symbol ITA --dry-run --committee-on-trigger-only
```

### run_daily.sh 経由（コンテナ外・NAS 直接実行）

```bash
bash scripts/run_daily.sh --skip-market-data --no-slack
```

---

## 7. Synology Task Scheduler / cron 設定

### 推奨実行時刻

| 市場クローズ | 日本時間 |
|------------|--------|
| 米国市場 16:00 ET (冬/EST) | 翌日 06:00 JST |
| 米国市場 16:00 ET (夏/EDT) | 翌日 05:00 JST |
| **推奨実行時刻** | **平日 07:30 JST** |

### Synology Task Scheduler（Container Manager 使用）

Container Manager → プロジェクト → スケジュール:

```
スケジュール: 毎日 07:30
コマンド: docker compose run --rm etf-bot python scripts/daily_signal_check.py
作業ディレクトリ: /path/to/etf-rotation-bot
```

または **Synology タスクスケジューラ**（管理 → タスクスケジューラ）:

```
タスクの種類: ユーザー定義スクリプト
スケジュール: 毎日 07:30
スクリプト:
  cd /path/to/etf-rotation-bot
  bash scripts/run_daily.sh >> /path/to/etf-rotation-bot/logs/cron.log 2>&1
```

### Linux cron

```bash
# crontab -e で追記
# 平日 07:30 に実行
30 7 * * 1-5 cd /path/to/etf-rotation-bot && bash scripts/run_daily.sh >> logs/cron.log 2>&1

# または docker compose run を使う
30 7 * * 1-5 cd /path/to/etf-rotation-bot && docker compose run --rm etf-bot python scripts/daily_signal_check.py >> logs/cron.log 2>&1
```

---

## 8. ログ確認方法

### 日次実行サマリー

```bash
# 最新の実行結果
python -c "
import json
line = open('logs/scheduler_run_log.jsonl', encoding='utf-8').readlines()[-1]
d = json.loads(line)
print('status:', d['status'])
print('committee_target:', d['committee_target_count'])
print('global_only_skipped:', d['global_only_skipped_count'])
print('no_auto_trade:', d['no_auto_trade'])
"
```

### コンソールログ

```bash
tail -30 logs/daily_signal_check_console.log
```

### シグナルレポート

```bash
cat reports/daily_signal_report.md | head -50
```

---

## 9. 安全運用

### デフォルト動作

| 項目 | デフォルト | 変更方法 |
|------|-----------|--------|
| watchlist.csv 更新 | **しない（dry-run）** | `--allow-watchlist-update` |
| 注文数量計算 | **しない** | 変更不可（設計で禁止） |
| 自動売買 | **しない** | 変更不可（設計で禁止） |
| 証券口座連携 | **しない** | 変更不可（設計で禁止） |
| AI委員会スキップ | トリガーなしシンボルはスキップ | `--full-committee-scan` |

### 確認コマンド

```bash
# 最新 JSONL エントリで安全フラグを確認
python -c "
import json
d = json.loads(open('logs/scheduler_run_log.jsonl').readlines()[-1])
assert d['no_auto_trade'] is True
assert d['no_order_quantity'] is True
print('Safety OK:', d['status'])
"
```

---

## 10. Git 管理注意事項

以下は `.gitignore` および `.dockerignore` で除外済みです。**絶対に Git にコミットしないでください:**

| ファイル/ディレクトリ | 理由 |
|---------------------|------|
| `.env` | API key / Slack token を含む |
| `data/watchlist.csv` | 実行時 state（日々変わる） |
| `data/market_data_latest.csv` | 日次キャッシュ（日々再生成） |
| `data/total_portfolio_snapshot.csv` | 個人資産情報 |
| `data/ai_sleeve_state.csv` | 個人資産情報 |
| `data/archive/` | バックアップ（個人データ含む） |
| `logs/` | 実行ログ全般 |
| `reports/daily_signal_report.md` | 生成レポート |

コミット前確認:

```bash
git status --short
# data/*.csv や logs/* が ?? や M で表示されていないことを確認
```

---

## 11. トラブルシュート

### .env がない / 読み込まれない

```bash
# .env が存在するか確認
ls -la .env
# .env.example を参考に作成
cp .env.example .env && nano .env
```

### Slack 通知が来ない

```bash
# SLACK_WEBHOOK_URL が設定されているか確認
grep SLACK_WEBHOOK_URL .env
# テスト投稿
curl -X POST -H 'Content-type: application/json' \
  --data '{"text":"test"}' $SLACK_WEBHOOK_URL
```

### market data 取得失敗（タイムアウト / API エラー）

```bash
# market data をスキップして他ステップだけ実行
docker compose run --rm etf-bot \
  python scripts/daily_signal_check.py --skip-market-data --no-slack
```

### AI 委員会タイムアウト

- ANTHROPIC_API_KEY が設定されているか確認
- API の利用状況・レート制限を確認（Anthropic Console）
- `run_step` の timeout は 600 秒（デフォルト）。一時的な問題なら翌日自然解消

### PARTIAL_FAILURE 時の確認

```bash
python -c "
import json
lines = open('logs/scheduler_run_log.jsonl', encoding='utf-8').readlines()
d = json.loads(lines[-1])
print('Status:', d['status'])
for s in d['steps']:
    if s['exit_code'] != 0:
        print('Failed step:', s['command'][:60])
        print('  stderr:', (s.get('stderr_summary') or '')[:200])
"
```

### Docker image に個人データが入っている（確認方法）

```bash
docker run --rm etf-bot find /app/data -name "*.csv" | sort
# 期待: etf_master.csv と test_scenarios/*.csv のみ
# watchlist.csv / market_data_latest.csv / total_portfolio_snapshot.csv が表示されたら .dockerignore を見直す
```

---

## 12. 常駐サービス運用（Slack Bot + Job Runner）

**Phase 7.4 — NAS Runtime Services**

### 12-1. サービス概要

| サービス名 | 役割 | 起動方式 |
|-----------|------|--------|
| `slack-bot` | `/etf` Slack コマンドの受付 + インタラクティブボタン処理 | `restart: unless-stopped`（常駐） |
| `job-runner` | `job_queue.jsonl` の非同期処理（10 秒間隔ポーリング） | `restart: unless-stopped`（常駐） |
| `etf-bot` | 日次 daily run（手動 / cron / Task Scheduler 起動） | `docker compose run --rm`（非常駐） |

**通信方式:** `slack-bot` と `job-runner` は同一 bind-mount ボリューム（`logs/`）を共有します。  
Slack コマンドがジョブをエンキューすると `logs/job_queue.jsonl` に書き込まれ、  
`job-runner` が定期的に読み取って処理します。IPC なし・ポート不要。

**必要な `.env` 設定（Slack Bot 用）:**

```bash
SLACK_BOT_TOKEN=xoxb-...          # Bot OAuth Token
SLACK_APP_TOKEN=xapp-...          # App-Level Token (Socket Mode)
SLACK_ALLOWED_USER_IDS=U1234,U5678  # 許可ユーザー ID（省略可）
SLACK_CHANNEL_ID=C1234567890      # ボタン付きメッセージ送信チャンネル（省略可）
```

---

### 12-2. 常駐サービスの起動

```bash
# Image のビルド（初回または Dockerfile 変更後）
docker compose build

# slack-bot と job-runner を常駐起動
docker compose up -d slack-bot job-runner

# 起動確認
docker compose ps
# 両サービスが "Up" と表示されることを確認
```

個別起動:

```bash
docker compose up -d slack-bot   # Slack Bot のみ
docker compose up -d job-runner  # Job Runner のみ
```

---

### 12-3. 常駐サービスの停止

```bash
# 両サービスを停止
docker compose stop slack-bot job-runner

# 完全削除（コンテナ・ネットワーク）
docker compose down

# ボリュームは削除しない（bind-mount のため host 側は保持される）
```

---

### 12-4. ログの確認

#### Docker Compose ログ

```bash
# Slack Bot のリアルタイムログ
docker compose logs -f slack-bot

# Job Runner のリアルタイムログ
docker compose logs -f job-runner

# 直近 50 行
docker compose logs --tail=50 slack-bot
docker compose logs --tail=50 job-runner
```

#### ファイルログ

```bash
# Job キュー（エンキュー済みジョブ一覧）
tail -20 logs/job_queue.jsonl

# Job ステータス（最新状態確認）
tail -20 logs/job_status.jsonl

# 日次実行ログ
tail -20 logs/scheduler_run_log.jsonl

# シグナル人間判断ログ
tail -20 logs/signal_human_decision_log.jsonl
```

#### ジョブステータスのワンライナー確認

```bash
python -c "
import json
lines = open('logs/job_status.jsonl', encoding='utf-8').readlines()
for l in lines[-10:]:
    d = json.loads(l)
    print(d.get('job_id','')[:8], d.get('status'), d.get('updated_at','')[:16])
"
```

---

### 12-5. Slack Bot の動作確認

常駐起動後、Slack ワークスペースで以下を実行してください:

```
/etf help                   → コマンド一覧表示
/etf status                 → AI検証枠 現在状態
/etf signals                → 最新シグナルダイジェスト
/etf overdue                → 確認期限超過・近日確認
/etf candidate ITA          → 候補レビュージョブのエンキュー (dry-run)
/etf signal QTUM            → シグナル確認ジョブのエンキュー (dry-run)
/etf update-market-data     → market data 更新ジョブのエンキュー
/etf run daily              → 日次シグナルチェックジョブのエンキュー
/etf job <JOB_ID>           → ジョブの実行状態確認
```

**期待動作:**
- 全コマンドが ephemeral（自分だけ見える）メッセージで返答される
- `/etf candidate ITA` → Job ID が返る
- `/etf job <JOB_ID>` → QUEUED → RUNNING → SUCCESS/FAILED の状態変化が確認できる

---

### 12-6. Job Runner の動作確認

```bash
# job_queue.jsonl に QUEUED エントリが書き込まれていること
grep '"status": "QUEUED"' logs/job_queue.jsonl | tail -5

# job_status.jsonl で RUNNING → SUCCESS/FAILED に遷移していること
grep '"status"' logs/job_status.jsonl | tail -20

# ジョブが正常に完了したかチェック
python -c "
import json
lines = open('logs/job_status.jsonl', encoding='utf-8').readlines()
last = json.loads(lines[-1])
print('Job ID:', last['job_id'][:8])
print('Status:', last['status'])
print('no_auto_trade:', last.get('no_auto_trade'))
print('no_order_quantity:', last.get('no_order_quantity'))
"
```

**安全フラグの確認（全ジョブエントリに必須）:**

```bash
python -c "
import json
lines = open('logs/job_status.jsonl', encoding='utf-8').readlines()
for l in lines:
    d = json.loads(l)
    assert d.get('no_auto_trade') is True, f'no_auto_trade missing: {d}'
    assert d.get('no_order_quantity') is True, f'no_order_quantity missing: {d}'
print('Safety OK: all', len(lines), 'entries have no_auto_trade=True')
"
```

---

### 12-7. 日次実行（Task Scheduler / cron）

daily_signal_check は **常駐サービスではなく**、NAS Task Scheduler / cron から定期起動します。

#### Synology Task Scheduler

管理 → タスクスケジューラ → 作成 → ユーザー定義スクリプト:

```
スケジュール: 毎日 07:30（平日のみ）
スクリプト:
  cd /path/to/etf-rotation-bot
  docker compose run --rm etf-bot python scripts/daily_signal_check.py --no-slack
```

**重要:** `--allow-watchlist-update` は日次自動実行に使わないでください。  
watchlist の更新は Slack の `review` ボタン、または手動コマンドで行います。

#### Linux cron

```bash
# crontab -e で追記（平日 07:30 JST）
30 7 * * 1-5 cd /path/to/etf-rotation-bot && docker compose run --rm etf-bot python scripts/daily_signal_check.py --no-slack >> logs/cron.log 2>&1
```

#### docker compose run オプション例

```bash
# 基本（dry-run, Slack なし）
docker compose run --rm etf-bot python scripts/daily_signal_check.py --no-slack

# market data も更新する場合（フル実行）
docker compose run --rm etf-bot python scripts/daily_signal_check.py

# skip market data（既存データで実行）
docker compose run --rm etf-bot python scripts/daily_signal_check.py --skip-market-data --no-slack
```

---

### 12-8. 障害対応

#### Slack コマンドが返ってこない

```bash
# 1. Slack Bot のログを確認
docker compose logs --tail=50 slack-bot

# 2. サービスが起動しているか確認
docker compose ps slack-bot

# 3. SLACK_BOT_TOKEN / SLACK_APP_TOKEN が .env に設定されているか確認
grep -E "SLACK_(BOT|APP)_TOKEN" .env

# 4. 再起動
docker compose restart slack-bot
```

#### ジョブが QUEUED のまま進まない

```bash
# 1. Job Runner が起動しているか確認
docker compose ps job-runner

# 2. Job Runner のログを確認
docker compose logs --tail=50 job-runner

# 3. ロックファイルが残っていないか確認（前の runner が異常終了した場合）
cat logs/.job_runner.lock  # PID が表示される
# プロセスが存在しないなら削除して良い
rm -f logs/.job_runner.lock

# 4. Job Runner を再起動
docker compose restart job-runner
```

#### job-runner が落ちている（repeatedly exiting）

```bash
# Exit コードを確認
docker compose ps job-runner  # State: Exited (N)

# ログで原因を確認
docker compose logs job-runner

# よくある原因:
# - ANTHROPIC_API_KEY 未設定（candidate_review など AI 委員会ジョブが必要）
# - Python モジュールが見つからない → docker compose build でイメージを再ビルド
# - 文字コードエラー → Dockerfile や requirements.txt の見直し
```

#### Slack Token 不備

```bash
# Socket Mode には SLACK_BOT_TOKEN と SLACK_APP_TOKEN の両方が必要
grep -E "SLACK_(BOT|APP)_TOKEN" .env
# どちらかが空・未設定の場合、slack-bot は起動後すぐに終了します
# Slack App Settings で Socket Mode が有効になっているか確認してください
```

#### market data 取得失敗

```bash
# market data 更新をスキップして daily run
docker compose run --rm etf-bot \
  python scripts/daily_signal_check.py --skip-market-data --no-slack

# または job-runner 経由で market data のみ更新
# /etf update-market-data を Slack から実行
```

#### OpenAI / Claude API エラー

- `ANTHROPIC_API_KEY` の有効期限・クォータを確認（Anthropic Console）
- rate limit なら数分後に再実行
- committee が INSUFFICIENT_DATA を返しても日次 run 自体は SUCCESS になります

---

### 12-9. 安全運用まとめ

| 項目 | 保証内容 |
|------|---------|
| 自動売買なし | Bot は投資判断の補助のみ。注文は一切実行しません |
| 注文数量計算なし | 株数・口数の計算は行いません |
| 証券口座連携なし | 楽天証券・SBI 等への API 接続は実装されていません |
| 最終判断は人間 | USER_APPROVED は「候補として確認済み」であり「買付承認」ではありません |
| Slack からの任意実行なし | job_type は固定ホワイトリスト。Slack テキストをシェルに渡しません |
| shell=True 不使用 | 全 subprocess は固定コマンドリストで実行します |
| --allow-watchlist-update 禁止 | Slack / job-runner 経由では watchlist の自動更新は不可 |
| 秘密情報のログ除外 | api_key / token / secret 等のキーは JSONL ログから除外されます |
| no_auto_trade フラグ | 全ジョブエントリに `no_auto_trade: true` を記録します |
| no_order_quantity フラグ | 全ジョブエントリに `no_order_quantity: true` を記録します |

**日次確認推奨コマンド:**

```bash
# 最新ジョブの安全フラグ確認
python -c "
import json
lines = open('logs/job_status.jsonl', encoding='utf-8').readlines()
if lines:
    d = json.loads(lines[-1])
    print('no_auto_trade:', d.get('no_auto_trade'))
    print('no_order_quantity:', d.get('no_order_quantity'))
    print('brokerage_connection:', d.get('brokerage_connection', False))
"

# 最新 daily run の安全フラグ確認
python -c "
import json
lines = open('logs/scheduler_run_log.jsonl', encoding='utf-8').readlines()
if lines:
    d = json.loads(lines[-1])
    print('status:', d['status'])
    print('no_auto_trade:', d.get('no_auto_trade'))
    print('no_order_quantity:', d.get('no_order_quantity'))
"
```
