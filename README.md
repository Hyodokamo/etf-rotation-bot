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

## Phase 3.6.1: Slack Executive Digest

Committee実行時（`--ai-audit ... --committee`）のSlack通知は、「ファイル確認の通知」ではなく**スマホで読める投資委員会サマリー**になります。詳細はMarkdownレポートに残し、Slackは要約に徹します（決定的に生成・LLM再呼び出しなし・配分変更なし）。

Slack表示構成:
- 今月の結論（1文）／戦略・リスクモード・ターンオーバー／Top配分／AI監査（1〜2文）
- Investment Committee 最終判定（Core/Satellite）
- **Committee 論点**（最大4件：警戒メンバー優先＋`core_ai_auditor`、ETF名・数値を含む具体コメント）
- **各エージェントの一言**（全7メンバー各1行・原則80字以内。PASSは許容理由、WATCH/REJECT/PASS_WITH_CAUTIONは警戒理由、AI Auditorは品質保証観点）
- **主な反対意見 Top3** / **次回までの監視条件 Top3**（具体銘柄・閾値を優先）
- **Committee Advisory Top3**（HIGH優先）／前回比 severity
- shadow mode 注記（配分変更なし／売買数量計算なし／自動売買なし）／詳細レポートパス

Committee非実行時（`--no-ai-audit` 等）は従来の簡易サマリーを使用します。digest生成ヘルパ（`build_committee_debate_highlights` / `build_agent_one_liners` / `select_top_dissenting_views` / `select_top_review_triggers`）は `CommitteeResult` でもメンバー辞書リストでも動作し、将来 Candidate Review にも流用できます。

## Phase 4.1: Slack Interactivity Foundation

Slackボタンの押下を受信し、人間判断を **append-only** で `logs/slack_decision_log.jsonl` に記録します。**ボタンは判断の記録であり、売買承認ではありません。** 配分変更・注文数量計算・自動売買・証券口座連携は一切行いません（「買う/売る/注文/購入実行」の文言も使いません）。

- **起動**: `python src/slack_interaction_handler.py`（Socket Mode 既定）。`--dry-run` でSlack非接続のpayload検証のみ。
- **必要な環境変数**: `SLACK_BOT_TOKEN` / `SLACK_APP_TOKEN`（Socket Mode必須）、`SLACK_SIGNING_SECRET`（将来のHTTP方式用）、`SLACK_ALLOWED_USER_IDS`（許可ユーザーのカンマ区切り。未設定なら制限なし）。トークン未設定時はインタラクティブ機能は無効です。
- **action_id**: monthly = `monthly_review_confirmed` / `monthly_skip_this_month` / `monthly_request_rerun` / `monthly_add_note`。candidate = `candidate_watchlist` / `candidate_small_test_candidate` / `candidate_wait` / `candidate_reject` / `candidate_re_review` / `candidate_add_note`。
- **ボタンの value** は安全なJSON（`source_type` / `run_id`・`review_id` / `candidate_symbol` / `candidate_stability` / `recommended_handling` / `generated_at`）で、APIキー・プロンプト・raw_response を含みません。
- **小額検討候補のブロック（Phase 4.1.1 ボタン表示ゲーティング）**: `candidate_small_test_candidate` は Stability Check が `UNSTABLE` または `recommended_handling` が `HUMAN_REVIEW_REQUIRED` / `DO_NOT_ACT_YET` の候補では、Block Kit上で **`小額検討候補` と `Watchlist入り` の両方を非表示**にし（`様子見` / `見送り` / `再レビュー` / `メモ追加` のみ表示＋警告文）、押下時もルーターが拒否します（最終防衛線）。`STABLE` または `OK_FOR_WATCHLIST` のときのみ全6ボタン（`小額検討候補` 含む）を表示。それ以外は `Watchlist入り` ＋ 4ボタン（`小額検討候補` は非表示）。
- **冪等性**: 同一 `action_id` + `user_id` + 対象ID（run_id/review_id）の重複押下は二重記録しません。不正な action_id・壊れた value・未許可ユーザーは拒否します。
- 押下後は即時 ack し、短い確認メッセージ（例: 「記録しました: GRID を WAIT として保存しました」）を返します。

各レコードには `allocation_override: false` / `auto_trade: false` / `order_generated: false` が必ず含まれます。

### Phase 4.2: Slack Note Modal

