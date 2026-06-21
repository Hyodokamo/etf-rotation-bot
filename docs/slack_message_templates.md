# Slack 通知文面テンプレート（スマホ向け）

対象: ETF Rotation Bot 月次レビュー通知
作成日: 2026-06-21
方針: 最初の3行で「結論・急ぎ度・やること」が分かる。詳細は後方。断定・売買表現を使わない。

---

## 設計原則

1. **1行目 = 結論**（状態アイコン＋一文）。「今月どうすべきか」を最初に出す。
2. **2〜3行目 = 推奨アクションと急ぎ度**。「急ぎ対応なし／要確認／要対応」を明示。
3. Top配分は **1行コンパクト**（`·` 区切り）。スマホ折返しを最小化。
4. リスク・指標・委員会などの詳細は **結論の後ろ**。情報は消さず順序を変える。
5. **自動売買しないこと** を毎回1行で明記。
6. レポートは **スマホで開けない前提**。要約で判断が成立すること。
7. 判断記録は「**スマホは見るだけ／記録は後でPC・NASで**」を明示し、CLIは最小限。
8. 状態語の意味を併記（PASS=制約クリア 等）。専門用語に依存しない。

絵文字凡例: ✅ 良好 / 🔶 要確認 / ❌ 要対応 / 🛡️ 安全注記 / 📄 レポート / 📝 記録方法

---

## パターン1: PASS（制約クリア・通常運用）

```text
✅ ETF Rotation Bot 月次レビュー（2026-06）
今月の結論: 制約クリア。確認のみでOK、急ぎの対応はありません。
推奨アクション: 内容を確認 →「確認済み」を記録（時間のある時にPC/NASで）

モード: ✅ リスクオン ／ ターンオーバー 20.0%（上限20%）
Top配分: BND 32.1% · VTV 26.8% · VOO 23.4% · QQQM 17.7%
Pre-Trade Gate: ✅ PASS（売買前チェックの制約はすべてクリア）

主な留意点: 高バリュエーション局面のため過度な追随は避ける（参考）。
🛡️ これは推奨であり最終判断は人間が行います。自動売買・注文・数量計算はしません。

📄 詳細レポート: outputs/report_2026-06-20.md（スマホで開けない場合は本要約で判断可）
📝 判断を記録（後でPC/NASで実行・1つ選ぶ）:
   確認済み →  python main.py --record-decision REVIEW_CONFIRMED
   今月見送り → python main.py --record-decision SKIP_THIS_MONTH
```

---

## パターン2: REVIEW_REQUIRED（注意あり・要確認）

```text
🔶 ETF Rotation Bot 月次レビュー（2026-06）
今月の結論: 注意ポイントあり。記録の前に内容確認をおすすめします。
推奨アクション: 下記の注意点を確認 → 確認済み / 見送り / 再レビュー のいずれかを記録

モード: ✅ リスクオン ／ ターンオーバー 18.0%（上限20%）
Top配分: BND 30.5% · VTV 25.0% · VOO 24.0% · QQQM 12.0% · SGOV 8.5%
Pre-Trade Gate: 🔶 REVIEW_REQUIRED（自動で弾く違反ではないが要確認の項目あり）
要確認: 防御資産比率が高め（リスクオンだが守りが厚い構成）

🛡️ これは推奨であり最終判断は人間が行います。自動売買・注文・数量計算はしません。

📄 詳細レポート: outputs/report_2026-06-20.md（注意点の根拠はレポートに記載）
📝 判断を記録（後でPC/NASで・1つ選ぶ）:
   確認した上で採用 → python main.py --record-decision REVIEW_CONFIRMED
   今月は見送り    → python main.py --record-decision SKIP_THIS_MONTH
   設定見直して再実行 → python main.py --record-decision REQUEST_RERUN
```

---

## パターン3: FAIL（制約抵触・要対応）

