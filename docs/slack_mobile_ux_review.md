# Slack モバイルUX調査レポート

対象: ETF Rotation Bot 月次レビュー通知のスマートフォンSlack体験
作成日: 2026-06-21
前提: NAS（Synology DS224+）で定期実行 → Slack通知 → スマホで確認 → 必要なら後でPC/NASで対応

---

## 0. 結論サマリ（先に読む用）

- 現状通知は **PC開発者向け**。スマホでは「結論が一目で分からない」「CLIコマンドが押せない／コピーしづらい」「レポートがローカルパスで開けない」の3点が主要課題。
- ただし調査の結果、**Slackボタンによる判断記録の仕組み（Socket Mode）は既に実装済み**（Phase 4.1〜7.4）。`src/slack_interaction_handler.py`・`src/slack_actions.py`・`scripts/run_slack_bot.sh`・docker-compose の `slack-bot` サービスまで揃っている。
- したがって「まず文面だけ直す（B/C案は将来）」という当初仮説は **半分は正しいが、ボタンは“将来の大工事”ではなく“既存機能の有効化”** という点で更新が必要。
- **推奨MVP**: ①通知文面をスマホ向けに再設計（全員に効く・最低リスク）を必須とし、②既に存在するボタン経路を「任意で有効化できる」状態として案内する二段構え。レポート閲覧は当面 Synology Drive 共有リンク or Slackファイルアップロードで補う。

---

## 1. 既存実装の確認結果

| 項目 | 実装箇所 | 現状 |
|---|---|---|
| Slack通知（テキスト） | `src/slack_client.py` `post_to_slack()` | Incoming Webhook（`SLACK_WEBHOOK_URL`）に `{"text": ...}` を POST |
| 月次サマリ文面（非Committee） | `src/slack_client.py` `build_slack_summary()` | 冒頭にプロンプト例の文面を生成 |
| 月次サマリ文面（Committee有効時） | `src/committee/slack_digest.py` `build_executive_digest()` | 委員会の論点・各エージェント一言・反対意見Top3等で**さらに長文化** |
| 判断記録CLIの案内 | `src/slack_blocks.py` `build_review_decision_section()` | `python main.py --record-decision ...` を本文に列挙 |
| `--record-decision` 実装 | `main.py` `_handle_record_decision()` | パイプライン再実行なしで判断のみ記録 |
| 判断種別 | `src/decision_logger.py` `ReviewDecision` | `REVIEW_CONFIRMED` / `SKIP_THIS_MONTH` / `REQUEST_RERUN` / `MANUAL_OVERRIDE` |
| decision log 保存 | `src/decision_logger.py` `save_decision_log()` | `outputs/YYYY-MM/decision_log.json` と `.md`、`run_log.json` も更新 |
| report markdown | `src/report_builder.py` `save_report()` / `config.yaml report.output_dir: outputs` | `outputs/report_YYYY-MM-DD.md` |
| NAS上の出力パス | `docs/SYNOLOGY_SETUP.md` | `/var/services/homes/HyodoAdmin/apps/etf-rotation-bot/outputs/...` |
| Slack Webhook方式 | `src/slack_client.py` | Incoming Webhook（テキストのみ） |
| Slack Bot / Interactivity | `src/slack_interaction_handler.py` ほか | **実装済み**（Socket Mode リスナー、`/etf` コマンド、メモ用モーダル、メッセージ更新） |
| ボタン操作対応構成 | `src/slack_actions.py` / `src/slack_blocks.py` / `src/slack_publish.py` | **実装済み**。Bot Token あり＋`SLACK_CHANNEL_ID`あり＋Committee実行時に `chat.postMessage` でボタン付き投稿 |
| ボタン押下の記録先 | `src/slack_actions.py` `append_slack_decision_log()` | `logs/slack_decision_log.jsonl`（append-only、秘密キー除去あり） |
| `.env` / 設定 | `.env.example` / `config.yaml slack_review_decision` | `SLACK_WEBHOOK_URL` / `SLACK_BOT_TOKEN` / `SLACK_APP_TOKEN` / `SLACK_SIGNING_SECRET` / `SLACK_ALLOWED_USER_IDS` / `SLACK_CHANNEL_ID` / `SLACK_INTERACTIVE_ENABLED`。config 側は `enabled: true`・`interactive_enabled: false` |

### 重要な発見：ボタンは「未実装」ではなく「未配線・未有効化」
- `main.py`（月次パイプライン末尾）では、`committee_result is not None and bot_token_available()` のときだけボタン付き投稿（`build_monthly_digest_blocks`）になり、それ以外は **Webhookテキスト（ボタンなし）** にフォールバックする。
- プロンプトに貼られた通知は、この **Webhookテキスト経路**（=Committeeなし or Bot Token未設定）の出力。つまり「ボタンが出ない」のは設計上の分岐であって、コードが無いからではない。
- 結果として、判断記録の現実的な選択肢は当初想定より広い（後述の改善案比較参照）。

