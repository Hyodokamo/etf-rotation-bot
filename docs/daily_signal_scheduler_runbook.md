# ETF Rotation Bot — 日次シグナル実行 運用Runbook

**Phase 6.2 — Windows Task Scheduler Setup / Operational Runbook**

> 本Botはアドバイザリーのみ。実注文なし、注文数量計算なし、自動売買なし、証券口座連携なし。  
> 最終判断は常に人間が行います。

---

## 1. 前提条件

### Python環境

```
Python 3.14以上
仮想環境またはシステムPythonにパッケージインストール済み
```

必要パッケージの確認:

```powershell
python -m pip list | Select-String "anthropic|pydantic|pyyaml|yfinance|requests"
```

インストール (未インストールの場合):

```powershell
pip install -r requirements.txt
```

### 環境変数 (.env)

プロジェクトルートに `.env` を作成 (`.env.example` をコピーして編集):

```
ANTHROPIC_API_KEY=sk-ant-...       # Claude API キー（AI委員会・crash signal使用）
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...   # Slack通知先
```

`.env` はリポジトリにコミットしないこと (`.gitignore` 済み)。  
`.env.example` にはキー名のみ記載 (値なし)。

### 作業ディレクトリ

```
C:\Users\<ユーザー名>\dev\etf-rotation-bot
```

Task Scheduler の「開始ディレクトリ」として必ず設定すること。  
相対パスで `scripts\daily_signal_check.py`, `config\`, `data\` 等を参照するため、  
作業ディレクトリがプロジェクトルートでないと起動に失敗します。

---

## 2. 手動実行コマンド

### 基本実行（テスト用・Slack無効）

```powershell
python scripts\daily_signal_check.py --no-slack
```

- market data更新 → crash signal check → overdue review の順で実行
- Slack通知はスキップ
- watchlist.csv は更新しない（dry-run デフォルト）
- 結果は `logs\scheduler_run_log.jsonl` に記録

### 本番実行（Slack有効）

```powershell
python scripts\daily_signal_check.py
```

- Slack Signal Digest を送信
- 上記と同じ安全モード（dry-run）

### market data更新スキップ（再実行・テスト用）

```powershell
python scripts\daily_signal_check.py --skip-market-data --no-slack
```

- 既存の `data\market_data_latest.csv` をそのまま使用
- market data APIを叩かないため高速に完了（約10〜20秒）

### watchlist更新を許可する場合（手動判断時のみ）

```powershell
python scripts\daily_signal_check.py --allow-watchlist-update
```

> **注意:** `data\watchlist.csv` が更新されます。  
> デフォルト（dry-run）では更新されません。  
> 必ず内容を確認してから使用してください。

### AI委員会フルスキャン（全シンボル）

```powershell
python scripts\daily_signal_check.py --full-committee-scan
```

- デフォルトは `--committee-on-trigger-only`（トリガーがあるシンボルのみAI委員会実行）
- フルスキャンはすべてのシンボルにAI委員会を実行（時間・コスト増）

---

## 3. Windows タスクスケジューラー設定

### 設定手順（GUIによる手動設定）

1. **タスクスケジューラーを開く**  
   `Win + R` → `taskschd.msc`

2. **新しいタスクを作成**  
   右ペインの「タスクの作成...」をクリック

3. **全般タブ**
   - 名前: `ETF-Daily-Signal-Check`
   - 説明: `AI監査型ETFローテーションBot 日次シグナルチェック（アドバイザリー専用・注文なし）`
   - セキュリティオプション: `ユーザーがログオンしているかどうかにかかわらず実行する`
   - `最上位の特権で実行する` のチェックは不要

4. **トリガータブ** → 「新規...」
   - 開始: `毎日`
   - 開始時刻: `07:30:00`（または `08:00:00`）
   - 繰り返し間隔: なし
   - 有効: チェック
   - **高度な設定**: 平日のみに絞る場合は後述のスケジュール調整を参照

5. **操作タブ** → 「新規...」
   - 操作: `プログラムの開始`
   - プログラム/スクリプト:  
     ```
     C:\Users\<ユーザー名>\dev\etf-rotation-bot\scripts\daily_signal_check.bat
     ```
   - 引数の追加 (省略可):  
     ```
     (空欄。引数はbatファイル内で指定)
     ```
   - 開始 (オプション):  
     ```
     C:\Users\<ユーザー名>\dev\etf-rotation-bot
     ```

6. **条件タブ**
   - `コンピューターをAC電源で使用している場合のみタスクを開始する` — 必要に応じてオフ
   - ネットワーク接続: チェックを外す（必要なければ）

7. **設定タブ**
   - `タスクが失敗した場合の再起動の間隔`: 5分（オプション）
   - `再起動する最大回数`: 1
   - 実行時間の制限: `6時間`（デフォルト1時間は短すぎる場合あり）

8. **「OK」をクリックしてタスクを保存**

### PowerShellによるタスク登録（コマンドライン）

管理者PowerShellで実行:

```powershell
$ProjectRoot = "C:\Users\$env:USERNAME\dev\etf-rotation-bot"
$BatFile = "$ProjectRoot\scripts\daily_signal_check.bat"

