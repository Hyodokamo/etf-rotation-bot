# Signal Review スマホ完結 MVP 実装方針

対象: 個別銘柄ウォッチ / Signal Review をスマホSlackで判断記録まで完結する
作成日: 2026-06-21
種別: 設計 → **実装済み（2026-06-21）**
関連: `docs/signal_review_architecture.md`

---

## 実装ステータス（2026-06-21）

実装済み（Signal Review系のみ。月次ローテーションのロジック/投稿分岐は不変更）:

1. **候補のみ安全upsert** — `watchlist_store.upsert_candidate_entries()`。
   `BUY_CANDIDATE` / `HIGH_PRIORITY_CANDIDATE` のみ追加。`NO_ACTION` 等は積まない。
   `USER_APPROVED` / `USER_REJECTED` は不可侵。`main.py --crash-signal-check --persist-candidates`
   と `daily_signal_check.py --persist-candidates`（opt-in）で dry-run でも候補を永続化。
2. **ボード要約をSlackに表示** — `slack_signal_digest.board_summary_lines()` を候補に付与
   （賛成Top1 / 反対Top1 / AI監査一言）。
3. **候補ありはボタン付き投稿** — `deliver_signal_digest()` が `build_signal_review_blocks()` を
   Bot Token で送信、無ければWebhookテキストにフォールバック。`main.py` crash-signal-check のみ変更。
4. **watchlist未存在でもボタン記録可** — `record_human_signal_decision(create_if_missing=, protect_human_locked=)`。
   人間ロック維持。ログに `no_auto_trade` / `no_order_quantity` / `final_decision_by_human` を記録。
5. **NAS Socket Mode常駐手順** — `docs/SYNOLOGY_SETUP.md §11`、`.env.example` 更新。

テスト: `tests/test_signal_review_mobile_mvp.py`（18件）。全体 `pytest` = 1149 passed。
未着手/対象外は本ドキュメント §8 を参照。

---

## 0. 前提（調査からの要点）

- 判断記録ボタン（6種）・ルーター・人間判断ログ・Socket Mode常駐スクリプトは **Phase 5.2 / 7.2 / 7.4 で実装済み**。
- 不足はコードではなく **運用配線**: ①watchlistがNASで空、②日次プッシュにボタンが付かない、③メンバーコメントがSlackに出ない、④Socket Mode未常駐。
- よってMVPは「新規開発」より「**有効化＋最小の安全変更**」が中心。

---

## 1. スマホ完結のためのMVP案（比較）

判断記録をスマホで行う方式の比較。評価は本リポジトリの既存実装を踏まえる。

| 案 | 概要 | 実装難易度 | 既存活用度 | NAS相性 | セキュリティ | スマホUX | 壊れにくさ |
|---|---|---|---|---|---|---|---|
| **A. Slackボタン** | 候補通知にボタン添付、押下で記録 | ★★（**ほぼ実装済み**・配線のみ） | ◎ | ○（常駐要） | ○（allowlist+ack+冪等） | ◎ | △（常駐落ちで無反応） |
| B. スレッド返信 | `confirm/skip/rerun` を返信で受信 | ★★★（受信解釈を新規実装） | △ | ○ | △（自由文検証） | ○ | △ |
| C. 簡易Webリンク | NAS上の画面をSlackから開く | ★★★ | △ | ○（LAN内限定） | △（公開範囲管理） | △（外出先で開けない） | △ |
| D. 通知のみ・記録後回し | スマホは閲覧、記録はPC/NAS | ★ | ◎ | ◎ | ◎ | △（完結しない） | ◎ |

### 推奨: **A（Slackボタン）を主軸**
- 6ボタン（`候補として確認済み / Watch継続 / Hold Off / 却下 / 再評価依頼 / メモ追加`）と記録経路（`route_signal_action` → `signal_human_decision_log.jsonl`）は既存。
- B/Cは既存ボタンと機能重複、または外出先要件を満たさず、優先度低。
- Dは安全だが「スマホ完結」の目的を満たさないため、**Aのフォールバック**として残す（常駐停止時はCLI記録）。

---

## 2. MVPで実装する範囲

「銘柄が出たらスマホで確認→記録まで」を成立させる最小セット。

1. **watchlist の永続化**（運用＋設定）
   - `data/watchlist.csv` をNASの永続volume/ディレクトリに固定（gitignoreのまま）。
   - 日次で候補（BUY_CANDIDATE / HIGH_PRIORITY_CANDIDATE）を watchlist に反映できる運用にする。
   - 方式は2択（MVPは(a)を推奨）:
     - (a) 候補のみ安全 upsert（`--dry-run` でも BUY_CANDIDATE 以上だけは watchlist に追記、`USER_*` は不可侵）。
     - (b) `daily_signal_check.py --allow-watchlist-update` を採用（全status更新。既存挙動だが書込み範囲が広い）。
2. **日次「候補あり」通知を Bot Token + ブロックで送る**
   - 候補が1件以上のとき、`build_signal_review_blocks(items)` を使って **ボタン付き**でチャンネル投稿。
   - 候補ゼロのときは従来Webhookテキスト（変更なし）。
3. **ボタン押下を watchlist 未存在でも記録可能に（最小の安全変更）**
   - `record_human_signal_decision()` が「symbol未存在で ValueError」を、ボタンが運ぶ symbol で **作成 or 更新**に緩和。
4. **メンバーコメント要約をダイジェストに1〜2行**
   - 賛成根拠Top1 ＋ 反対意見Top1 ＋（あれば）AI監査の一言。`SignalResult.positive_reasons / dissenting_views` を利用。
