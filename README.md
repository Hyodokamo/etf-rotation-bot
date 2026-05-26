# etf-rotation-bot

AI監査型ETFローテーションBot Phase 1.

ETFユニバース25本を月次で評価し、モメンタム・トレンド・ボラティリティ・相関・リスク制約を考慮した推奨配分を作成し、MarkdownレポートとSlack投稿を生成します。

## Phase 1 スコープ

- ETF価格取得（yfinance）
- 指標計算（モメンタム、トレンド、ボラティリティ）
- スコアリング（ランクベース、トレンドボーナス、ボラティリティ調整）
- 配分計算（スコア比例、最大ウエイト制約、カテゴリキャップ）
- リスクゲート（SP500ドローダウン監視）
- ターンオーバー制約（最大50%、ブレンド調整）
- Markdownレポート生成
- Slack Incoming Webhook投稿
- ログ・状態ファイル保存

**実装対象外（Phase 2以降）:**

- AI監査
- 自動売買
- 楽天証券連携
- NISA内ローテーション

## セットアップ

```bash
pip install -r requirements.txt
cp .env.example .env
# .env の SLACK_WEBHOOK_URL を設定（任意）
```

## 実行方法

```bash
python main.py
```

オプション:

```bash
python main.py --config config.yaml       # 設定ファイル指定（デフォルト: config.yaml）
python main.py --date 2026-05-21          # 実行日を手動指定
python main.py --no-ai-audit              # AI監査を無効化して実行
python main.py --compare-strategy-variants  # 戦略バリアント比較レポートを生成
```

**出力ファイル:**
- `outputs/report_YYYY-MM-DD.md` — Markdownレポート
- `outputs/portfolio_state.json` — 前回配分の状態（次回ターンオーバー計算用）
- `outputs/bot.log` — 実行ログ
- `outputs/YYYY-MM/pre_trade_gate_result.json` — Pre-Trade Gate結果
- `outputs/YYYY-MM/run_log.json` — 実行ログ（strategy_variant含む）
- `outputs/YYYY-MM/strategy_variant_comparison.md` — 戦略比較レポート（`--compare-strategy-variants` 時）

## 戦略バリアント（Strategy Variant）

デフォルト戦略は **`cash_fallback_separated`** です。

| バリアント | 説明 |
|-----------|------|
| `cash_fallback_separated` | **デフォルト**。SGOV・UUPをモメンタムランキングから除外し、通常ETF（VOO・VTV・BND・QQQMなど）で配分を構成。Risk-ON時の防御資産比率が低下し、Pre-Trade Gate PASS率が改善する。 |
| `baseline_current` | 比較用。全ETFをランキング対象とする従来挙動。SGOV 50%超・UUP採用などが発生しやすく、Pre-Trade Gate FAILになるケースが多い。 |

### バリアントの変更方法

`config.yaml` の `strategy_variant.name` を変更します:

```yaml
strategy_variant:
  name: "cash_fallback_separated"  # または "baseline_current"
```

### 戦略比較レポートの生成

```bash
python main.py --compare-strategy-variants --no-ai-audit
```

`outputs/YYYY-MM/strategy_variant_comparison.md` に両バリアントの並列比較と簡易バックテストが出力されます。

## テスト方法

```bash
pytest tests/ -v
```

## 設定

`config.yaml` で以下を変更できます:

| セクション | 主な設定 |
|-----------|---------|
| `universe.assets` | ETFユニバース定義 |
| `scoring` | モメンタム窓・重み、トレンドボーナス、ボラティリティ調整 |
| `allocation` | 配分方法（score_proportional/equal_weight/inverse_volatility）、Top-N数 |
| `risk` | 最大ウエイト、カテゴリキャップ、リスクオフ閾値 |
| `turnover` | 最大ターンオーバー（デフォルト50%） |
| `data` | データ取得年数、最低履歴日数 |

## 環境変数

| 変数名 | 説明 | 必須 |
|--------|------|------|
| `SLACK_WEBHOOK_URL` | Slack Incoming Webhook URL | 任意（未設定時はSlack投稿をスキップ） |

## AI監査（Phase 2）

AI監査は定量モデルの推奨配分を Claude または OpenAI で審査し、参考意見を Markdown レポートと Slack に追記します。**最終配分は常に定量モデルの出力値であり、AI 提案は配分に反映されません。**

### プロバイダーの切り替え

#### Claude（デフォルト）

```bash
# .env
ANTHROPIC_API_KEY=sk-ant-...
AI_AUDIT_ENABLED=true
AI_AUDIT_PROVIDER=claude
AI_AUDIT_MODEL=claude-3-5-sonnet-latest
```