$action = New-ScheduledTaskAction `
    -Execute $BatFile `
    -WorkingDirectory $ProjectRoot

$trigger = New-ScheduledTaskTrigger `
    -Daily `
    -At "07:30AM"

$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Hours 6) `
    -RestartCount 1 `
    -RestartInterval (New-TimeSpan -Minutes 5)

Register-ScheduledTask `
    -TaskName "ETF-Daily-Signal-Check" `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description "ETFローテーションBot 日次シグナルチェック（アドバイザリー専用）" `
    -RunLevel Limited
```

タスク確認:

```powershell
Get-ScheduledTask -TaskName "ETF-Daily-Signal-Check" | Select-Object TaskName, State
```

手動テスト実行:

```powershell
Start-ScheduledTask -TaskName "ETF-Daily-Signal-Check"
# 少し待ってから結果確認:
Get-ScheduledTaskInfo -TaskName "ETF-Daily-Signal-Check" | Select-Object LastRunTime, LastTaskResult
```

タスク削除:

```powershell
Unregister-ScheduledTask -TaskName "ETF-Daily-Signal-Check" -Confirm:$false
```

---

## 4. 推奨実行時刻

### 米国市場クローズ後のタイミング

| 米国時間 | 日本時間（標準/冬時間: EST+14h） | 日本時間（夏時間: EDT+13h） |
|---------|--------------------------------|--------------------------|
| 市場クローズ 16:00 ET | 翌日 06:00 JST（冬） | 翌日 05:00 JST（夏） |
| 推奨実行 | **07:30 JST**（冬・夏共通） | **07:30 JST** |

**推奨: 平日 07:30 JST または 08:00 JST**

- 米国市場クローズ後の終値・テクニカルデータが取得可能
- アジア市場の朝の動きを反映した状態でシグナル評価
- 通勤前にSlack通知を確認できる

### 平日のみに絞る場合

タスクスケジューラーGUIでは「毎日」トリガーは土日を含みます。  
平日のみの実行はXML編集またはbatファイル内での曜日チェックで対応:

```batch
REM batファイル内に曜日チェックを追加する場合
for /f %%A in ('powershell -Command "(Get-Date).DayOfWeek"') do set DOW=%%A
if "%DOW%"=="Saturday" exit /b 0
if "%DOW%"=="Sunday" exit /b 0
```

---

## 5. ログ確認方法

### scheduler_run_log.jsonl（主要ログ）

```powershell
# 最新の実行結果を確認
python -c "import json; d = json.loads(open('logs/scheduler_run_log.jsonl', encoding='utf-8').readlines()[-1]); print('status:', d['status']); print('committee_target:', d['committee_target_count']); print('global_only_skipped:', d['global_only_skipped_count']); print('no_auto_trade:', d['no_auto_trade'])"
```

各エントリには以下が含まれます:
- `run_id`: 実行ID（タイムスタンプ付き）
- `status`: `SUCCESS` / `PARTIAL_FAILURE` / `FAILED`
- `started_at` / `finished_at`: 開始・終了時刻
- `committee_target_count`: AI委員会を実行したシンボル数
- `skipped_count`: トリガーなしでスキップしたシンボル数
- `global_only_skipped_count`: グローバルトリガーのみでスキップしたシンボル数
- `no_auto_trade`: 常に `true`（自動売買なし）
- `no_order_quantity`: 常に `true`（注文数量計算なし）
- `steps`: 各ステップの詳細（exit_code / duration_seconds / stderr_summary）

