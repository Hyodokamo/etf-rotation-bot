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
```

**出力ファイル:**
- `outputs/report_YYYY-MM-DD.md` — Markdownレポート
- `outputs/portfolio_state.json` — 前回配分の状態（次回ターンオーバー計算用）
- `outputs/bot.log` — 実行ログ

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

## 既知の制限事項・注意点

- 日本円建てETF（1306.T等）と米ドル建てETFを同一スコアで比較しており、**通貨換算は行っていません**
- yfinanceのデータは当日分が取得できない場合があります（前営業日データを使用）
- ターンオーバー制約は前回状態（`outputs/portfolio_state.json`）がある場合のみ適用されます
- リスクゲートはVOO（S&P500）の60日リターンのみで判定します
- 推奨配分はあくまで参考情報であり、投資判断・売買は手動で行ってください