---

## 2. スマホSlack利用時の課題（UX評価）

### 2.1 読みやすさ
- **結論が3行目までに来ない**。1行目はタイトル、2行目は戦略variant名（内部識別子 `cash_fallback_separated`）で、ユーザーが本当に知りたい「今月どうすべきか」が下方にある。
- `PASS / REVIEW_REQUIRED / FAIL` の **意味が説明されていない**。アイコンはあるが「PASSなら何をすればいいか」が言語化されていない。
- Top配分は `BND: 32.1%` の縦並び。スマホでは許容範囲だが、Committee有効時は論点・各エージェント一言・反対意見Top3・監視条件Top3…と続き、**1画面に収まらない長文**になりスクロール疲れを起こす。
- 数値は多め。ターンオーバー上限・防御資産比率などPC向け指標が前面に出る。
- 投資判断上の注意（「これは推奨であり最終判断は人間」「相場急変時は別」等）はテキストにあるが埋もれている。

### 2.2 行動導線
- 本文が `python main.py --record-decision REVIEW_CONFIRMED` を提示するが、**スマホからは実行不可能**。コピーしてPCに持っていくにも、Slackモバイルのコードコピーは取りこぼしやすい。
- 「今すぐ対応が必要か／後でいいか」がメッセージから判別できない。FAIL時もPASS時も同じトーンで CLI が並ぶ。