### コンソールログ（batファイル出力）

```powershell
# 最新のコンソールログ
Get-Content logs\daily_signal_check_console.log -Tail 50
# または日付付きログ
Get-ChildItem logs\daily_run_*.log | Sort-Object LastWriteTime | Select-Object -Last 1 | Get-Content
```

### シグナルレポート

```powershell
# 最新のシグナルレポート
Get-Content reports\daily_signal_report.md
```

`reports/daily_signal_report.md` — crash signal check の詳細結果（Markdown形式）

### market data

```powershell
# 最新のmarket data（最初の数行）
python -c "import csv; rows = list(csv.DictReader(open('data/market_data_latest.csv'))); print('取得シンボル数:', len(rows)); [print(r['symbol'], r.get('daily_return_pct','N/A'), r.get('rsi_14','N/A')) for r in rows[:5]]"
```

---

## 6. 失敗時の確認

### PARTIAL_FAILURE の場合

```powershell
# 直近の失敗ステップを確認
python -c "
import json
lines = open('logs/scheduler_run_log.jsonl', encoding='utf-8').readlines()
d = json.loads(lines[-1])
print('Status:', d['status'])
for s in d['steps']:
    print(f'  step exit={s[\"exit_code\"]} dur={s[\"duration_seconds\"]}s')
    if s['exit_code'] != 0 and s.get('stderr_summary'):
        print('  stderr:', s['stderr_summary'][:200])
"
```

### よくある失敗ケースと対処

| 症状 | 原因 | 対処 |
|------|------|------|
| `market data` ステップ失敗 (exit=-1) | タイムアウト / ネットワーク不良 | `--skip-market-data` で再実行 / 時間帯を変える |
| `market data` ステップ失敗 (exit=1) | yfinance APIエラー / 市場休場 | 翌営業日まで待つ。`--skip-market-data` で他ステップだけ実行 |
| Slack送信失敗 (PARTIAL_FAILURE) | SLACK_WEBHOOK_URL未設定 / URLが無効 | `.env` の `SLACK_WEBHOOK_URL` を確認 / `--no-slack` で続行 |
| AI委員会タイムアウト (exit=-1) | Claude APIの応答遅延 / timeout=600s超過 | 一時的な問題の場合は翌日に自然解消。ANTHROPIC_API_KEYを確認 |
| `ANTHROPIC_API_KEY` エラー | APIキーなし / 期限切れ | `.env` にキーを設定 / Anthropic Consoleで確認 |
| `python: command not found` | PATH未設定 | batファイルにフルパスで`python.exe`を指定 |
| `No module named ...` | 仮想環境外で実行 | `pip install -r requirements.txt` で再インストール |

### market data取得失敗時の継続運用

```powershell
# market dataスキップで signal check だけ実行
python scripts\daily_signal_check.py --skip-market-data --no-slack
```

前回取得した `data\market_data_latest.csv` を使用するため、  
データが古い（1日以上経過）可能性があることに注意。

### AI委員会タイムアウトの調整

`scripts/daily_signal_check.py` の `run_step()` のデフォルト timeout は 600秒。  
シンボル数が多い場合や、AI APIが遅い場合はコード内で調整可能（Phase 6.2時点では変更不要）。

---

## 7. 安全運用

### デフォルト動作（常に安全）

| 項目 | デフォルト | 変更方法 |
|------|-----------|---------|
| watchlist.csv更新 | **しない（dry-run）** | `--allow-watchlist-update` で許可 |
| 注文数量計算 | **しない** | 変更不可（設計上禁止） |
| 自動売買 | **しない** | 変更不可（設計上禁止） |
| 証券口座連携 | **しない** | 変更不可（設計上禁止） |
| AI委員会スキップ | トリガーなし / グローバルのみのシンボルはスキップ | `--full-committee-scan` で全スキャン |

### --allow-watchlist-update の使用タイミング

以下の条件がすべて揃った場合のみ手動で使用:
1. BUY_CANDIDATEシグナルを `--signal-review` で確認済み
2. 候補の内容・理由を自分で理解・納得済み
3. `--allow-watchlist-update` を意識的に付けて実行

```powershell
# 正しい使用例（すべて確認済みのうえで）
python scripts\daily_signal_check.py --allow-watchlist-update --no-slack
```

### ai_sleeve_state.csv / etf_master.csv は変更しない

