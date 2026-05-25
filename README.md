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

## 既知の制限事項・注意点

- 日本円建てETF（1306.T等）と米ドル建てETFを同一スコアで比較しており、**通貨換算は行っていません**
- yfinanceのデータは当日分が取得できない場合があります（前営業日データを使用）
- ターンオーバー制約は前回状態（`outputs/portfolio_state.json`）がある場合のみ適用されます
- リスクゲートはVOO（S&P500）の60日リターンのみで判定します
- 推奨配分はあくまで参考情報であり、投資判断・売買は手動で行ってください