`メモ追加`（`monthly_add_note` / `candidate_add_note`）ボタン押下で **判断メモ用モーダル**を開き、`human_note` を入力して既存ログに **append-only** で記録します。**メモは判断の補足であり、売買承認ではありません**（モーダル内に「買う/売る/注文」の文言なし）。配分変更・注文数量計算・自動売買・証券口座連携は行いません。

- モーダル: title「判断メモ」/ submit「記録」/ close「キャンセル」/ 複数行入力（上限500文字）。空欄は記録しません（拒否）。500字超は切り詰めます。
- `private_metadata` は安全なJSON（`source_type` / `run_id` / `review_id` / `candidate_symbol` / `action_id` / `user_id`）。秘密情報を含まず、壊れていれば拒否します。
- 記録先: 月次メモ → `logs/slack_decision_log.jsonl`（`run_id` 必須）、Candidateメモ → `logs/candidate_review_log.jsonl`（`review_id` または `candidate_symbol` 必須）。`entry_type: "note"` / `human_decision: "ADD_NOTE"` で保存。
- 許可ユーザー制御・冪等性（同一 action+user+対象+メモ本文は二重記録なし）を適用します。

`src/slack_modals.py` の `build_note_modal_view` は月次・Candidate 共通で使い回せます。

## Phase 4.3: Slack Action Blocks Production Wiring & Confirmation

月次Digest / Candidate Review のSlack投稿に**実際のアクションボタン**を添付し、押下・メモ送信後に短い ephemeral 確認応答を返します。**ボタンは判断記録であり売買承認ではありません**（「買う/売る/注文/購入実行」の文言なし）。配分変更・注文数量計算・自動売買・証券口座連携は行いません。

- **配信**: `SLACK_BOT_TOKEN` ＋ `SLACK_CHANNEL_ID` がある場合は Bot Token（`chat.postMessage`）でボタン付き投稿。無い場合は従来の Incoming Webhook でボタンなし送信し、warning を出します（既存送信は非破壊）。
- **月次ボタン**: 確認済み / 今月は見送り / 再レビュー / メモ追加。
- **Candidateボタン**: Watchlist入り / 小額検討候補 / 様子見 / 見送り / 再レビュー / メモ追加。`recommended_handling` が `DO_NOT_ACT_YET` / `HUMAN_REVIEW_REQUIRED`（または `candidate_stability=UNSTABLE`）の場合は `小額検討候補`・`Watchlist入り` を非表示（表示ゲーティング）。Candidate Review投稿では `candidate_verdict` から handling を導出（`REJECT_FOR_NOW`/`INSUFFICIENT_DATA` は買い系ボタン非表示、`APPROVE_SMALL_TEST_BUY` は全表示）。
- **確認応答**: 「記録しました: 月次レビューを確認済みにしました」「記録しました: GRID を様子見として保存しました」「メモを記録しました: GRID」「この候補はUNSTABLEのため、小額検討候補にはできません。再レビューまたは様子見を選択してください」。
- **value/metadata**: 将来の元メッセージ更新に備え `channel_id` / `message_ts` を安全な value に含めます（それ以外の秘密情報は含めません）。押下時ブロック・冪等性・許可ユーザー制御は維持。

## Phase 4.4: Original Message Update & Audit Trail Surfacing

ボタン押下／メモ送信後、ephemeral確認に加えて**元のSlackメッセージ**にも「記録済み」ステータスを反映します（`chat.update`）。後からSlackを見ても、誰がどの判断を記録したか分かります。**表示のみ**で、**append-onlyログが主・Slack更新は副**。`chat.update` 失敗時もログ記録は維持します（取り消しません）。配分変更・注文数量計算・自動売買・証券口座連携は行いません。

- ステータスblock（`block_id: committee_record_status`）を既存blocksに**追加または置換**（全文再生成しない／Digest本文を保持）。
- 表示例: `✅ 記録済み: 月次レビュー = 確認済み by U123 at 2026-06-05 09:12` / `✅ 記録済み: GRID = 様子見 by U123 at 2026-06-05 09:15` / メモ送信時 `📝 記録済み: GRID = メモ追加 by …`、判断＋メモ併記時は末尾に `📝 メモあり`。
- `channel_id` / `message_ts` が無い場合、または元blocksが取得できない場合は**安全にnoop**（メッセージをclobberしない）。
- `chat.update` 呼び出しは `message_updater` で注入可能（テストでモック）。実運用は Socket Mode の `body.message.blocks` を渡して本文を保持。
- ペイロード/メタdata/blocksに秘密情報（api_key/token/secret/prompt/raw_response）を含めません。

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