```yaml
# config.yaml
ai_audit:
  enabled: true
  provider: claude
  model: claude-3-5-sonnet-latest
```

#### OpenAI

```bash
# .env
OPENAI_API_KEY=sk-...
AI_AUDIT_ENABLED=true
AI_AUDIT_PROVIDER=openai
AI_AUDIT_MODEL=gpt-4o
```

```yaml
# config.yaml
ai_audit:
  enabled: true
  provider: openai
  model: gpt-4o
```

#### コマンドラインで上書き

```bash
# Claude で実行
python main.py --ai-audit --ai-audit-provider claude --ai-audit-model claude-3-5-sonnet-latest

# OpenAI で実行
python main.py --ai-audit --ai-audit-provider openai --ai-audit-model gpt-4o

# AI監査を無効化
python main.py --no-ai-audit
```

#### 将来のプロバイダー拡張

`src/llm/gemini_client.py` に `GeminiClient` を実装し、`config.yaml` で `provider: gemini` を指定することで追加できます。現在は `NotImplementedError` のスタブのみ存在します。

## Phase 3: 月次レビュー判断ログ

Phase 3は **売買承認機能ではありません**。月次レポートを見た人間が、レビュー結果を記録するための機能です。

### 目的

- 定量モデルの推奨配分と各種チェック結果を人間がレビューした記録を残す
- 「見送り」「再レビュー」「手動判断で採用」などの判断を JSON/Markdown で保存する
- 自動売買・注文数量計算・楽天証券連携は行わない

### 判断種別

| 判断 | 意味 |
|------|------|
| `REVIEW_CONFIRMED` | レポートを確認し、判断ログだけ残す |
| `SKIP_THIS_MONTH` | 今月は売買・入替を見送る |
| `REQUEST_RERUN` | 条件や設定を見直して再実行する |
| `MANUAL_OVERRIDE` | 警告を理解したうえで人間判断として採用する（コメント必須） |

### CLIで判断を記録する方法

```bash
# 今月は見送り
python main.py --record-decision SKIP_THIS_MONTH --decision-comment "Pre-Trade GateがFAILのため今月は見送り"

# 再レビュー
python main.py --record-decision REQUEST_RERUN --decision-comment "防御資産比率が高いため再設定"

# 手動判断で採用（コメント必須）
python main.py --record-decision MANUAL_OVERRIDE --decision-comment "警告を理解したうえで採用"

# 実行日指定（デフォルトは当日）
python main.py --record-decision SKIP_THIS_MONTH --decision-comment "..." --date 2026-05-26
```

`--record-decision` 実行時は価格取得・AI監査を再実行しません。直近の `run_log.json` を読み込んで判断ログを作成します。

### 判断ログの出力先

```
outputs/YYYY-MM/decision_log.json   # JSON形式の判断記録
outputs/YYYY-MM/decision_log.md     # Markdown形式の判断記録
outputs/YYYY-MM/run_log.json        # 既存のrun_logに判断情報が追記される
```

### Pre-Trade Gate FAILの場合

- `REVIEW_CONFIRMED`（レビュー確認済み）は表示・選択できません
- `SKIP_THIS_MONTH` / `REQUEST_RERUN` / `MANUAL_OVERRIDE` が選択肢となります
- いずれもコメントが必須です
- **自動売買は行いません**

### Slack表示

Slack投稿の末尾に「月次レビュー判断」セクションが追加されます。Pre-Trade Gate の状態に応じて表示内容が変わります。

- **PASS**: レビュー確認済み / 今月は見送り / 再レビュー
- **PASS_WITH_CAUTION / REVIEW_REQUIRED**: 上記 + 手動判断で採用（コメント必須）
- **FAIL**: レビュー確認済みは表示なし。見送り / 再レビュー / 手動判断で採用のみ

現在はCLI記録方式です（Slackボタンのインタラクティブ受信は未実装）。

### 安全ルール

- 判断ログは売買実行ではありません
- `auto_trade: false`、`order_generated: false` が必ず記録されます
- `MANUAL_OVERRIDE` はコメント必須です
- `final_allocation = quant_recommendation` は維持されます

## 既知の制限事項・注意点

- 日本円建てETF（1306.T等）と米ドル建てETFを同一スコアで比較しており、**通貨換算は行っていません**
- yfinanceのデータは当日分が取得できない場合があります（前営業日データを使用）
- ターンオーバー制約は前回状態（`outputs/portfolio_state.json`）がある場合のみ適用されます
- リスクゲートはVOO（S&P500）の60日リターンのみで判定します
- 推奨配分はあくまで参考情報であり、投資判断・売買は手動で行ってください