本スケジューラーは以下のファイルを **絶対に変更しません**:
- `data/ai_sleeve_state.csv`
- `data/etf_master.csv`
- `data/total_portfolio_snapshot.csv`
- `logs/signal_history.csv`（dry-runデフォルト時）

USER_APPROVED / USER_REJECTED の設定は人間が `--human-signal-decision` で行います。

---

## 8. 日次運用フロー

```
毎朝 07:30  Task Scheduler が daily_signal_check.bat を自動実行
    ↓
    [Step 1] --update-market-data
             yfinanceから市場データ取得 → data/market_data_latest.csv
    ↓
    [Step 2] --crash-signal-check --dry-run --committee-on-trigger-only
             クラッシュトリガー検出 → AI委員会評価（対象シンボルのみ）
             → reports/daily_signal_report.md, Slack Signal Digest送信
    ↓
    [Step 3] --signal-review --overdue
             期限超過watchlistの確認 → Slack overdue通知
    ↓
    logs/scheduler_run_log.jsonl に記録

朝: Slack確認
    - "本日Committee実行対象: N件" → 対象シンボルを確認
    - BUY_CANDIDATE / HIGH_PRIORITY_CANDIDATE があれば要確認
    - グローバルトリガー情報（SPY/QQQ/VIX等の急落状況）

BUY_CANDIDATEがあった場合:
    1. シグナルレポートを確認
       python main.py --signal-review
    
    2. 候補の内容を理解・判断
       python main.py --signal-review --review-symbol ITA
    
    3. （必要なら）AI候補レビューを実施
       python main.py --candidate-review --candidate-symbol ITA
    
    4. 人間判断を記録
       python main.py --signal-review --review-symbol ITA \
           --human-signal-decision USER_APPROVED \
           --human-signal-note "エントリー条件確認済み、NISA枠外で検討"
    
    5. NISAへの実際の発注は別途証券口座で手動実行

watchlist.csv更新が必要な場合（任意）:
    python scripts\daily_signal_check.py --allow-watchlist-update --no-slack
```

---

## 9. Git管理注意事項

以下のファイルはすでに `.gitignore` で除外されています:

```gitignore
# ログ（scheduler記録・committee記録）
logs/*
!logs/.gitkeep

# 生成レポート
reports/*
!reports/.gitkeep
reports/daily_signal_report.md

# 市場データ・個人ポートフォリオ
data/market_data_latest.csv
data/watchlist.csv
data/total_portfolio_snapshot.csv
data/ai_sleeve_state.csv
data/ai_sleeve_state_*.csv
```

コミット時は `git status` で確認し、個人データや生成ファイルが含まれていないことを確認してください。

### コミット対象（管理するファイル）

```
config/                  ← シグナル設定・market data設定
scripts/daily_signal_check.py
scripts/daily_signal_check.bat
scripts/daily_signal_check.ps1
docs/daily_signal_scheduler_runbook.md
src/                     ← Botのソースコード
tests/                   ← テスト
```

---

## 10. 動作確認チェックリスト

初回セットアップ後、以下の順で確認:

```powershell
# [1] 依存関係確認
python -c "import anthropic, pydantic, yaml, yfinance; print('OK')"

# [2] 設定ファイル確認
python -c "from src.signals.signal_config import load_signal_config; cfg = load_signal_config(); print('signal config OK')"

# [3] 手動テスト（Slack無効・market data更新スキップ）
python scripts\daily_signal_check.py --skip-market-data --no-slack

# [4] ログ確認
python -c "import json; d = json.loads(open('logs/scheduler_run_log.jsonl', encoding='utf-8').readlines()[-1]); print('status:', d['status'], '/ no_auto_trade:', d['no_auto_trade'])"

# [5] Task Schedulerに登録後、手動実行テスト
Start-ScheduledTask -TaskName "ETF-Daily-Signal-Check"
# 数分後に確認:
Get-ScheduledTaskInfo -TaskName "ETF-Daily-Signal-Check"
```

---

## 安全確認

- 本Botは投資助言ではありません
- シグナルはWatchlist候補の通知のみです
- 実際の売買は証券口座で人間が判断・手動実行します
- `no_auto_trade: true` / `no_order_quantity: true` はすべての実行ログに記録されます
- `scheduler_run_log.jsonl` の各エントリで安全性が確認できます