### Phase 3.5: Candidate Review（新規買付候補レビュー）

月次運用レビューとは**別系統**で、新規買付・追加購入候補（GRID/BOTZ/ARKQ等）をCommitteeで審査します。**明示実行時のみ**動作し、通常の月次実行では起動しません。**助言専用**で、配分変更・注文数量計算・自動売買・証券口座連携は一切行いません（`allocation_override` 常に false、`final_allocation` 不変）。`intended_amount_jpy` は**検討額**であり株数には変換しません。月次レビューより**厳しめ**に判定します（committee PASS でも小口テスト買い止まり）。

候補は `data/watchlist_candidates.csv`（列: `symbol,name,asset_type,theme,candidate_action,intended_amount_jpy,account,reason,time_horizon,notes`）で定義。`candidate_action`: `NEW_BUY` / `ADD` / `HOLD_REVIEW` / `TRIM_REVIEW` / `EXIT_REVIEW`。

`candidate_verdict`: `APPROVE_FOR_WATCHLIST` / `APPROVE_SMALL_TEST_BUY` / `WAIT_FOR_BETTER_ENTRY` / `REJECT_FOR_NOW` / `INSUFFICIENT_DATA`。Core/Satellite 両委員会を実行し、各候補で既存ポートフォリオとの重複・テーマ集中・価格トレンド・高値掴み・長期保有仮説・見直し条件・反証条件を必ず確認します。

```bash
# 全候補をレビュー（reports/candidates/candidate_review_YYYYMMDD.md を生成）
python main.py --candidate-review --candidate-file data/watchlist_candidates.csv

# 単一候補のみ
python main.py --candidate-review --candidate-symbol GRID

# Slackにも要約を送信
python main.py --candidate-review --candidate-review-slack
```

出力: `reports/candidates/candidate_review_YYYYMMDD.md`（候補ごとに verdict・買い/反対根拠・リスク・required_checks・エントリー条件・反証条件・sizing_note・メンバー所見）。`reports/` は `.gitignore` 済み。

### Phase 3.6: Candidate Review Decision Log

Candidate Review の結果を `logs/candidate_review_log.jsonl` に **1候補=1行・append-only** で保存します（versioned schema 1.0）。GRID/BOTZ/ARKQ等の判定・根拠・反対理由・人間判断を時系列で追跡し、**LLMの判定揺れを後から検出**できる土台です。Monthly Review の判断ログ（`logs/committee_decision_log.jsonl`）とは**完全分離**。配分変更・注文数量計算・自動売買はしません（`allocation_override` 常に false、`intended_amount_jpy` は検討額で株数に変換しない）。

- **Candidate Review実行時、AIレビュー結果は常に保存**されます。
- **人間判断**は `--record-candidate-decision` 指定時のみ `human_decision` / `human_note` に保存（未指定時は `null`）。

```bash
# 全候補をレビューしログ追記
python main.py --candidate-review --candidate-file data/watchlist_candidates.csv

# GRIDのみレビューしログ追記
python main.py --candidate-review --candidate-symbol GRID

# 人間判断付きでログ追記
python main.py --candidate-review --candidate-symbol GRID \
  --record-candidate-decision --candidate-human-decision WAIT --candidate-human-note "判定揺れ確認のため様子見"
```

`--candidate-human-decision` の値: `WATCHLIST` / `SMALL_TEST_BUY_CANDIDATE` / `WAIT` / `REJECT` / `RE_REVIEW` / `SKIP`。

保存フィールド（schema_version 1.0）: `review_id` / `timestamp`(JST) / `review_date` / `candidate_symbol` / `candidate_name` / `asset_type` / `theme` / `candidate_action` / `intended_amount_jpy`(検討額) / `account` / `candidate_verdict` / `confidence` / `strongest_buy_thesis` / `strongest_rejection_thesis` / `key_risks` / `required_checks` / `entry_conditions` / `invalidation_conditions` / `sizing_note` / `final_advisory` / `member_outputs` / `allocation_override`(常にfalse) / `human_decision`(初期null) / `human_note`(初期null)。APIキー・プロンプト・raw_response は保存しません。壊れた行があっても読み取りは継続します。

### Phase 3.7: Candidate Review Stability Check