### 2.3 レポート閲覧
- `レポート: outputs\report_2026-06-20.md` は **NASローカルの相対パス**。スマホのSlackからは開けない。Windowsの `\` 区切りで、NAS実体は `/var/services/...` 配下のため、表示そのものが誤誘導になりうる。

---

## 3. ユーザージャーニー

### 現状（As-Is）
1. 通勤中、スマホでSlack通知を受け取る
2. タイトルと戦略名を見るが「で、何をすればいい？」が分からずスクロール
3. Top配分とPASSは分かるが、判断の良し悪しを自分で評価しないといけない
4. レポートを見ようとするがローカルパスで開けない
5. CLIコマンドが並ぶが押せない → 「家に帰ってPCで」となり、多くの場合そのまま放置

### 理想（To-Be）
1. 通知の **1行目で結論**（例:「✅ 今月は確認のみでOK。急ぎ対応なし」）
2. 2〜3行で **推奨アクションとTop配分**
3. 折りたたまれた／後方に **詳細（リスク・指標・委員会）**
4. レポートは **タップで開けるリンク** か、要約が本文に十分ある
5. 判断記録は **スマホで完結（ボタン）** か、できない場合は「あとでPCで実行する1コマンド」と明示

---

## 4. 改善案の比較

判断記録の導線について、依頼の A〜E 案を評価する。

| 案 | 概要 | 実装難易度 | NAS相性 | Slack設定の手間 | セキュリティ | スマホUX | 1か月以内 | 壊れにくさ |
|---|---|---|---|---|---|---|---|---|
| A | 確認専用。記録は後でPC/NASでCLI | ★（文面のみ） | ◎ | なし | ◎ | △（記録は別端末） | ◎ | ◎ |
| B | Slackボタンで確認/見送り/再実行 | ★★（**実装済み・有効化のみ**） | ○（常駐プロセス要） | 中（App+Token） | ○（allowlist+ack） | ◎ | ◎ | △（常駐が落ちると無反応） |
| C | スレッド返信で confirm/skip/rerun | ★★★（メッセージ受信処理を新規実装） | ○ | 中 | △（自由文の検証） | ○ | △ | △ |
| D | NAS簡易Web画面にSlackからリンク | ★★★ | ○（LAN内限定） | 低 | △（公開範囲管理） | △（LAN外で開けない） | △ | △ |
| E | Google Form / Notion / スプレッドシート | ★★ | ◎ | 低 | △（外部サービスに判断履歴） | ○ | ◎ | ○ |

### 評価コメント
- **A案** は最小リスクで全環境に効く。文面再設計と組み合わせれば、それ単独でも体験は大きく改善する。MVPの土台に最適。
- **B案** は本来「重い」が、本リポジトリでは **コードが既に存在** する。残りは「Bot/App Token取得 → Socket Mode リスナーをNASで常駐 → `SLACK_CHANNEL_ID`設定」という運用作業。ただしWebhookテキスト経路にはボタンが付かないため、`main.py` の投稿分岐を「Committeeなしでもボタン付きにする」小改修が要る（後述MVP計画）。常駐プロセスが落ちるとボタンが無反応になる運用リスクがある。
- **C案** はスレッド返信を解釈する新規受信処理が必要で、自由文ゆえ誤入力に弱い。Bが既にある以上、優先度は低い。
- **D案** はLAN内専用なら安全だが「スマホで外出先から」という主目的を満たさない。レポート閲覧用途には部分的に有効。
- **E案** は実装は軽いが、投資判断履歴を外部SaaSに置くことになり、本プロジェクトの「秘密情報を外に出さない」方針と相性が悪い。Botの decision log と二重管理にもなる。

---

## 5. レポート閲覧導線の比較

| 方式 | 長所 | 短所 | スマホ可否 |
|---|---|---|---|
| Slack本文に要約を十分載せる | 追加設定ゼロ・最速 | 本文が長くなる | ◎ |
| Markdown全文を分割投稿 | リンク不要 | 通知が冗長・流し読みに不向き | △ |
| Synology Drive / File Station 共有リンク | NAS標準機能・タップで開ける | 共有リンク発行と貼り付けの運用が要る | ◎ |
| NAS上にHTMLレポート（LAN内閲覧） | 見やすい | LAN外で開けない | △ |
| Slackファイルとしてアップロード（Bot Token） | 通知内で完結・モバイルで開ける | Bot Token必須・`files_upload` 実装が要る | ○ |
| GitHub private / gist 等 | どこでも閲覧 | 外部にレポート保存・方針と相性△ | ○ |

### 推奨
- **第一段階**: 「本文に要約を十分載せる」を徹底（リンク不達でも判断に困らない状態にする）。現状パス表示は残しつつ、**スマホからは開けない前提**で要約を充実させる。
- **第二段階（任意）**: Synology Drive の共有リンク、または Bot Token 経由の Slack ファイルアップロード。どちらもNAS運用と整合する。外部SaaS（gist等）は避ける。

---

## 6. 推奨MVP

「過剰設計を避ける個人利用MVP」として、次の二段構えを推奨する。

### MVP-1（必須・低リスク）: 通知文面のスマホ再設計
- `build_slack_summary` / `build_executive_digest` の **冒頭に「1行結論＋推奨アクション」ブロック** を追加。
- 状態を `PASS / REVIEW_REQUIRED / FAIL` の3パターンに整理し、各パターンで「急ぎ度」「やること」を明示。
- 詳細指標・委員会パートは結論の **後ろ** に移動（情報は消さない）。
- レポート行は「スマホからは開けない場合あり」を前提に、要約で判断が成立するようにする。
- 判断記録は「**スマホでは見るだけ／記録は後でPC・NASで次のコマンド**」と役割を明示し、CLIは1〜数行に短縮。

### MVP-2（任意・既存機能の有効化）: Slackボタン
- 既存の Socket Mode 経路を使い、`SLACK_BOT_TOKEN` + `SLACK_APP_TOKEN` + `SLACK_CHANNEL_ID` を設定。
- `main.py` の投稿分岐を調整し、Committee無効時でも（Bot Token があれば）ボタン付き投稿にする。
- NAS で `scripts/run_slack_bot.sh`（または docker-compose `slack-bot`）を常駐。
- `SLACK_ALLOWED_USER_IDS` で本人のみ許可。落ちても MVP-1 の文面でCLI記録に退避できるため、**MVP-1がフォールバックを兼ねる**。

この二段構えなら、設定をしない人はMVP-1の改善だけ享受し、設定した人はスマホ完結まで到達できる。

---

## 7. 当初仮説の評価

> 仮説: ①文面をスマホ向けに再設計 ②CLIは「後でPC/NASで実行する操作」と明示 ③判断候補を短く ④レポートはローカルパス表示だけでなく代替を検討 ⑤次フェーズでボタン/スレッド返信を検討

- ①〜④は **妥当**。特に①②④はMVP-1としてそのまま採用してよい。
- ⑤については **更新が必要**。ボタン/スレッドは「次フェーズの新規開発」ではなく、ボタンは **既に実装済みで“有効化”フェーズ**。スレッド返信(C案)は既存ボタンと機能が重複するため、新規実装する価値は低い。
- したがって「いきなりボタンまで行かない」は判断としては保守的で安全だが、「ボタンは大変だから後回し」という理由付けは事実と異なる、という点だけ補正したい。

---

## 8. 今回やらないこと（明確化）

- 自動売買・注文数量計算・証券会社／楽天証券API連携・NISA操作 … **一切実装しない**（プロジェクト方針）。
- ボタンやコマンドの文言で「買う／売る／購入／注文」を使わない。「確認」「見送り」「再レビュー」「レビュー候補」に限定。
- 投資助言の断定（「買え」「上がる」）はしない。
- Slack Webhook URL / Bot Token を本文・ログに出さない（既存実装は除去済み＝`src/slack_actions.py`）。
- 判断履歴を外部SaaSへ持ち出さない（E案を不採用とした理由）。

---

## 関連

- 文面案 → `docs/slack_message_templates.md`
- 実装方針 → `docs/slack_mobile_mvp_plan.md`