```text
❌ ETF Rotation Bot 月次レビュー（2026-06）
今月の結論: 売買前チェックの制約に抵触。このままの採用は推奨しません。
推奨アクション: 見送り、または設定を見直して再レビュー（採用するなら理由のメモが必須）

モード: ⚠️ リスクオフ ／ ターンオーバー 24.0%（上限20%）
Top配分: SGOV 40.0% · BND 28.0% · GLDM 18.0% · VTV 14.0%
Pre-Trade Gate: ❌ FAIL
抵触している制約:
  - ターンオーバーが上限20%を超過（24.0%）
  - 単一資産の上限超過（SGOV 40.0%）

🛡️ これは推奨であり最終判断は人間が行います。自動売買・注文・数量計算はしません。
   FAIL でも自動で何かが実行されることはありません。

📄 詳細レポート: outputs/report_2026-06-20.md（抵触の詳細を確認）
📝 判断を記録（後でPC/NASで・1つ選ぶ。FAIL採用はコメント必須）:
   今月は見送り      → python main.py --record-decision SKIP_THIS_MONTH
   設定見直して再実行  → python main.py --record-decision REQUEST_RERUN
   理由を理解して採用  → python main.py --record-decision MANUAL_OVERRIDE --decision-comment "理由"
```

---

## ボタン版（MVP-2有効化時の併記イメージ）

Bot Token + Socket Mode 常駐が有効な場合、本文末尾の「📝 判断を記録」ブロックの代わりに既存ボタン（`src/slack_actions.py MONTHLY_BUTTONS`）が付く。文面は同じ結論ブロックを使い、最後だけ差し替える。

```text
（…結論・配分・リスク・自動売買注記は上と同じ…）

📝 判断はこのメッセージのボタンから記録できます（押下＝判断の記録。売買承認ではありません）。
 [ 確認済み ] [ 今月は見送り ] [ 再レビュー ] [ メモ追加 ]
```

- ボタン押下は `logs/slack_decision_log.jsonl` に追記される（既存実装）。
- 常駐リスナーが停止している場合に備え、CLI記録の手順は別途ドキュメント化しておく（フォールバック）。

---

## 既存通知からの改善点（対比）

| 観点 | 現状 | 改善後 |
|---|---|---|
| 1行目 | タイトルのみ | 状態アイコン＋「今月の結論」一文 |
| 急ぎ度 | 不明 | 「急ぎ対応なし／要確認／要対応」を明示 |
| 状態語の意味 | アイコンのみ（PASS/FAIL の意味なし） | 「PASS=制約クリア」等を併記 |
| Top配分 | 縦並び（4〜5行） | `·` 区切りの1行 |
| 情報の順序 | 指標・委員会が前、結論が後 | 結論が先、詳細は後 |
| CLI案内 | 「以下のCLIを実行」とだけ（押せない） | 「後でPC/NASで実行」と役割明示・最小行数 |
| レポート | ローカルパスのみ（スマホ不可） | 「スマホで開けない場合あり・要約で判断可」と明記 |
| 自動売買注記 | 末尾に埋もれる | 🛡️ 行で結論直後に固定表示 |
| FAIL時 | PASS時と同じトーン | 「このままの採用は非推奨」と明確化＋採用はコメント必須を明示 |

---

## 文言ガイド（NG/OK）

- NG: 「買い」「売り」「購入」「注文」「利確」「損切りせよ」 → OK: 「確認」「見送り」「再レビュー」「レビュー候補」「採用（=判断の記録）」
- NG: 「上がる」「儲かる」「今が買い時」 → OK: 「モデル上は〜の配分」「参考」「留意点」
- NG: 数量・株数・金額の指示 → そもそも出さない（注文数量は計算しない）
- 状態語は必ず意味を1語添える（PASS=制約クリア / FAIL=制約抵触 / REVIEW_REQUIRED=要確認）。

---

## 実出力サンプル（MVP-1実装後）

`build_slack_summary()` の実際の出力（2026-06-21 時点、`report_path=outputs/report_2026-06-20.md`）。テンプレートとの差は、内部実装の表示順（モード→ターンオーバー→Top配分→Gate）に合わせている点のみ。

### PASS

