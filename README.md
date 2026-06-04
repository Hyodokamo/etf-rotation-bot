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

## Phase 3.1: Investment Committee OS（shadow mode）

定量モデルが確定した推奨配分に対し、著名投資家アーキタイプを模した**二層会議体**（Core / Satellite）が多面的な参考意見を出します。**shadow mode** であり、**最終配分・Pre-Trade Gate・AI監査には一切影響しません**（`allocation_override` は常に `false` 固定）。出力はMarkdownレポートとSlackに表示専用で追記されます。

### メンバー構成

| 委員会 | member_id | メンバー | 評価観点 |
|--------|-----------|---------|---------|
| Core | `aqr_meb` | AQR / Meb Faber 型 | 定量・トレンド・ETFローテーション |
| Core | `howard_marks` | Howard Marks 型 | サイクル・過熱・リスク |
| Core | `rob_arnott` | Rob Arnott 型 | バリュエーション・平均回帰・スマートベータ |
| Core | `core_ai_auditor` | Core AI Auditor | データ品質・ロジック・過剰最適化監査（**投資家ではなく品質保証**。既存AI監査とは独立） |
| Satellite | `buffett` | Buffett 型 | 長期品質・事業価値・保有耐性 |
| Satellite | `paul_tudor_jones` | Paul Tudor Jones 型 | 防御的トレンド・損切り・200日線 |
| Satellite | `druckenmiller` | Druckenmiller 型 | 大局テーマ・集中投資仮説 |

各メンバーは**「本人を演じる」のではなく、公開された投資哲学に基づく判断様式（Level 2）でモデル化**されます（"You are an investment-analysis agent modeled on X-style thinking. You are not X."）。共通指示は `agents/common.md` に定義され、各メンバーのプロンプトに前置されます。

各メンバーは独立評価として扱われ、他メンバーの結論を参照・追従せず、必ず「最も支持する理由」「最も反対する理由」、そして**最も強く反対する点（`dissenting_view`）**と**具体的な再レビュー条件（`next_review_triggers`）**を出します。`core_ai_auditor` のみ人格を持たない品質保証エージェントです。

### Satellite Committee の起動方式

`config/committee.yaml` の `satellite_activation` で制御します。

| 値 | 挙動 |
|----|------|
| `always`（デフォルト） | 毎回フル稼働（既存挙動を維持） |
| `conditional` | テーマ/セクターETF・成長株比率・売買増（turnover>0）・AI監査がPASS_WITH_CAUTION以下、のいずれか成立時のみ起動 |

Core Committee は毎月必ず稼働します。`conditional` で未起動の場合、Satellite判定は `INSUFFICIENT_DATA` となり、レポートに未起動理由が表示されます。

### 判定種別（5種）

`PASS` / `PASS_WITH_CAUTION` / `WATCH` / `REJECT` / `INSUFFICIENT_DATA`

Core / Satellite の判定は別々に集約され、最終判定は両者を統合して決まります。集約は**多数決ではなく重大度ベース**（1人のREJECTが全体を支配）です。

### 設定

`config/committee.yaml` で制御します。

```yaml
committee:
  enabled: true
  shadow_mode: true              # 初期実装は必ず shadow mode
  allocation_override_allowed: false
  max_tokens_per_member: 1200
  llm_call_mode: "batch"         # batch（初期値・1回呼び出し） / per_member（将来対応）
  satellite_activation: "always" # always / conditional
```

共通エージェント指示は `agents/common.md`（Level 2 モデリング・捏造禁止・`dissenting_view` 要求・具体的レビュー条件）に切り出されています。

`llm_call_mode` を `per_member` にするとメンバーごとに個別LLM呼び出しへ切り替わります（既定は `batch`）。

### 実行とLLM

Committee は **AI監査が有効でLLMクライアントが利用可能なとき**に実行されます（既存のAI監査と同じ provider/model を再利用）。`--no-ai-audit` ではスキップされ、レポート/Slackにも追加されません。

```bash
python main.py --ai-audit --ai-audit-provider openai
```

### 出力先

```
outputs/YYYY-MM/committee_result.json   # Committee判定結果（JSON）
outputs/report_YYYY-MM-DD.md            # レポート末尾に Committee セクションを追記
outputs/YYYY-MM/run_log.json            # committee_*_verdict / committee_shadow_mode を追記
logs/committee_decision_log.jsonl       # Phase 3.2: 月次Committee判断の追記ログ（後述）
```

### Phase 3.2: Committee Decision Log

Committee実行のたびに、その月次判断を `logs/committee_decision_log.jsonl` に **append-only** で1行追記します。「過去のCommitteeが何を判断し、その後どう変化したか」を後から検証するための versioned ログです。配分ロジックには一切影響しません（shadow mode 不変条件を維持）。

- **AI委員会ログはCommittee実行時に毎回保存**されます（`--record-committee-decision` 不要）。
- **人間判断**は `--record-committee-decision` を付けたときのみ `human_decision` / `human_note` に記録されます（未指定時は `null`）。

```bash
# AI委員会ログのみ追記
python main.py --ai-audit --ai-audit-provider openai --committee --committee-mode shadow

# 人間判断付きで追記
python main.py --ai-audit --ai-audit-provider openai --committee --committee-mode shadow \
  --record-committee-decision --human-decision HOLD --human-note "Shadow mode validation"
```

`--human-decision` の値: `HOLD` / `BUY` / `ADD` / `TRIM` / `EXIT` / `WAIT` / `SKIP`。

