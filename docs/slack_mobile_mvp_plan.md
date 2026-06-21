# Slack モバイル対応 MVP 実装方針

対象: ETF Rotation Bot 月次レビュー通知のスマホUX改善
作成日: 2026-06-21
関連: `docs/slack_mobile_ux_review.md` / `docs/slack_message_templates.md`

---

## 0. スコープ方針

二段構えで、**MVP-1（文面再設計）を必須・最小コード変更**、**MVP-2（ボタン有効化）を任意・主に設定/配線** とする。
理由: ボタン用コード（Socket Mode・action router・decision log）は Phase 4.1〜7.4 で**既に実装済み**のため、新規開発ではなく有効化に近い。MVP-1単独でもUXは大きく改善し、MVP-2のフォールバックも兼ねる。

| | 内容 | 種別 | 優先度 |
|---|---|---|---|
| MVP-1 | 通知文面のスマホ再設計（結論ブロック・順序・自動売買注記・CLI最小化） | コード少改修 | 必須 |
| MVP-2 | 既存Slackボタン経路の有効化（Bot Token + 常駐 + 投稿分岐の調整） | 設定中心＋小改修 | 任意 |
| 対象外 | スレッド返信(C)、簡易Web(D)、外部SaaS(E)、自動売買全般 | — | やらない |

---

## 1. MVP-1: 通知文面のスマホ再設計

### 1.1 変更対象ファイル候補（最小）

| ファイル | 変更内容 | 規模 |
|---|---|---|
| `src/slack_blocks.py` | 新規 `build_mobile_summary_header(state, run_date, ...)` を追加。状態（PASS/REVIEW_REQUIRED/FAIL）から「1行結論＋推奨アクション＋急ぎ度＋🛡️自動売買注記」を生成。`build_review_decision_section()` のCLI案内を「後でPC/NASで実行」と明示する文言に調整し行数を圧縮。 | 中 |
| `src/slack_client.py` | `build_slack_summary()` の冒頭に header ブロックを差し込み、Top配分を `·` 区切り1行に、詳細指標を結論の後方へ並べ替え。レポート行に「スマホで開けない場合あり」注記。 | 中 |
| `src/committee/slack_digest.py` | `build_executive_digest()` 冒頭に同じ header を差し込み、委員会の詳細（論点・各エージェント一言・反対意見・監視条件）を結論の後方へ。長文化対策として各セクションは折りたたみ前提の順序に。 | 中 |
| `config.yaml` / `src/config_loader.py`（任意） | `slack_review_decision` に `mobile_layout: true`（既定true）等のフラグを追加し、旧文面に戻せる退避口を用意。過剰なら省略可。 | 小 |

> 状態の決定ロジックは既存の `pre_trade_gate.overall_status`（PASS / PASS_WITH_CAUTION / REVIEW_REQUIRED / FAIL）を流用する。新たな判定は作らない。

### 1.2 Slack通知生成ロジックの改善方針
- **header を1か所に集約**（`build_mobile_summary_header`）し、`build_slack_summary` と `build_executive_digest` の両方から呼ぶ。重複実装を避ける。
- 状態→文言マッピングは辞書で持つ（`PASS: ("制約クリア。確認のみでOK…", "急ぎ対応なし")` 等）。`docs/slack_message_templates.md` の3パターンに一致させる。
- 既存の本文要素（モード・ターンオーバー・Top配分・AI監査・Gate・委員会）は **消さず順序のみ変更**。後方互換のため関数シグネチャは維持し、内部の行構築順を変える。
- 文言は売買表現を使わない既存ガイドを踏襲（`src/slack_actions.py` のラベル方針と一致）。

### 1.3 レポートリンク問題の扱い（MVP-1）
- 当面は **本文要約の充実で対応**。リンクは現状のパス表示を残しつつ「スマホからは開けない場合あり・要約で判断可」を併記する（誤誘導を防ぐ）。
- 第二段階（任意）として、Synology Drive 共有リンクを `.env`（例 `REPORT_BASE_URL`）で受け取り、設定時のみ本文に絶対URLを出す方式を検討。外部SaaS保存は不採用。

### 1.4 判断記録導線の扱い（MVP-1）
- 「スマホは見るだけ／記録は後でPC・NASで」を明示。CLIは状態に応じ1〜3行に圧縮（テンプレ準拠）。
- 既存 `--record-decision`（`REVIEW_CONFIRMED` / `SKIP_THIS_MONTH` / `REQUEST_RERUN` / `MANUAL_OVERRIDE`）はそのまま利用。新コマンドは追加しない。

---

## 2. MVP-2: Slackボタン有効化（任意）

### 2.1 現状の配線と不足点
- 実装済み: `src/slack_interaction_handler.py`（Socket Mode）、`src/slack_action_router.py`、`src/slack_actions.py`、`src/slack_blocks.py build_monthly_action_blocks`、`src/slack_publish.py`、`scripts/run_slack_bot.sh`、docker-compose `slack-bot`、記録先 `logs/slack_decision_log.jsonl`。
- 不足/要調整:
  1. `main.py` の月次投稿分岐が `committee_result is not None and bot_token_available()` のときだけボタン付き。**Committee無効時（`--no-ai-audit`等）はボタンが出ない**。
  2. Webhookのみ運用ではボタン不可（Bot Token + App Token + `SLACK_CHANNEL_ID` が必要）。
  3. NASでの常駐運用（タスクスケジューラはワンショット向き。常駐は別途）。

