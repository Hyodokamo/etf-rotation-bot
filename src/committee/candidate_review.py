"""Phase 3.5: Candidate Review.

Reviews *new-buy / add* candidates (e.g. GRID / BOTZ / ARKQ) with the Investment
Committee, separately from the monthly portfolio review. Advisory-only:

- `allocation_override` is always False; the monthly `final_allocation` is never touched.
- No order quantity is computed; `intended_amount_jpy` is treated only as the user's
  *consideration amount*, never converted to shares.
- No auto-trading / brokerage integration. Never asserts "買え"/"売れ".
- Stricter than the monthly review (a committee PASS yields only a small test buy).

Run explicitly via the `--candidate-review` CLI path; the normal monthly run does
NOT trigger it.
"""
from __future__ import annotations

import csv
import uuid
from datetime import date
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError, field_validator

from src.committee.models import CommitteeConfig, CommitteeVerdict, _SEVERITY
from src.committee.runner import run_committee
from src.etf_master import EtfMasterEntry, build_enrichment, get_entry
from src.llm.base import BaseLlmClient
from src.logger import logger
from src.portfolio_context import (
    PortfolioContext,
    build_committee_read_only_instruction,
    build_portfolio_context_markdown,
    build_slack_context_line,
    detect_core_overlap,
    to_committee_context,
)

REQUIRED_COLUMNS = [
    "symbol", "name", "asset_type", "theme", "candidate_action",
    "intended_amount_jpy", "account", "reason", "time_horizon", "notes",
]

MANDATORY_THEME_CHECKS = [
    "既存ポートフォリオとの重複",
    "テーマ集中度",
    "価格トレンド（200日線・相対強度）",
    "高値掴みリスク",
    "長期保有に耐える投資仮説",
    "見直し条件の明確さ",
    "反証条件の明確さ",
]


class CandidateAction(str, Enum):
    NEW_BUY = "NEW_BUY"
    ADD = "ADD"
    HOLD_REVIEW = "HOLD_REVIEW"
    TRIM_REVIEW = "TRIM_REVIEW"
    EXIT_REVIEW = "EXIT_REVIEW"


class CandidateVerdict(str, Enum):
    APPROVE_FOR_WATCHLIST = "APPROVE_FOR_WATCHLIST"
    APPROVE_SMALL_TEST_BUY = "APPROVE_SMALL_TEST_BUY"
    WAIT_FOR_BETTER_ENTRY = "WAIT_FOR_BETTER_ENTRY"
    REJECT_FOR_NOW = "REJECT_FOR_NOW"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


# Commitment ordering (least -> most committed) for strictness adjustments.
_VERDICT_ORDER = [
    CandidateVerdict.REJECT_FOR_NOW,
    CandidateVerdict.WAIT_FOR_BETTER_ENTRY,
    CandidateVerdict.APPROVE_FOR_WATCHLIST,
    CandidateVerdict.APPROVE_SMALL_TEST_BUY,
]


class Candidate(BaseModel):
    symbol: str
    name: str = ""
    asset_type: str = ""
    theme: str = ""
    candidate_action: CandidateAction
    intended_amount_jpy: float | None = None
    account: str = ""
    reason: str = ""
    time_horizon: str = ""
    notes: str = ""

    @field_validator("intended_amount_jpy", mode="before")
    @classmethod
    def _parse_amount(cls, v):
        if v is None or v == "":
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None


class CandidateReviewResult(BaseModel):
    review_id: str
    review_date: str
    candidate: dict
    candidate_verdict: CandidateVerdict
    confidence: float = 0.0
    committee_summary: str = ""
    member_outputs: list[dict] = Field(default_factory=list)
    strongest_buy_thesis: str = ""
    strongest_rejection_thesis: str = ""
    key_risks: list[str] = Field(default_factory=list)
    required_checks: list[str] = Field(default_factory=list)
    entry_conditions: list[str] = Field(default_factory=list)
    invalidation_conditions: list[str] = Field(default_factory=list)
    sizing_note: str = ""
    final_advisory: str = ""
    allocation_override: bool = False
    # Phase 5.0: ETF master enrichment (read-only).
    etf_master_known: bool = True
    universe_status: str = ""
    review_role: str = ""
    review_frequency: str = ""
    role_in_ai_sleeve: str = ""
    expected_overlap_with_core: str = ""
    nisa_usage_policy: str = ""
    preferred_account: str = ""
    needs_order_screen_check: bool = False
    data_quality_status: str = ""
    data_quality_warning: str = ""
    agent_affinity: list[str] = Field(default_factory=list)
    agent_concern: list[str] = Field(default_factory=list)

    @field_validator("allocation_override")
    @classmethod
    def _force_no_override(cls, v: bool) -> bool:
        return False

    def to_dict(self) -> dict:
        return self.model_dump(mode="json")