`logs/candidate_review_log.jsonl` を読み、候補ごとに直近2件のレビューを比較して**判定の安定性（LLMの判定揺れ）を監査**します。これは**承認判断ではなく判定品質の監査**です。決定的なPythonロジックのみで、LLMには依存しません。配分変更・注文数量計算・自動売買はしません。

`stability`: `STABLE` / `MINOR_CHANGE` / `UNSTABLE` / `INSUFFICIENT_HISTORY`。`severity`: `NONE` / `INFO` / `CAUTION` / `MATERIAL`。`verdict_direction`: `IMPROVED` / `WORSENED` / `UNCHANGED` / `MIXED` / `UNKNOWN`。`recommended_handling`: `OK_FOR_WATCHLIST` / `REVIEW_BEFORE_ACTION` / `HUMAN_REVIEW_REQUIRED` / `DO_NOT_ACT_YET`。

主な判定: 直近2件なし→`INSUFFICIENT_HISTORY`、WAIT⇔REJECT間で揺れ→`UNSTABLE`、REJECTへ悪化→`UNSTABLE`/`MATERIAL`、confidence差20pt以上→`CAUTION`、reject連続→`STABLE`だが`DO_NOT_ACT_YET`、人間判断と現verdictが大きく乖離→`HUMAN_REVIEW_REQUIRED`。揺れの大きい候補は Slack承認対象にせず人間レビュー必須として扱えます。

```bash
# 候補ごとの安定性レポート（reports/candidates/candidate_stability_YYYYMMDD.md）
python main.py --candidate-stability

# GRIDのみ
python main.py --candidate-stability --candidate-symbol GRID

# Slack送信
python main.py --candidate-stability --candidate-symbol GRID --candidate-stability-slack
```

### 安全ルール

- `allocation_override` は常に `false`（`shadow_mode` 検証で強制）
- Committee は配分（weights）を一切返さず、`final_allocation = quant_recommendation` を維持
- 自動売買・注文生成は行わない（メンバー出力中の不適切な売買表現は検出して保留）
- Slackボタンのインタラクティブ受信は未実装（表示専用）

## Phase 5: Integrated Decision Audit Summary

月次レビュー・Investment Committee・Candidate Review・Candidate Stability・Slackボタン押下・人間メモを統合し、**月次の投資判断監査サマリー**を生成します。「いつ・誰が・何を判断したか」「AI判断と人間判断がどこでズレたか」「次回何を確認すべきか」を1つのMarkdownで確認できます。**決定的なPythonロジック**（LLM非依存）。**振り返り用の監査であり売買指示ではありません**。配分変更・注文数量計算・自動売買・証券口座連携は行いません。

入力（append-onlyログ）: `logs/committee_decision_log.jsonl` / `logs/candidate_review_log.jsonl` / `logs/slack_decision_log.jsonl`、および Candidate Stability（再計算）。ログ欠如・壊れた行があっても安全に処理します（秘密情報はサマリーに出しません）。

出力セクション: 今月の結論 / 月次レビュー判断 / Candidate Review判断 / AI委員会 vs 人間判断 / 判断が割れた項目 / Stabilityが不安定な候補 / 人間メモ一覧 / 次回確認事項 / 安全注記。

**AI vs 人間の整合判定**（candidate）: `aligned`（一致）/ `mild_divergence`（軽微なズレ）/ `divergence`（要注意のズレ）。例: AI=`REJECT_FOR_NOW` × Human=`WAIT` → divergence、AI=`WAIT_FOR_BETTER_ENTRY` × Human=`WATCHLIST` → mild_divergence、AI=`REJECT_FOR_NOW` × Human=`REJECT` → aligned。

```bash
python main.py --audit-summary                       # 当月
python main.py --audit-summary --audit-month 2026-06 # 月指定
python main.py --audit-summary --audit-month 2026-06 --audit-output reports/audit/decision_audit_202606.md
```

出力先（既定）: `reports/audit/decision_audit_YYYYMM.md`（`reports/` は `.gitignore` 済み）。

## 既知の制限事項・注意点

- 日本円建てETF（1306.T等）と米ドル建てETFを同一スコアで比較しており、**通貨換算は行っていません**
- yfinanceのデータは当日分が取得できない場合があります（前営業日データを使用）
- ターンオーバー制約は前回状態（`outputs/portfolio_state.json`）がある場合のみ適用されます
- リスクゲートはVOO（S&P500）の60日リターンのみで判定します
- 推奨配分はあくまで参考情報であり、投資判断・売買は手動で行ってください