5. **NASで Socket Mode 常駐を起動**（設定）
   - 既存 `scripts/run_slack_bot.sh` または docker-compose `slack-bot`。

---

## 3. 変更対象ファイル候補

| ファイル | 変更内容 | 種別 |
|---|---|---|
| `src/signals/slack_signal_digest.py` | `build_signal_digest_text()` に「候補のメンバーコメント要約（賛成Top1/反対Top1）」を追加。候補ありの戻り値にブロック添付可否のフックを用意 | 小〜中 |
| `main.py::_handle_crash_signal_check()` | 候補が1件以上＋Bot Token＋Channelがある時、`build_signal_review_blocks()` を `post_committee_message()` で送る分岐を追加（無ければ従来 `post_signal_digest()`） | 小 |
| `src/signals/signal_review.py::record_human_signal_decision()` | 「未存在symbolは作成 or 更新」に緩和（`USER_APPROVED/USER_REJECTED` の人間ロックは維持） | 小 |
| `src/signals/watchlist_store.py` | （案(a)採用時）dry-runでも BUY_CANDIDATE 以上のみ安全に upsert するヘルパ追加 | 小 |
| `.env.example` / `docs/SYNOLOGY_SETUP.md` | Socket Mode常駐・`SLACK_BOT_TOKEN/APP_TOKEN/CHANNEL_ID/ALLOWED_USER_IDS` の有効化手順を追記 | 小 |

> `main.py` の **月次Slack投稿分岐** は変更しない。触るのは crash-signal-check（Signal Review系）側のみ。

---

## 4. Slackボタン活用可否

- **活用可（推奨）**。既存資産:
  - ブロック生成: `build_signal_review_blocks(items)`（`src/slack_signal_actions.py`）。
  - 受信・記録: `@app.action(^signal_)`（`src/slack_interaction_handler.py`）→ `route_signal_action()`。
  - 冪等性: `logs/slack_signal_idempotency.jsonl`、ユーザーallowlist、ack即応。
  - メモ: `signal_note_modal`（自由記述の判断メモ）。
- 必要条件: `SLACK_BOT_TOKEN` + `SLACK_APP_TOKEN`（Socket Mode）+ `SLACK_CHANNEL_ID` + 常駐プロセス。
- 注意: ボタンvalueは whitelisted キーのみ（secret混入は構造的に不可）。押下＝**判断の記録**であり売買承認ではない旨を文言で明示（既存実装済み）。

---

## 5. 判断記録の保存形式

- 既存形式を踏襲（新フォーマットを作らない）:
  - 人間判断: `logs/signal_human_decision_log.jsonl`（append-only。symbol/decision/prev_status/new_status/note/no_order_quantity/no_auto_trade）。
  - Watchlist状態: `data/watchlist.csv`（`USER_APPROVED/USER_REJECTED` は人間ロック）。
  - ボタン冪等: `logs/slack_signal_idempotency.jsonl`。
- §3-3 の緩和後も、`USER_APPROVED/USER_REJECTED` は **人間のみ設定可**・AI上書き不可の不変条件を維持。

---

## 6. テスト観点

1. **digest（メンバー要約）**: 候補ありで賛成Top1/反対Top1が出る。候補ゼロで従来文面・例外なし。禁止語（買え/売れ/購入実行/売却実行/買付承認）が出ない。
2. **投稿分岐（main.py crash-signal-check）**: Bot Token+Channel+候補あり→ブロック投稿、無し→Webhookテキスト（投稿関数はモック）。月次投稿経路に影響しないこと。
3. **record緩和**: watchlist未存在symbolでボタン押下→新規行が作成され記録される。既存 `USER_APPROVED` 行は上書きされない（人間ロック維持）。
4. **既存回帰**: `pytest tests/test_slack_signal_actions.py tests/test_signal_review*.py tests/test_slack_command_router.py tests/test_signal_*` が緑。
5. **安全不変条件**: 出力・ログに `no_order_quantity=True / no_auto_trade=True`、APIキー/Webhook/Bot Token非出力。
6. **冪等性**: 同一 user+symbol+action の二重押下が二重記録されない。

---

## 7. 実装手順（推奨順）

1. 本ドキュメントを仕様基準として確定（本PRで作成）。
2. `slack_signal_digest`: メンバーコメント要約の追加＋単体テスト。
3. `record_human_signal_decision`: 未存在symbolの作成/更新緩和＋テスト（人間ロック維持を含む）。
4. `main.py _handle_crash_signal_check`: 候補ありのBot Tokenブロック投稿分岐＋分岐テスト。
5. （案(a)）watchlist 安全upsertヘルパ＋テスト。
6. `.env.example` / `docs/SYNOLOGY_SETUP.md`: Socket Mode常駐手順を追記。
7. NASで `run_slack_bot.sh` 常駐 → 実機で候補通知→ボタン押下→`signal_human_decision_log.jsonl` 追記を確認。

---

## 8. 今回やらないこと

- 自動売買 / 注文数量計算 / 証券口座連携 / 楽天証券 / NISA操作: 実装しない。
- SELLシグナル: 予約のまま（NO_ACTION固定）。
- 新規AIエージェント追加: しない（既存7名流用）。
- 月次ETFローテーションのロジック・Slack投稿分岐の変更: しない。
- スレッド返信(B案) / 簡易Web(C案): 今回は採用しない（Aと重複 or 外出先要件を満たさない）。
- レポート(`reports/daily_signal_report.md`)の絶対URL化: 別タスク（本MVPはダイジェストへの要約掲載で代替）。

---

## 関連
- アーキ調査 → `docs/signal_review_architecture.md`
- 月次側のスマホUX → `docs/slack_mobile_ux_review.md`