_FINAL_ADVISORY = {
    CandidateVerdict.APPROVE_SMALL_TEST_BUY: (
        "小口のテスト買いは許容範囲。ただし注文数量は本レビューでは出さず、エントリー条件成立後に人間が判断する。"
    ),
    CandidateVerdict.APPROVE_FOR_WATCHLIST: (
        "ウォッチリスト入りを助言。本格的な買付は推奨せず、エントリー条件の成立を待つ。"
    ),
    CandidateVerdict.WAIT_FOR_BETTER_ENTRY: (
        "より良いエントリーを待つことを助言。現時点での新規買付は控える。"
    ),
    CandidateVerdict.REJECT_FOR_NOW: "現時点では新規採用を見送ることを助言。",
    CandidateVerdict.INSUFFICIENT_DATA: (
        "情報不足のため判断保留。価格トレンド等のデータを補ってから再レビューする。"
    ),
}


# ── CSV loading ──────────────────────────────────────────────────────────────


def load_watchlist(path: str | Path) -> list[Candidate]:
    """Load and validate the watchlist CSV. Raises ValueError on schema problems."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Watchlist CSV not found: {p}")
    with open(p, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        header = reader.fieldnames or []
        missing = [c for c in REQUIRED_COLUMNS if c not in header]
        if missing:
            raise ValueError(f"watchlist CSV is missing required columns: {missing}")
        candidates: list[Candidate] = []
        for i, row in enumerate(reader, 1):
            try:
                candidates.append(Candidate.model_validate(row))
            except ValidationError as e:
                raise ValueError(f"invalid candidate row {i} ({row.get('symbol')}): {e}") from e
    return candidates


def filter_candidates(candidates: list[Candidate], symbol: str | None) -> list[Candidate]:
    if not symbol:
        return candidates
    sym = symbol.strip().upper()
    return [c for c in candidates if c.symbol.strip().upper() == sym]


# ── context ──────────────────────────────────────────────────────────────────

_THEME_CATEGORIES = {"theme_equity", "growth_equity", "sector"}


def build_candidate_context(
    candidate: Candidate,
    portfolio_holdings: dict[str, float] | None = None,
    universe_categories: dict[str, str] | None = None,
    price_trend: dict | None = None,
    portfolio_context: PortfolioContext | None = None,
    etf_master: dict[str, EtfMasterEntry] | None = None,
) -> dict:
    """Build the committee context for a single candidate (no allocation change)."""
    holdings = portfolio_holdings or {}
    cats = universe_categories or {}
    held_categories = sorted({cats.get(t, "unknown") for t in holdings})
    symbol_in_portfolio = candidate.symbol in holdings
    theme_like = candidate.asset_type in ("theme_etf", "active_etf") or bool(candidate.theme)
    overlap_suspected = symbol_in_portfolio or (
        theme_like and any(c in _THEME_CATEGORIES for c in held_categories)
    )

    # Phase 5.0.5: read-only core overlap (intentional concentration audit).
    core_overlap = None
    if portfolio_context is not None:
        warning = detect_core_overlap(candidate.theme, portfolio_context, candidate.symbol)
        if warning is not None:
            core_overlap = warning.model_dump(mode="json")

    instruction = (
        "これは新規/追加候補の買付可否レビューです。月次運用レビューより厳しく審査してください。"
        "テーマ集中・既存ポートフォリオとの重複・価格トレンド・高値掴みリスク・長期保有に耐える仮説・"
        "見直し条件・反証条件を必ず評価してください。配分変更や注文数量は扱いません。"
        "intended_amount_jpy は検討額であり、株数には変換しません。"
    )
    if portfolio_context is not None:
        instruction += " " + build_committee_read_only_instruction(portfolio_context)

    # Phase 5.0: ETF master enrichment (hints only; never a filter).
    enrichment = None
    if etf_master is not None:
        enrichment = build_enrichment(get_entry(etf_master, candidate.symbol))
        instruction += (
            " agent_affinity/agent_concern は評価対象の制限ではなく賛成/反対論点生成の補助です。"
            "全メンバーが全ETFを独立に評価してください。"
            "data_quality_status が needs_*/insufficient の場合、AI Auditor(core_ai_auditor) は"
            "データ品質警告を出してください。"
        )

    ctx: dict = {
        "review_type": "candidate_new_buy_review",
        "instruction": instruction,
        "candidate": {
            "symbol": candidate.symbol,
            "name": candidate.name,
            "asset_type": candidate.asset_type,
            "theme": candidate.theme,
            "candidate_action": candidate.candidate_action.value,
            "intended_amount_jpy_consideration_only": candidate.intended_amount_jpy,
            "account": candidate.account,
            "reason": candidate.reason,
            "time_horizon": candidate.time_horizon,
            "notes": candidate.notes,
        },
        "current_portfolio": {t: round(w, 4) for t, w in holdings.items()},
        "portfolio_overlap": {
            "symbol_in_portfolio": symbol_in_portfolio,
            "existing_holding_categories": held_categories,
            "overlap_suspected": overlap_suspected,
        },
        "price_trend": price_trend,
    }
    if portfolio_context is not None:
        ctx["core_portfolio_context"] = to_committee_context(portfolio_context)
        if core_overlap is not None:
            ctx["core_overlap_warning"] = core_overlap
    if enrichment is not None:
        ctx["etf_master_metadata"] = enrichment
    return ctx


# ── verdict mapping (stricter than monthly) ──────────────────────────────────


def map_candidate_verdict(
    final_verdict: CommitteeVerdict,
    auditor_verdict: CommitteeVerdict | None,
    has_trend: bool,
) -> CandidateVerdict:
    if final_verdict == CommitteeVerdict.INSUFFICIENT_DATA:
        return CandidateVerdict.INSUFFICIENT_DATA
    if auditor_verdict == CommitteeVerdict.REJECT:
        # process/data unreliable -> cannot approve a buy
        return CandidateVerdict.INSUFFICIENT_DATA

    base = {
        CommitteeVerdict.PASS: CandidateVerdict.APPROVE_SMALL_TEST_BUY,
        CommitteeVerdict.PASS_WITH_CAUTION: CandidateVerdict.APPROVE_FOR_WATCHLIST,
        CommitteeVerdict.WATCH: CandidateVerdict.WAIT_FOR_BETTER_ENTRY,
        CommitteeVerdict.REJECT: CandidateVerdict.REJECT_FOR_NOW,
    }[final_verdict]

    # AI auditor WATCH downgrades one notch toward caution.
    if auditor_verdict == CommitteeVerdict.WATCH:
        idx = max(0, _VERDICT_ORDER.index(base) - 1)
        base = _VERDICT_ORDER[idx]

    # Without confirmed price trend, do not green-light an actual test buy.
    if not has_trend and base == CandidateVerdict.APPROVE_SMALL_TEST_BUY:
        base = CandidateVerdict.APPROVE_FOR_WATCHLIST

    return base


# ── thesis / risk extraction ─────────────────────────────────────────────────


def _pick_buy_thesis(members) -> str:
    supportive = sorted(members, key=lambda m: (_SEVERITY[m.verdict], -m.confidence))
    for m in supportive:
        if m.strongest_support:
            return f"{m.member_id}: {m.strongest_support}"
    return ""


def _pick_rejection_thesis(members) -> str:
    opposed = sorted(members, key=lambda m: (-_SEVERITY[m.verdict], -m.confidence))
    for m in opposed:
        if m.strongest_objection:
            return f"{m.member_id}: {m.strongest_objection}"
        if m.dissenting_view:
            return f"{m.member_id}: {m.dissenting_view}"
    return ""


def _dedup(seq, cap=None):
    out: list[str] = []
    for x in seq:
        if x and x not in out:
            out.append(x)
    return out[:cap] if cap else out


# ── review ───────────────────────────────────────────────────────────────────


def review_candidate(
    candidate: Candidate,
    committee_cfg: CommitteeConfig,
    client: BaseLlmClient | None,
    *,
    portfolio_holdings: dict[str, float] | None = None,
    universe_categories: dict[str, str] | None = None,
    price_trend: dict | None = None,
    portfolio_context: PortfolioContext | None = None,
    etf_master: dict[str, EtfMasterEntry] | None = None,
    review_date: str | None = None,
    ai_audit_status: str | None = None,
) -> CandidateReviewResult:
    """Run the committee on one candidate and assemble an advisory-only result."""
    review_date = review_date or date.today().isoformat()
    context = build_candidate_context(
        candidate, portfolio_holdings, universe_categories, price_trend,
        portfolio_context=portfolio_context, etf_master=etf_master,
    )
    overlap = context["portfolio_overlap"]
    core_overlap = context.get("core_overlap_warning")
    enrichment = context.get("etf_master_metadata")

    committee_result = run_committee(context, committee_cfg, client, ai_audit_status=ai_audit_status)
    members = committee_result.members

    # Enforce dissenting_view / next_review_triggers presence for each member.
    for m in members:
        if not m.dissenting_view:
            m.dissenting_view = "(明示的な反対意見なし。次回レビューで再評価)"
        if not m.next_review_triggers:
            m.next_review_triggers = list(m.required_checks) or ["次回レビューで再評価"]

    auditor = next((m.verdict for m in members if m.member_id == "core_ai_auditor"), None)
    has_trend = price_trend is not None
    verdict = map_candidate_verdict(committee_result.final_committee_verdict, auditor, has_trend)

    # risks: member risks + overlap risk when suspected
    risks = _dedup([r for m in members for r in m.key_risks])
    if overlap["overlap_suspected"]:
        detail = "同一銘柄を保有中" if overlap["symbol_in_portfolio"] else \
            f"既存テーマ/成長株と重複の可能性（{', '.join(overlap['existing_holding_categories'])}）"
        risks.insert(0, f"既存ポートフォリオとの重複: {detail}")
    # Phase 5.0.5: read-only core overlap (intentional concentration audit).
    if core_overlap and core_overlap.get("overlaps_with"):
        risks.insert(0, f"コア資産との重複注意: {core_overlap.get('reason')}")
    # Phase 5.0: ETF master data-quality warning (AI Auditor concern).
    data_quality_warning = ""
    if enrichment and enrichment.get("data_quality_needs_warning"):
        data_quality_warning = enrichment.get("data_quality_note", "")
        risks.insert(0, f"データ品質警告(AI Auditor): {data_quality_warning}")
    risks = risks[:8]

    extra_checks = []
    if enrichment and enrichment.get("data_quality_needs_warning"):
        extra_checks.append(
            f"データ品質の裏取り（data_quality_status={enrichment.get('data_quality_status')}）")
    if enrichment and enrichment.get("needs_order_screen_check"):
        extra_checks.append("発注前のブローカー取扱い・最新費用の注文画面確認")
    required_checks = _dedup(
        MANDATORY_THEME_CHECKS + extra_checks
        + [c for m in members for c in m.required_checks], cap=12
    )

    sym = candidate.symbol
    entry_conditions = _dedup([
        f"{sym} が200日移動平均線を回復・維持していること",
        "直近高値からの過熱が解消し、押し目で入れること",
        "出来高を伴う安定したトレンドであること",
    ] + [t for m in members for t in m.next_review_triggers], cap=6)

    invalidation_conditions = _dedup([
        f"{sym} が200日移動平均線を明確に割り込む",
        "テーマの主要構成銘柄のトレンドが総崩れになる",
        "長期保有を支える事業/テーマの前提が崩れる",
    ] + [f"{m.member_id}: {m.dissenting_view}" for m in members if m.dissenting_view], cap=6)

    amount = candidate.intended_amount_jpy
    if amount:
        sizing_note = (
            f"検討額 {int(amount):,}円 はユーザーの検討額であり、注文数量・株数には変換しません。"
            "採用時も初回は小口（テスト買い）に留めることを助言します。"
        )
    else:
        sizing_note = "検討額の指定なし。注文数量は算出しません。"

    confidence = round(sum(m.confidence for m in members) / len(members), 3) if members else 0.0

    return CandidateReviewResult(
        review_id=uuid.uuid4().hex,
        review_date=review_date,
        candidate=context["candidate"],
        candidate_verdict=verdict,
        confidence=confidence,
        committee_summary=committee_result.summary,
        member_outputs=[m.model_dump(mode="json") for m in members],
        strongest_buy_thesis=_pick_buy_thesis(members),
        strongest_rejection_thesis=_pick_rejection_thesis(members),
        key_risks=risks,
        required_checks=required_checks,
        entry_conditions=entry_conditions,
        invalidation_conditions=invalidation_conditions,
        sizing_note=sizing_note,
        final_advisory=_FINAL_ADVISORY[verdict],
        allocation_override=False,
        etf_master_known=bool(enrichment.get("known_in_master")) if enrichment else True,
        universe_status=enrichment.get("universe_status", "") if enrichment else "",
        review_role=enrichment.get("review_role", "") if enrichment else "",
        review_frequency=enrichment.get("review_frequency", "") if enrichment else "",
        role_in_ai_sleeve=enrichment.get("role_in_ai_sleeve", "") if enrichment else "",
        expected_overlap_with_core=enrichment.get("expected_overlap_with_core", "") if enrichment else "",
        nisa_usage_policy=enrichment.get("nisa_usage_policy", "") if enrichment else "",
        preferred_account=enrichment.get("preferred_account", "") if enrichment else "",
        needs_order_screen_check=bool(enrichment.get("needs_order_screen_check")) if enrichment else False,
        data_quality_status=enrichment.get("data_quality_status", "") if enrichment else "",
        data_quality_warning=data_quality_warning,
        agent_affinity=enrichment.get("agent_affinity_hint", []) if enrichment else [],
        agent_concern=enrichment.get("agent_concern_hint", []) if enrichment else [],
    )


def review_watchlist(
    candidates: list[Candidate],
    committee_cfg: CommitteeConfig,
    client: BaseLlmClient | None,
    *,
    portfolio_holdings: dict[str, float] | None = None,
    universe_categories: dict[str, str] | None = None,
    price_trends: dict[str, dict] | None = None,
    portfolio_context: PortfolioContext | None = None,
    etf_master: dict[str, EtfMasterEntry] | None = None,
    review_date: str | None = None,
    ai_audit_status: str | None = None,
) -> list[CandidateReviewResult]:
    price_trends = price_trends or {}
    results: list[CandidateReviewResult] = []
    for c in candidates:
        results.append(review_candidate(
            c, committee_cfg, client,
            portfolio_holdings=portfolio_holdings,
            universe_categories=universe_categories,
            price_trend=price_trends.get(c.symbol),
            portfolio_context=portfolio_context,
            etf_master=etf_master,
            review_date=review_date,
            ai_audit_status=ai_audit_status,
        ))
    return results


# ── optional price trend (CLI path only; network) ────────────────────────────


def fetch_candidate_trends(symbols: list[str], years: int = 2) -> dict[str, dict]:
    """Best-effort price-trend metrics per symbol. Network; failures -> omitted."""
    trends: dict[str, dict] = {}
    if not symbols:
        return trends
    try:
        from src.data_fetcher import fetch_prices
        prices = fetch_prices(symbols, years=years)
    except Exception as e:  # pragma: no cover - network/runtime guard
        logger.warning(f"Candidate trend fetch failed: {type(e).__name__}: {e}")
        return trends
    for sym in symbols:
        try:
            if sym not in prices.columns:
                continue
            s = prices[sym].dropna()
            if len(s) < 130:
                continue
            last = float(s.iloc[-1])
            dma200 = float(s.tail(200).mean())
            ret_3m = float(s.iloc[-1] / s.iloc[-63] - 1) if len(s) >= 63 else None
            ret_6m = float(s.iloc[-1] / s.iloc[-126] - 1) if len(s) >= 126 else None
            high = float(s.tail(252).max())
            trends[sym] = {
                "last": round(last, 2),
                "above_200dma": last >= dma200,
                "pct_vs_200dma": round(last / dma200 - 1, 4),
                "ret_3m": round(ret_3m, 4) if ret_3m is not None else None,
                "ret_6m": round(ret_6m, 4) if ret_6m is not None else None,
                "drawdown_from_52w_high": round(last / high - 1, 4),
            }
        except Exception:  # pragma: no cover
            continue
    return trends


# ── formatters ────────────────────────────────────────────────────────────────

_VERDICT_ICON = {
    CandidateVerdict.APPROVE_SMALL_TEST_BUY: "✅",
    CandidateVerdict.APPROVE_FOR_WATCHLIST: "🟢",
    CandidateVerdict.WAIT_FOR_BETTER_ENTRY: "👀",
    CandidateVerdict.REJECT_FOR_NOW: "❌",
    CandidateVerdict.INSUFFICIENT_DATA: "❓",
}


def build_candidate_markdown(
    results: list[CandidateReviewResult],
    review_date: str | None = None,
    portfolio_context: PortfolioContext | None = None,
) -> str:
    review_date = review_date or date.today().isoformat()
    lines: list[str] = []
    lines.append("# Candidate Review（新規買付候補レビュー・助言専用）")
    lines.append(f"**レビュー日:** {review_date}")
    lines.append("")
    lines.append(
        "> 🛡️ 助言専用：配分変更・注文数量計算・自動売買・証券口座連携は行いません。"
        "intended_amount_jpy は検討額であり株数には変換しません。月次運用レビューより厳しく審査しています。"
    )
    lines.append("")
    if portfolio_context is not None:
        lines.extend(build_portfolio_context_markdown(portfolio_context))
        lines.append("")
    for r in results:
        c = r.candidate
        icon = _VERDICT_ICON.get(r.candidate_verdict, "")
        lines.append(f"## {c['symbol']} — {c.get('name', '')}")
        lines.append("")
        lines.append(f"- 候補アクション: `{c.get('candidate_action')}` / asset_type: {c.get('asset_type')} / theme: {c.get('theme')}")
        lines.append(f"- 検討額（注文数量ではない）: {c.get('intended_amount_jpy_consideration_only')} / 口座: {c.get('account')}")
        lines.append(f"- **candidate_verdict:** {icon} `{r.candidate_verdict.value}`（確信度 {r.confidence:.0%}）")
        lines.append(f"- allocation_override: `{str(r.allocation_override).lower()}`")
        if r.universe_status:
            role = f"{r.review_role}（{r.review_frequency}）" if r.review_role else r.review_frequency
            known = "" if r.etf_master_known else " ⚠️ ETF Master未登録（unknown metadata）"
            lines.append(f"- ユニバース区分: `{r.universe_status}` / {role}{known}")
        if r.role_in_ai_sleeve:
            lines.append(f"- AI検証枠での役割: {r.role_in_ai_sleeve}")
        if r.expected_overlap_with_core:
            lines.append(f"- 既存コアとの重複想定: `{r.expected_overlap_with_core}`")
        if r.agent_affinity:
            lines.append(f"- 賛成論点が出やすいエージェント(参考・評価制限ではない): {', '.join(r.agent_affinity)}")
        if r.agent_concern:
            lines.append(f"- 反対論点が出やすいエージェント(参考): {', '.join(r.agent_concern)}")
        if r.nisa_usage_policy or r.preferred_account:
            lines.append(f"- NISA方針: {r.nisa_usage_policy or '—'} / 推奨口座: {r.preferred_account or '—'}")
        if r.data_quality_status:
            lines.append(f"- data_quality_status: `{r.data_quality_status}`")
        if r.needs_order_screen_check:
            lines.append("")
            lines.append("> ⚠️ **発注前確認要**：発注前に証券会社の注文画面でブローカー取扱い・最新費用等を確認（自動発注は行いません）")
        if r.data_quality_warning:
            lines.append(f"- 🔍 データ品質警告: {r.data_quality_warning}")
        lines.append("")
        lines.append(f"**最も強い買い根拠:** {r.strongest_buy_thesis or '—'}")
        lines.append("")
        lines.append(f"**最も強い反対根拠:** {r.strongest_rejection_thesis or '—'}")
        lines.append("")
        if r.key_risks:
            lines.append("**主なリスク:**")
            for x in r.key_risks:
                lines.append(f"- {x}")
            lines.append("")
        lines.append("**確認すべき項目（required_checks）:**")
        for x in r.required_checks:
            lines.append(f"- {x}")
        lines.append("")
        lines.append("**エントリー条件:**")
        for x in r.entry_conditions:
            lines.append(f"- {x}")
        lines.append("")
        lines.append("**反証・無効化条件（invalidation_conditions）:**")
        for x in r.invalidation_conditions:
            lines.append(f"- {x}")
        lines.append("")
        lines.append(f"**sizing_note:** {r.sizing_note}")
        lines.append("")
        lines.append(f"**最終助言:** {r.final_advisory}")
        lines.append("")
        if r.member_outputs:
            lines.append("**Committee メンバー所見:**")
            lines.append("")
            lines.append("| メンバー | tier | verdict | 確信度 | 最も強い反対点 |")
            lines.append("|---|---|---|---:|---|")
            for m in r.member_outputs:
                lines.append(
                    f"| {m.get('member_id')} | {m.get('tier')} | {m.get('verdict')} "
                    f"| {float(m.get('confidence', 0)):.0%} | {m.get('dissenting_view', '—')} |"
                )
            lines.append("")
        lines.append("---")
        lines.append("")
    return "\n".join(lines)


def save_candidate_report(
    markdown: str,
    review_date: str | None = None,
    out_dir: str | Path = "reports/candidates",
) -> str:
    review_date = review_date or date.today().isoformat()
    stamp = review_date.replace("-", "")
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"candidate_review_{stamp}.md"
    path.write_text(markdown, encoding="utf-8")
    logger.info(f"Candidate review report saved to {path}")
    return str(path)


def build_candidate_slack_summary(
    result: CandidateReviewResult,
    portfolio_context: PortfolioContext | None = None,
) -> str:
    c = result.candidate
    icon = _VERDICT_ICON.get(result.candidate_verdict, "")
    top_risk = result.key_risks[0] if result.key_risks else "—"
    lines = [
        f"*Candidate Review: {c['symbol']}* {icon} {result.candidate_verdict.value}",
        f"買い根拠: {result.strongest_buy_thesis[:120] or '—'}",
        f"主リスク: {top_risk}",
        f"助言: {result.final_advisory}",
    ]
    if result.universe_status:
        known = "" if result.etf_master_known else "（ETF Master未登録）"
        lines.append(f"区分: {result.universe_status}{known} / 役割: {result.role_in_ai_sleeve or '—'}")
    if result.expected_overlap_with_core:
        lines.append(f"コア重複想定: {result.expected_overlap_with_core}")
    if result.agent_affinity or result.agent_concern:
        lines.append(
            f"参考エージェント 賛成寄り: {', '.join(result.agent_affinity) or '—'} / "
            f"反対寄り: {', '.join(result.agent_concern) or '—'}（評価制限ではない）"
        )
    if result.nisa_usage_policy or result.preferred_account:
        lines.append(f"NISA方針: {result.nisa_usage_policy or '—'} / 推奨口座: {result.preferred_account or '—'}")
    if result.needs_order_screen_check:
        lines.append("⚠️ 発注前に証券会社注文画面で確認（ブローカー取扱い・最新費用）")
    if result.data_quality_warning:
        lines.append(f"🔍 データ品質警告: data_quality_status={result.data_quality_status}")
    if portfolio_context is not None:
        lines.extend(build_slack_context_line(portfolio_context))
    lines.append("🛡️ shadow: 助言のみ・配分変更/数量計算なし")
    return "\n".join(lines)