```text
✅ ETF Rotation Bot 月次レビュー（2026-06）
今月の結論: 制約クリア。確認のみでよく、急ぎの対応はありません。
推奨アクション: 内容を確認し、時間のある時に「確認済み」を記録してください。
🛡️ 自動売買は行いません。売買数量の計算もしません。最終判断は人間が行います。
モード: ✅ リスクオン
ターンオーバー: 20.0%
Top配分: BND 32.1% · VTV 26.8% · VOO 23.4% · QQQM 17.7%
Pre-Trade Gate: ✅ PASS
📄 詳細レポート: outputs/report_2026-06-20.md（スマホで開けない場合あり／本要約で判断可）

*月次レビュー判断*
✅ Pre-Trade Gate: PASS
自動売買は行いません。最終判断は人間が行います。
📝 スマホでは内容の確認のみ。判断の記録は後でPC/NASで次のいずれかを実行:
  確認済み → python main.py --record-decision REVIEW_CONFIRMED
  見送り → python main.py --record-decision SKIP_THIS_MONTH
  再レビュー → python main.py --record-decision REQUEST_RERUN
```

### REVIEW_REQUIRED

```text
🔶 ETF Rotation Bot 月次レビュー（2026-06）
今月の結論: 注意ポイントあり。記録の前に内容の確認をおすすめします（緊急ではありません）。
推奨アクション: 注意点を確認し、確認済み／見送り／再レビューのいずれかを記録してください。
🛡️ 自動売買は行いません。売買数量の計算もしません。最終判断は人間が行います。
モード: ✅ リスクオン
ターンオーバー: 18.0%
Top配分: BND 30.5% · VTV 25.0% · VOO 24.0% · QQQM 12.0% · SGOV 8.5%
Pre-Trade Gate: 🔶 REVIEW_REQUIRED
- 防御資産比率が高め（リスクオンだが守りが厚い）
📄 詳細レポート: outputs/report_2026-06-20.md（スマホで開けない場合あり／本要約で判断可）

*月次レビュー判断*
🔶 Pre-Trade Gate: REVIEW_REQUIRED — 注意あり
自動売買は行いません。最終判断は人間が行います。
📝 スマホでは内容の確認のみ。判断の記録は後でPC/NASで次のいずれかを実行:
  確認済み → python main.py --record-decision REVIEW_CONFIRMED
  見送り → python main.py --record-decision SKIP_THIS_MONTH
  再レビュー → python main.py --record-decision REQUEST_RERUN
  理由を理解して採用 → python main.py --record-decision MANUAL_OVERRIDE --decision-comment "理由"
```

### FAIL

```text
❌ ETF Rotation Bot 月次レビュー（2026-06）
今月の結論: 売買前チェックの制約に抵触。このままの採用は推奨しません（要対応）。
推奨アクション: 見送り、または設定を見直して再レビューしてください（採用する場合は理由メモが必須）。
🛡️ 自動売買は行いません。売買数量の計算もしません。最終判断は人間が行います。
モード: ⚠️ リスクオフ
ターンオーバー: 24.0%
Top配分: SGOV 40.0% · BND 28.0% · GLDM 18.0% · VTV 14.0%
Pre-Trade Gate: ❌ FAIL
- ターンオーバーが上限20%を超過（24.0%）
- 単一資産の上限超過（SGOV 40.0%）
📄 詳細レポート: outputs/report_2026-06-20.md（スマホで開けない場合あり／本要約で判断可）

*月次レビュー判断*
❌ Pre-Trade Gate: FAIL — 以下の制約に抵触しています
  - turnover_limit_check
  - single_asset_limit_check
推奨：見送り、または再レビューしてください。
自動売買は行いません。最終判断は人間が行います。
📝 スマホでは内容の確認のみ。判断の記録は後でPC/NASで次のいずれかを実行:（採用する場合はコメント必須）
  見送り → python main.py --record-decision SKIP_THIS_MONTH
  再レビュー → python main.py --record-decision REQUEST_RERUN
  理由を理解して採用 → python main.py --record-decision MANUAL_OVERRIDE --decision-comment "理由"
```

> 注: FAIL本文上部は人間可読メッセージを表示するが、下部「月次レビュー判断」の制約列挙は現状 `check_id` のまま（Backlog: `docs/slack_mobile_mvp_plan.md` 参照）。

---

## 関連

- UX調査 → `docs/slack_mobile_ux_review.md`
- 実装方針 → `docs/slack_mobile_mvp_plan.md`
