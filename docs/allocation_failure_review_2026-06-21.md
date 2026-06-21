# 月次ローテーション配分 — Risk-ON債券過大化 調査と改善

対象: 月次ETFローテーションの配分ロジック（Risk-ON時に債券/防御資産が主役化する問題）
作成日: 2026-06-21
種別: 調査 + MVP設計（Signal Review系・月次Slack UXには触れない）

---

## 0. 事象概要

直近の月次レビューで、Risk-ON判定にもかかわらず債券（bond）が合計78.5%を占める配分案が生成され、
Pre-Trade Gate が FAIL で停止した。Gate が止めたこと自体は正しい。問題は **配分生成段階で
明らかに制約違反になりやすい案（Risk-ONなのに債券78.5%）が作られてしまう** こと。

### 実行結果（Before）

```text
モード: リスクオン
ターンオーバー: 20.0%（通常運用、上限20%）
Top配分:
  BND: 37.8%  (bond)
  IEF: 22.5%  (bond)
  VTV: 21.5%  (factor)
  TLT: 18.2%  (bond)
fixed_income(bond) 合計: 78.5%

Pre-Trade Gate: FAIL
- bond weight 78.5% exceeds category limit 40.0%.
- Risk-ON ですが防御資産比率が 78.5% と高くなっています（閾値 75% 超）。
```

---

## 1. なぜ Risk-ON で債券比率78.5%になったか（根本原因）

複数要因の合わせ技。中心は **「スコアの低ボラ優遇」×「最終本数トリムの再正規化」**。

### 原因A（スコア段階）: vol_adjust が低ボラ債券を過大評価
`src/scoring.py`:
```python
if cfg.vol_adjust and "vol_20d" in indicators.columns:
    vol = indicators["vol_20d"]...clip(lower=0.01)
    scores = scores / vol          # ← 低ボラ資産ほどスコアが跳ね上がる
```
`config.yaml: scoring.vol_adjust: true`。債券（BND/IEF/TLT/2561.T）は20日ボラが小さく、
`score/vol` で **スコアが大きく増幅**される。`method: score_proportional` のため、増幅された
スコアがそのまま大きな個別ウェイトになり、債券が上位を占める。

### 原因B（配分生成段階）: カテゴリ上限は効くが「45%」かつ全体ベース
`src/allocation.py: compute_allocation()` は `_apply_category_cap()` を**生成時に適用済み**。
ただし上限は `risk.max_category_weights.bond = 0.45`（=45%、~15資産の全体合計に対して）。
つまり生成直後は債券 ≤45%。ここまでは制約が効いている。

### 原因C（最終本数トリム）: trim が上限を「巻き戻す」 ← 主因
`main.py` の処理順:
```
compute_allocation()      # bond ≤45%（全体）
→ apply_risk_gate()       # Risk-ONでは equity_cap 未発動（no-op）
→ apply_turnover_limit()  # 前回配分へブレンド
→ trim_to_max_assets(4)   # 上位4本だけ残して再正規化 ← ここで再集中
```
`trim_to_max_assets` は**上位4本を選んで再正規化するだけで、カテゴリ上限を再適用しない**。
vol_adjust で債券の個別ウェイトが最大級になっているため、上位4本が「債券3本＋株式1本」になりやすく、
4本を100%に再正規化すると **債券が45%→78.5%へ巻き戻る**。
→ 生成時に効かせた45%上限が、最終本数トリムで無効化されるのが直接原因。

### 原因D（上限値の不一致）: 生成45% / Gate40%
`risk.max_category_weights.bond = 0.45`（生成） と `pre_trade_gate.category_limits.bond = 0.40`（Gate）が**不一致**。
仮にトリム巻き戻しが無くても、生成45%は Gate40% で FAIL になり得る。

### 原因E（Gateは検出のみ）
`pre_trade_gate.run_pre_trade_gate()` は最後に `check_category_limit` で検出してFAILにするだけ。
**生成段階で抑える仕組みは無い**。防御資産の概念は `risk_mode_checks`（警告/レビュー閾値）と
Gate にしか無く、いずれも検出（detective）であって抑制（preventive）ではない。

### turnover との相互作用
`apply_turnover_limit` は前回配分へブレンドするため、前回が債券寄りだと債券比率が残りやすい。
ただし今回の主因は原因C（トリム巻き戻し）。

---

## 2. 配分生成フロー（整理）