### 2.2 変更対象ファイル候補
| ファイル | 変更内容 |
|---|---|
| `main.py`（投稿分岐） | `bot_token_available() and SLACK_CHANNEL_ID` があれば、Committee有無に関わらず `build_monthly_digest_blocks` でボタン付き投稿にする。Bot Tokenが無ければ従来Webhook（MVP-1文面）にフォールバック。 |
| `.env.example` / `docs/SYNOLOGY_SETUP.md` | ボタン有効化手順（App作成・Socket Mode ON・必要スコープ・`run_slack_bot.sh` 常駐 or docker-compose `slack-bot`）を追記。 |
| （任意）`config.yaml slack_review_decision.interactive_enabled` | true 化と、`main.py` 側でのフラグ尊重。 |

### 2.3 運用（NAS）
- `scripts/run_slack_bot.sh` を常駐（DSM のタスクスケジューラ「起動時」トリガ、または docker-compose `slack-bot up -d`）。
- `SLACK_ALLOWED_USER_IDS` に本人のIDのみ設定。
- 常駐が落ちてもMVP-1の文面でCLI記録に退避できるため、致命傷にはならない。

---

## 3. テスト観点

### MVP-1
- **単体**: `build_mobile_summary_header` が状態ごと（PASS / PASS_WITH_CAUTION / REVIEW_REQUIRED / FAIL）に正しい結論文・急ぎ度・🛡️注記を返す。
- **文言**: 売買表現（買う/売る/購入/注文/数量）が出力に**含まれない**ことをアサート（正規表現テスト）。`docs/slack_message_templates.md` の3パターンと整合。
- **順序**: 結論ブロックが本文先頭、Top配分が `·` 区切り1行、詳細が後方であることを検証。
- **後方互換**: `build_slack_summary` / `build_executive_digest` のシグネチャ不変、Committee有り/無し両経路でクラッシュしない（既存 `tests/test_slack_*` を回す）。
- **FAIL分岐**: FAIL時に `REVIEW_CONFIRMED` を勧めない／MANUAL_OVERRIDEにコメント必須が明記される。
- **秘密情報**: 出力に Webhook URL / Bot Token が混入しないこと。

### MVP-2
- 既存 `tests/test_slack_interactivity.py` / `test_slack_action_blocks_wiring.py` / `test_slack_message_update.py` が緑のまま。
- `main.py` 投稿分岐: Bot Token あり＋Channel あり→ボタン経路、無し→Webアフックフォールバック、を分岐テスト（投稿関数はモック）。
- `slack_interaction_handler --dry-run` がサンプルpayloadを検証できる（ネット不要）。

### 共通
- `SLACK_WEBHOOK_URL` 未設定時に warning でスキップし例外を投げない（既存挙動維持）。

---

## 4. 実装手順（推奨順）

1. `docs/slack_message_templates.md` を仕様の基準として固定（本PRで作成済み）。
2. **MVP-1a**: `src/slack_blocks.py` に `build_mobile_summary_header` を追加＋単体テスト。
3. **MVP-1b**: `build_slack_summary`（`src/slack_client.py`）に header を組み込み、配分1行化・順序変更・レポート注記。テスト更新。
4. **MVP-1c**: `build_executive_digest`（`src/committee/slack_digest.py`）に同 header を組み込み、詳細を後方へ。テスト更新。
5. **MVP-1d**: `build_review_decision_section` のCLI案内文を「後でPC/NASで実行」に圧縮。文言テスト追加。
6. ここで一旦リリース可能（MVP-1完了）。実機Slackで PASS/REVIEW/FAIL の表示をスマホ確認。
7. **MVP-2a**（任意）: `main.py` 投稿分岐を調整し、Bot Token + Channel があればボタン付きに。分岐テスト。
8. **MVP-2b**（任意）: `.env.example` と `docs/SYNOLOGY_SETUP.md` にボタン有効化・常駐手順を追記。
9. **MVP-2c**（任意）: NASで `run_slack_bot.sh` 常駐 → 実機でボタン押下→`logs/slack_decision_log.jsonl` 追記を確認。

---

## 5. 既知の制約・留意

- ボタン経路は常駐プロセス前提。停止中は無反応になるため、CLI記録（MVP-1）を常にフォールバックとして残す。
- レポートの絶対URL化（Synology Drive）は別タスク。MVP-1は要約充実で代替する。
- 秘密情報（Webhook URL / Bot Token）は本文・ログ・ボタンvalueに出さない（既存の除去実装を維持）。
- 自動売買・注文数量・証券口座連携・NISA操作は本MVPでも一切扱わない。ボタン/CLIは「判断の記録」であり売買承認ではない。

---

## 6. Backlog（今回スコープ外・将来対応）

- **FAIL時のcheck_idをSlack表示だけ人間可読ラベルに変換**: `build_review_decision_section()` の「*月次レビュー判断*」セクションは現状 `check_id`（例 `turnover_limit_check`）をそのまま列挙する。本文上部のヘッダ／Pre-Trade Gate行では人間可読メッセージ（例「ターンオーバーが上限20%を超過」）を表示済みのためリリースを止める問題ではないが、Slack表示の一貫性のため check_id→ラベルのマッピングを導入したい。decision log / JSON 等の機械可読箇所は check_id のまま維持する（表示層のみ変換）。
- MVP-2: Slackボタン有効化（本ドキュメント第2章）。今回未着手。
- レポートの絶対URL化（Synology Drive 共有リンク）。今回未着手。

## 関連

- UX調査 → `docs/slack_mobile_ux_review.md`
- 文面案 → `docs/slack_message_templates.md`