**ログスキーマ（schema_version 1.0）**: `schema_version`, `run_id`, `timestamp`(JST), `date`, `strategy_variant`, `risk_mode`, `final_allocation`, `ai_audit_status`, `core_committee_verdict`, `satellite_committee_verdict`, `final_committee_verdict`, `recommended_action`, `allocation_override`(常にfalse), `member_outputs`, `dissenting_views`, `next_review_triggers`, `satellite_activated`, `satellite_activation_reason`, `human_decision`(初期null), `human_note`(初期null)。

**安全設計**:
- APIキー・プロンプト全文・秘密情報は保存しません（ホワイトリスト＋再帰的リダクション）。
- append-only（既存行を書き換えない）。
- JSONLの1行が壊れても、読み取りはその行のみスキップして全体は落ちません。
- 既存の月次レビュー判断ログ（Phase 3）とは責務を分離した **Committee専用ログ** です。

### Phase 3.3: Committee Review Comparison（前回比）

`logs/committee_decision_log.jsonl` の**直近2件**を比較し、前回と今回のCommittee判断の変化を構造化してレポート/Slackに表示します。**Pythonで決定的に**差分抽出し、LLMには依存しません（後付けストーリーを避けるため）。配分ロジックには一切影響しません（shadow mode 不変条件を維持）。

- 有効ログが**2件未満なら比較不能として安全にスキップ**（Slackに冗長表示しない）。
- Committee実行時、比較可能なら**自動でサマリーを生成**します。無効化したい場合は `--no-committee-comparison`。

抽出する差分: Core/Satellite/Final の verdict変化、メンバー別verdict変化、ETFごとの配分増減（INCREASED/DECREASED/ADDED/REMOVED/UNCHANGED）、新規・解消された `next_review_triggers`、新規・解消された `dissenting_views`、`recommended_action` の変化。

**severity（4段階）**:

| severity | 例 |
|----------|-----|
| `MATERIAL` | final が WATCH/REJECT へ悪化 / 単一ETFが±10pt以上変化 / `allocation_override` が true（監査検知。通常は出ない） / 人間判断が TRIM/EXIT/WAIT |
| `CAUTION` | メンバー2人以上が悪化 / Howard Marks・Rob Arnott・core_ai_auditor が WATCH以上 / 新規 dissenting_view が2件以上 / 新規 trigger が3件以上 |
| `INFO` | 軽微なverdict変化 / 小幅な配分変動 / 少数のtrigger追加 / recommended_action変化 |
| `NONE` | 重要な変化なし |

```bash
# 2件以上ログがあれば自動で前回比を生成（既定）
python main.py --ai-audit --ai-audit-provider openai --committee --committee-mode shadow

# 前回比を無効化
python main.py --ai-audit --ai-audit-provider openai --committee --no-committee-comparison
```

### Phase 3.4: Committee Advisory Mode（助言）

Committeeの構造化判断（`CommitteeResult` ＋ Review Comparison ＋ `final_allocation` ＋ `risk_mode` ＋ `ai_audit_status`）から、今月の**実務的な助言**を生成します。**決定的なPythonロジック**で生成し、売買判断のLLM生成には依存しません。**配分変更・売買数量計算・自動売買は一切行いません**（`allocation_override` 常に false）。助言は「追加購入を控える」「維持を推奨」「再レビュー」「候補レビューへ回す」と表現し、「売る」「買う」を断定しません。

固定スキーマ: `advisory_mode`(=`shadow_advisory`) / `overall_stance` / `action_items` / `do_not_actions` / `next_review_focus` / `generated_from` / `allocation_override`。

- `overall_stance`: `ACCEPT` / `HOLD_WITH_CAUTION` / `WAIT_FOR_REVIEW` / `REDUCE_RISK_REVIEW` / `INSUFFICIENT_DATA`
- `action_items[].category`: `BUY_DISCIPLINE` / `HOLD_DISCIPLINE` / `RISK_CONTROL` / `REVIEW_TRIGGER` / `DATA_QUALITY` / `CANDIDATE_REVIEW` / `HUMAN_DECISION_REQUIRED`
- `action_items[].priority`: `LOW` / `MEDIUM` / `HIGH`（HIGH優先・最大5件表示）

主な生成ルール: final=WATCH→`WAIT_FOR_REVIEW`、final=REJECT→`REDUCE_RISK_REVIEW`、AI監査REJECT/`core_ai_auditor` WATCH+→`DATA_QUALITY`/`HUMAN_DECISION_REQUIRED` を最優先、Rob Arnott WATCH→`BUY_DISCIPLINE`、Howard Marks WATCH→`RISK_CONTROL`、Paul Tudor Jones WATCH→`REVIEW_TRIGGER`、Druckenmiller WATCH→`CANDIDATE_REVIEW`、前回比 severity が CAUTION 以上→必ず1件以上の HIGH を含める。

```bash
# 既定で Advisory を生成
python main.py --ai-audit --ai-audit-provider openai --committee --committee-mode shadow

# Advisory を非表示（--no-committee-comparison と併用可）
python main.py --ai-audit --ai-audit-provider openai --committee --no-committee-advisory
```

### 安全ルール

- `allocation_override` は常に `false`（`shadow_mode` 検証で強制）
- Committee は配分（weights）を一切返さず、`final_allocation = quant_recommendation` を維持
- 自動売買・注文生成は行わない（メンバー出力中の不適切な売買表現は検出して保留）
- Slackボタンのインタラクティブ受信は未実装（表示専用）

## 既知の制限事項・注意点

- 日本円建てETF（1306.T等）と米ドル建てETFを同一スコアで比較しており、**通貨換算は行っていません**
- yfinanceのデータは当日分が取得できない場合があります（前営業日データを使用）
- ターンオーバー制約は前回状態（`outputs/portfolio_state.json`）がある場合のみ適用されます
- リスクゲートはVOO（S&P500）の60日リターンのみで判定します
- 推奨配分はあくまで参考情報であり、投資判断・売買は手動で行ってください