| 順 | 関数 | 役割 | 債券への効き |
|---|---|---|---|
| 1 | `scoring.compute_scores` | スコア算出（`vol_adjust` で低ボラ優遇） | **債券を増幅（原因A）** |
| 2 | `asset_role.filter_ranking_scores` | variant 除外（cash_like/fx等） | SGOV/UUP除外 |
| 3 | `allocation.compute_allocation` | 上位選定→weight化→資産cap→**カテゴリcap(45%)**→正規化 | 45%に抑制（原因B） |
| 4 | `risk_gate.apply_risk_gate` | Risk-OFF時のみ株式cap | Risk-ONはno-op |
| 5 | `turnover.apply_turnover_limit` | 前回へブレンド | 残存しやすい |
| 6 | `allocation.trim_to_max_assets` | **上位4本に絞り再正規化** | **45%→78.5%へ巻き戻し（原因C）** |
| 7 | `risk_mode_check` | 防御比率の警告/レビュー（検出のみ） | 78.5%>75%→REVIEW |
| 8 | `pre_trade_gate` | カテゴリ/単一/turnover検出→FAIL | bond>40%→**FAIL** |

→ 6 と 8 の間に「Risk-ONカテゴリ上限へ寄せる調整」が無いのが穴。

---

## 3. Pre-Trade Gate が検出した制約
- `category_limit_check`: bond 78.5% > 40.0% → **FAIL**（`pre_trade_gate.category_limits.bond`）
- `risk_mode_alignment_check`: 防御比率 78.5% > review閾値 75% → REVIEW_REQUIRED（`risk_mode_checks`）

Gate は正しく機能している。本改善でも **Gate は最終防衛線として温存**する。

---

## 4. 根本原因の仮説（要約）
1. `vol_adjust` による低ボラ債券の過大評価（スコア段階）。
2. 生成時カテゴリ上限(45%)が `trim_to_max_assets` の再正規化で巻き戻る（主因）。
3. Risk-ON時に防御/債券を生成段階で抑える preventive な仕組みが無い。
4. 生成上限(45%)とGate上限(40%)の不一致。

---

## 5. 改善案 A / B / C

### 案A: Risk-ON時のカテゴリ上限を「生成後・Gate前」に適用し再配分（採用）
最終配分（trim後）に対し、Risk-ON時の fixed_income / defensive 上限（例40%）へ寄せ、
超過分をスコアの高い許容カテゴリ（株式等）へ再配分。Gateは最終防衛線として温存。
- 実装難易度: 低〜中（独立関数 + main.py 1点配線）
- 既存ロジック影響: 小（scoring/allocation核は不変、trip後に追加適用）
- 1か月以内: ◎
- 解釈性: ◎（「Risk-ONは債券40%上限、超過は株式へ」）
- 副作用: 最終本数が「債券3+株式1」の極端ケースでは再配分先が1本しかなく、上限まで届かず
  FAIL が残ることがある（後述・許容）。

### 案B: vol_adjust の低ボラ過大評価を緩和（不採用＝今回スコープ外）
`score/vol` を弱める（指数化 `score / vol**k`、k<1）/ inverse-vol クリップ / 低ボラ上限。
- 数理的には根本に効くが、**全相場局面のスコア分布・順位を変える**ため影響範囲が広い。
- Risk-OFF時の防御資産選好（望ましい挙動）まで弱める懸念。
- MVPでは見送り、将来の調整候補として記録。

### 案C: Risk-ON時に債券をballast枠化（部分採用＝Aの上限値で表現）
Risk-ON時に bond/defensive を「補助枠（最大N%）」として扱う。
- 案Aの `max_fixed_income_weight` / `max_defensive_weight` がまさにこの思想を config 化したもの。
- TLT等を上位候補から個別除外する案は「決め打ち」が強く、ETF個別ハードコードを避ける方針に反するため不採用。

---

## 6. MVPとして採用する案
**案A**（= 案Cの上限思想を config で表現）を採用。ユーザー希望の方向性に一致:
1. Pre-Trade Gate はそのまま温存。
2. 配分生成後・Gate前に「カテゴリ上限へ寄せる調整」を追加。
3. Risk-ON時 fixed_income / defensive を設定値以下に抑制。
4. 超過分はスコアの高い許容カテゴリへ再配分（per-asset上限・合計100%を維持）。
5. 再配分先が足りず違反が残る場合は **Gate FAIL のままで可**（無理にPASSさせない）。
6. `config.allocation_constraints.enabled` でON/OFF可能。

### 設計の要点
- 適用箇所: `main.py` の `trim_to_max_assets` 直後・`risk_mode_check`/`pre_trade_gate` 前。
- Risk-ON（`risk_off=False`）時のみ適用。Risk-OFFはRisk-ON用制約を**適用しない**。
- fixed_income = {bond, cash_like}、defensive = `risk_mode_checks.defensive_categories`（bond/cash_like/commodity/fx）を既定流用。
- 再配分先 = `exclude_categories` 以外（株式・factor・reit・sector等）。
- per-asset上限（`risk.max_weight_per_asset`）と合計1.0を厳守。容量不足分は防御側に残し honest FAIL。

---

## 7. 今回やらないこと
- vol_adjust の改変（案B）。スコア分布全体への影響が大きく、別タスク。
- `trim_to_max_assets` 本体やカテゴリ対応トリムの新設（既存テスト/turnoverへの波及回避）。
- ETF個別のハードコード除外（TLT除外等）。
- 自動売買 / 注文数量 / 証券口座 / NISA / SELL / 新AIエージェント / ユニバース大改編。
- Signal Review系（スマホMVP）・月次Slack UXの改修。
- Pre-Trade Gate の削除・弱体化、FAILの隠蔽、無理なPASS化。

---

## 8. テスト方針
- Risk-ON×fixed_income超過 → 上限以下に調整され、超過分が許容カテゴリへ再配分される。
- 合計weightが1.0を維持。per-asset上限を超えない。
- Risk-OFF時はRisk-ON制約を適用しない（no-op）。
- 再配分先が無い/不足なら無理に調整せず残置（Gateで判定）。
- `cash_fallback_separated` で cash/fallback の扱いが壊れない。
- Pre-Trade Gate は温存され、調整後も違反が残ればFAIL。
- 既存 Slack MVP-1 / Signal Review スマホMVP / allocation / turnover / pre_trade_gate のテストが緑。

実装後の Before/After とテスト結果は完了報告に記載する。

---

## 9. 実装結果（After）

`src/allocation_constraints.py` を新設し、`main.py` の `trim_to_max_assets` 直後・Gate前に適用。
`config.allocation_constraints.enabled` でON/OFF。Risk-ON時のみ作動。

### 実行サンプル（決定的・同一スコア入力での Before/After）

| ケース | Before bond / Gate | After bond / Gate | 充足 |
|---|---|---|---|
| 再配分余地あり（株式3本が上限未満） | 47.0% / FAIL | **40.0% / PASS** | ✓ |
| 再配分余地なし（債券3本＋株式1本、最大4本） | 78.5% / FAIL | 75.0% / FAIL | ✗（honest） |
| 現行ライブ（2026-06-21, defensive 35.5%） | 35.5% / PASS | 35.5% / PASS（no-op） | — |

- **株式に再配分余地がある通常ケースでは、債券を40%上限へ寄せて Gate PASS に転換**できる（合計1.0維持、per-asset上限25%尊重）。
- 報告された極端ケース（最終4本中3本が債券・株式1本）は、`max_weight_per_asset=0.25`×株式1本のため
  再配分容量が約3.5%しかなく、債券は78.5%→75.0%までしか下げられず **Gate FAIL のまま（honest）**。
  これは仕様どおり（無理にPASSさせない）。

### 残課題（今回スコープ外・将来）
極端ケースを完全に解消するには、後段トリムの構造（最大4本×per-asset25%で株式1本だと債券≥75%が不可避）に踏み込む必要がある。次のいずれかが候補（本MVPでは未実施）:
1. 案B: `vol_adjust` を弱める（`score/vol**k` 等）→ そもそも債券がトリム上位を独占しないようにする。
2. カテゴリ対応トリム（Risk-ON時に最終本数の防御資産を1本までに制限）。
3. `max_portfolio_assets` を増やす（株式の本数を確保）。

### Slack/レポート表示例
```text
配分調整: Risk-ON防御資産上限により fixed_income 47.0%→40.0%／defensive 47.0%→40.0%（許容カテゴリへ再配分）
```
容量不足で一部のみの場合は末尾に「（容量不足で一部のみ・Gateで最終判定）」を付す。

---

## 関連
- 実装: `src/allocation_constraints.py`, `config.yaml: allocation_constraints`, `main.py`（月次のみ）
- 月次Slack UX: `docs/slack_mobile_ux_review.md`（本件では不変更）
