"""Phase 5.0.5: Core Portfolio Read-Only Context / AI Sleeve Separation.

The user's *total* portfolio is intentionally concentrated in long-term core
holdings (S&P500 / FANG+ / NASDAQ100 等). That concentration is a deliberate,
accepted long-term risk — it is **NOT** something the Bot optimizes, rebalances,
or proposes selling. The Bot's actual target is a separate ¥1,000,000
"AI検証枠" (``ai_sleeve``).

This module loads the total-portfolio snapshot and the context policy purely as
**read-only risk context**:

- ``core_manual`` holdings are read-only; never a sell/rebalance target.
- ``ai_sleeve_*`` rows describe the validation sleeve the Bot actually reasons about.
- It surfaces *overlap / concentration / thesis-break* risk so the Committee can
  act as a "偏って勝つためのリスク監査役" (risk auditor) — NOT a mechanical
  "diversify" enforcer.

Hard guarantees (consistent with every prior phase):
- Never changes ``final_allocation`` / weights.
- Never computes order quantity / shares.
- No auto-trading, no brokerage integration.
- The snapshot is reference-only; nothing here triggers a trade.
- Reports surface only aggregates (scope totals / core themes / sleeve budget),
  never raw per-holding personal fields, and never secrets.
"""
from __future__ import annotations

import csv
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator

from src.logger import logger

# ── paths ──────────────────────────────────────────────────────────────────────

DEFAULT_SNAPSHOT_PATH = "data/total_portfolio_snapshot.csv"
DEFAULT_POLICY_PATH = "config/portfolio_context_policy.yaml"
DEFAULT_AI_SLEEVE_STATE_PATH = "data/ai_sleeve_state.csv"

# Canonical scope vocabulary. Unknown scopes (e.g. a user's "satellite_legacy")
# are tolerated and treated as reference-only "other".
SCOPE_CORE_MANUAL = "core_manual"
SCOPE_AI_SLEEVE_CASH = "ai_sleeve_cash"
SCOPE_AI_SLEEVE_INVESTED = "ai_sleeve_invested"
SCOPE_UNKNOWN = "unknown"

# Required columns for the snapshot CSV. Extra columns (security_type, quantity,
# average_cost, current_price, valuation_pnl_jpy, …) are tolerated and ignored.
REQUIRED_SNAPSHOT_COLUMNS = [
    "as_of_date", "scope", "symbol", "name", "asset_type", "theme",
    "market_value_jpy", "account", "management_policy", "notes",
]

# The read-only premise injected into Committee / Candidate prompts. This is the
# core behavioural instruction: the Committee must NOT auto-correct the user's
# intentional concentration.
CORE_READ_ONLY_PREMISE = (
    "ユーザーのコア資産はS&P500 / FANG+ / NASDAQ100等に大きく偏っているが、"
    "これは本人が意図して取っている長期リスクである。"
    "Committeeはこの偏りを自動的に是正・分散しようとしてはいけない。"
    "コア資産(core_manual)は参照専用(read_only)であり、売却・是正提案の主対象にしない。"
    "役割は、偏りのリスク・破綻条件・追加投資時の重複・過信リスクを明示することである。"
    "100万円のAI検証枠(ai_sleeve)では、既存コア資産と同じリスクを"
    "無自覚に積み増していないかを確認する。"
    "今回の判断はAI検証枠に関するものであり、全資産の売却・是正提案ではない。"
)


# ── models ───────────────────────────────────────────────────────────────────


class PortfolioHolding(BaseModel):
    """One row of the total-portfolio snapshot (read-only context)."""

    as_of_date: str = ""
    scope: str = SCOPE_UNKNOWN
    symbol: str = ""
    name: str = ""
    asset_type: str = ""
    theme: str = ""
    market_value_jpy: float = 0.0
    account: str = ""
    management_policy: str = "reference_only"
    notes: str = ""

    @field_validator("market_value_jpy", mode="before")
    @classmethod
    def _parse_value(cls, v):
        if v is None or v == "":
            return 0.0
        if isinstance(v, str):
            v = v.replace(",", "").replace("円", "").strip()
            if v == "":
                return 0.0
        try:
            return float(v)
        except (TypeError, ValueError):
            return 0.0

    @property
    def is_core_manual(self) -> bool:
        return self.scope == SCOPE_CORE_MANUAL

    @property
    def is_read_only(self) -> bool:
        # core_manual is read-only by policy; management_policy may also say so.
        return self.is_core_manual or self.management_policy == "read_only"

    @property
    def is_ai_sleeve(self) -> bool:
        return self.scope in (SCOPE_AI_SLEEVE_CASH, SCOPE_AI_SLEEVE_INVESTED)


class TotalPortfolioPolicy(BaseModel):
    enabled: bool = True
    do_not_optimize_total_portfolio: bool = True
    core_assets_are_read_only: bool = True
    purpose: str = ""


class CoreManualPolicy(BaseModel):
    accepted_concentration: list[str] = Field(default_factory=list)
    review_frequency: str = "quarterly"
    review_purpose: list[str] = Field(default_factory=list)


class AiSleevePolicy(BaseModel):
    enabled: bool = True
    total_budget_jpy: float = 1_000_000.0
    current_cash_jpy: float = 1_000_000.0
    current_invested_jpy: float = 0.0
    max_initial_deployment_jpy: float = 250_000.0
    max_monthly_deployment_jpy: float = 250_000.0
    default_account: str = "taxable"
    nisa_use_policy: str = ""
    purpose: str = ""


class OverlapPolicy(BaseModel):
    warn_if_ai_sleeve_adds_same_risk_as_core: bool = True
    high_overlap_themes: list[str] = Field(default_factory=list)
    preferred_diversifying_themes: list[str] = Field(default_factory=list)


class PortfolioContextPolicy(BaseModel):
    total_portfolio_policy: TotalPortfolioPolicy = Field(default_factory=TotalPortfolioPolicy)
    core_manual_policy: CoreManualPolicy = Field(default_factory=CoreManualPolicy)
    ai_sleeve_policy: AiSleevePolicy = Field(default_factory=AiSleevePolicy)
    overlap_policy: OverlapPolicy = Field(default_factory=OverlapPolicy)


class AiSleeveState(BaseModel):
    """The ¥1,000,000 AI validation sleeve state — budget only, never order qty."""

    total_budget_jpy: float = 0.0
    current_cash_jpy: float = 0.0
    current_invested_jpy: float = 0.0
    invested_ratio: float = 0.0
    cash_ratio: float = 0.0
    max_initial_deployment_jpy: float = 0.0
    max_monthly_deployment_jpy: float = 0.0


class OverlapWarning(BaseModel):
    symbol: str = ""
    candidate_theme: str = ""
    overlaps_with: list[str] = Field(default_factory=list)
    reason: str = ""
    diversifying: bool = False


class PortfolioContext(BaseModel):
    """Read-only risk context: core (read-only) vs AI sleeve (Bot's target)."""

    policy: PortfolioContextPolicy
    holdings: list[PortfolioHolding] = Field(default_factory=list)
    core_manual: list[PortfolioHolding] = Field(default_factory=list)
    ai_sleeve_cash: list[PortfolioHolding] = Field(default_factory=list)
    ai_sleeve_invested: list[PortfolioHolding] = Field(default_factory=list)
    other: list[PortfolioHolding] = Field(default_factory=list)
    ai_sleeve: AiSleeveState = Field(default_factory=AiSleeveState)

    @property
    def existing_related_holdings(self) -> list[PortfolioHolding]:
        """Existing NISA/related holdings (e.g. BOTZ/GRID) — read-only reference.

        These are tracked separately from the freely-traded ¥1M AI sleeve and are
        NEVER auto-summed into the sleeve invested amount.
        """
        return list(self.ai_sleeve_invested)

    @property
    def core_themes(self) -> list[str]:
        seen: list[str] = []
        for h in self.core_manual:
            t = (h.theme or "").strip().lower()
            if t and t != "unknown" and t not in seen:
                seen.append(t)
        return seen

    @property
    def core_total_jpy(self) -> float:
        return sum(h.market_value_jpy for h in self.core_manual)

    @property
    def core_is_read_only(self) -> bool:
        """True iff every core_manual holding is treated as read-only."""
        return bool(self.core_manual) and all(h.is_read_only for h in self.core_manual)

    @property
    def optimizes_total_portfolio(self) -> bool:
        """Always False — the total portfolio is never optimized by the Bot."""
        return not self.policy.total_portfolio_policy.do_not_optimize_total_portfolio


# ── loaders ──────────────────────────────────────────────────────────────────


def load_portfolio_snapshot(path: str | Path = DEFAULT_SNAPSHOT_PATH) -> list[PortfolioHolding]:
    """Load the total-portfolio snapshot CSV.

    Missing file -> ``[]`` (graceful). Extra columns are ignored; rows with a
    bad/empty market_value become 0. A row missing required columns is skipped
    with a warning rather than aborting the whole load.
    """
    p = Path(path)
    if not p.exists():
        logger.info(f"Portfolio snapshot not found (read-only context skipped): {p}")
        return []
    holdings: list[PortfolioHolding] = []
    with open(p, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        header = reader.fieldnames or []
        missing = [c for c in REQUIRED_SNAPSHOT_COLUMNS if c not in header]
        if missing:
            logger.warning(f"portfolio snapshot missing columns {missing}; skipping load")
            return []
        for i, row in enumerate(reader, 1):
            data = {c: row.get(c, "") for c in REQUIRED_SNAPSHOT_COLUMNS}
            try:
                holdings.append(PortfolioHolding.model_validate(data))
            except Exception as e:  # never abort the whole snapshot on one bad row
                logger.warning(f"skipping snapshot row {i} ({row.get('symbol')}): {e}")
    return holdings


def load_portfolio_context_policy(
    path: str | Path = DEFAULT_POLICY_PATH,
) -> PortfolioContextPolicy:
    """Load the context policy YAML. Missing/empty -> safe defaults."""
    p = Path(path)
    if not p.exists():
        logger.info(f"Portfolio context policy not found; using defaults: {p}")
        return PortfolioContextPolicy()
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        logger.warning(f"portfolio context policy parse error; using defaults: {e}")
        return PortfolioContextPolicy()
    return PortfolioContextPolicy.model_validate(data)


# ── budget / state ─────────────────────────────────────────────────────────────


def _num(row: dict, *names: str) -> float | None:
    for n in names:
        v = row.get(n)
        if v is None or str(v).strip() == "":
            continue
        try:
            return float(str(v).replace(",", "").replace("円", "").strip())
        except (TypeError, ValueError):
            continue
    return None


def load_ai_sleeve_state(
    path: str | Path = DEFAULT_AI_SLEEVE_STATE_PATH,
) -> dict | None:
    """Load the AI sleeve state CSV — the source of truth for the ¥1M sleeve.

    Recommended single-row schema (extra columns such as ``sleeve_name`` tolerated)::

        as_of_date,total_budget_jpy,cash_jpy,invested_jpy,default_account,notes

    Legacy ``current_cash_jpy`` / ``current_invested_jpy`` names are also accepted.
    Missing/empty file -> ``None`` (caller falls back to policy + warns). State
    reference only: never used to compute order quantity.
    """
    p = Path(path)
    if not p.exists():
        logger.warning(f"AI sleeve state not found; using policy defaults: {p}")
        return None
    try:
        with open(p, encoding="utf-8-sig", newline="") as f:
            row = next(csv.DictReader(f), None)
    except OSError as e:
        logger.warning(f"AI sleeve state read failed; using policy defaults: {e}")
        return None
    if not row:
        logger.warning(f"AI sleeve state empty; using policy defaults: {p}")
        return None
    out: dict = {}
    tb = _num(row, "total_budget_jpy")
    cash = _num(row, "cash_jpy", "current_cash_jpy")
    inv = _num(row, "invested_jpy", "current_invested_jpy")
    if tb is not None:
        out["total_budget_jpy"] = tb
    if cash is not None:
        out["current_cash_jpy"] = cash
    if inv is not None:
        out["current_invested_jpy"] = inv
    acct = (row.get("default_account") or "").strip()
    if acct:
        out["default_account"] = acct
    return out or None


def compute_ai_sleeve_state(
    holdings: list[PortfolioHolding],
    policy: PortfolioContextPolicy,
    state_override: dict | None = None,
) -> AiSleeveState:
    """Compute the ¥1M AI sleeve budget state (read-only; never order quantity).

    Precedence per field: ``ai_sleeve_state.csv`` override > policy. The snapshot's
    ``ai_sleeve_invested`` rows (e.g. existing NISA BOTZ/GRID) are **NOT** summed
    into the sleeve invested — they are read-only ``existing_related_holdings``.
    ``holdings`` is accepted for signature stability but not used for the budget.
    """
    sleeve = policy.ai_sleeve_policy
    override = state_override or {}
    current_cash = override.get("current_cash_jpy", sleeve.current_cash_jpy)
    current_invested = override.get("current_invested_jpy", sleeve.current_invested_jpy)
    total_budget = override.get("total_budget_jpy", sleeve.total_budget_jpy)

    if total_budget > 0:
        invested_ratio = round(current_invested / total_budget, 4)
        cash_ratio = round(current_cash / total_budget, 4)
    else:
        invested_ratio = 0.0
        cash_ratio = 0.0

    return AiSleeveState(
        total_budget_jpy=total_budget,
        current_cash_jpy=current_cash,
        current_invested_jpy=current_invested,
        invested_ratio=invested_ratio,
        cash_ratio=cash_ratio,
        max_initial_deployment_jpy=sleeve.max_initial_deployment_jpy,
        max_monthly_deployment_jpy=sleeve.max_monthly_deployment_jpy,
    )


def build_portfolio_context(
    holdings: list[PortfolioHolding],
    policy: PortfolioContextPolicy,
    ai_sleeve_state: dict | None = None,
) -> PortfolioContext:
    """Separate core (read-only) from AI sleeve and compute the sleeve state."""
    core = [h for h in holdings if h.scope == SCOPE_CORE_MANUAL]
    cash = [h for h in holdings if h.scope == SCOPE_AI_SLEEVE_CASH]
    invested = [h for h in holdings if h.scope == SCOPE_AI_SLEEVE_INVESTED]
    other = [h for h in holdings if h.scope not in (
        SCOPE_CORE_MANUAL, SCOPE_AI_SLEEVE_CASH, SCOPE_AI_SLEEVE_INVESTED)]
    return PortfolioContext(
        policy=policy,
        holdings=holdings,
        core_manual=core,
        ai_sleeve_cash=cash,
        ai_sleeve_invested=invested,
        other=other,
        ai_sleeve=compute_ai_sleeve_state(holdings, policy, ai_sleeve_state),
    )


def load_portfolio_context(
    snapshot_path: str | Path = DEFAULT_SNAPSHOT_PATH,
    policy_path: str | Path = DEFAULT_POLICY_PATH,
    ai_sleeve_state_path: str | Path | None = DEFAULT_AI_SLEEVE_STATE_PATH,
) -> PortfolioContext:
    """Convenience: load snapshot + policy (+ optional sleeve state) and build."""
    policy = load_portfolio_context_policy(policy_path)
    holdings = load_portfolio_snapshot(snapshot_path)
    state = load_ai_sleeve_state(ai_sleeve_state_path) if ai_sleeve_state_path else None
    return build_portfolio_context(holdings, policy, state)


# ── overlap detection ──────────────────────────────────────────────────────────


def _theme_matches(a: str, b: str) -> bool:
    a, b = (a or "").strip().lower(), (b or "").strip().lower()
    if not a or not b or a == "unknown" or b == "unknown":
        return False
    return a == b or a in b or b in a


def detect_core_overlap(
    candidate_theme: str,
    context: PortfolioContext,
    candidate_symbol: str = "",
) -> OverlapWarning | None:
    """Warn when an AI-sleeve candidate adds the *same* risk as the core.

    The point is NOT to force diversification, but to surface that the user may
    be unconsciously doubling down on their existing concentration.
    """
    if not context.policy.overlap_policy.warn_if_ai_sleeve_adds_same_risk_as_core:
        return None
    theme = (candidate_theme or "").strip().lower()
    if not theme or theme == "unknown":
        return None

    high = context.policy.overlap_policy.high_overlap_themes
    diversifying = context.policy.overlap_policy.preferred_diversifying_themes

    overlaps: list[str] = []
    # Overlap with the policy's high-overlap (growth/concentration) themes.
    for ht in high:
        if _theme_matches(theme, ht) and ht not in overlaps:
            overlaps.append(ht)
    # Overlap with an actually-held core theme.
    for ct in context.core_themes:
        if _theme_matches(theme, ct) and ct not in overlaps:
            overlaps.append(ct)

    is_diversifying = any(_theme_matches(theme, d) for d in diversifying)

    if not overlaps:
        if is_diversifying:
            return OverlapWarning(
                symbol=candidate_symbol, candidate_theme=candidate_theme,
                overlaps_with=[], diversifying=True,
                reason="既存コア資産と重複しにくい分散的テーマ。意図した偏りに対する分散効果が見込める。",
            )
        return None

    reason = (
        f"候補テーマ『{candidate_theme}』は既存コア資産の偏り（{', '.join(overlaps)}）と"
        "重複しやすい。AI検証枠で同じリスクを無自覚に積み増していないか確認すること。"
    )
    return OverlapWarning(
        symbol=candidate_symbol, candidate_theme=candidate_theme,
        overlaps_with=overlaps, reason=reason, diversifying=False,
    )


# ── prompt / context embedding (aggregates only; no secrets, no per-holding leak) ─


def build_committee_read_only_instruction(context: PortfolioContext | None = None) -> str:
    """The read-only premise text for Committee / Candidate prompts."""
    return CORE_READ_ONLY_PREMISE


def to_committee_context(context: PortfolioContext) -> dict:
    """JSON-friendly aggregate context for embedding into committee prompts.

    Intentionally aggregate-only: scope totals, core themes, sleeve budget,
    explicit do-not list. No per-holding personal values, no order quantity,
    no weights/final_allocation.
    """
    s = context.ai_sleeve
    return {
        "read_only_premise": CORE_READ_ONLY_PREMISE,
        "core_manual": {
            "read_only": True,
            "is_sell_or_rebalance_target": False,
            "accepted_concentration": context.policy.core_manual_policy.accepted_concentration,
            "themes": context.core_themes,
            "holding_count": len(context.core_manual),
        },
        "ai_sleeve": {
            "is_bot_target": True,
            "source_of_truth": "ai_sleeve_state.csv",
            "total_budget_jpy": s.total_budget_jpy,
            "current_cash_jpy": s.current_cash_jpy,
            "current_invested_jpy": s.current_invested_jpy,
            "invested_ratio": s.invested_ratio,
            "cash_ratio": s.cash_ratio,
            "max_initial_deployment_jpy": s.max_initial_deployment_jpy,
            "nisa_use_policy": context.policy.ai_sleeve_policy.nisa_use_policy,
        },
        "existing_related_holdings": {
            "read_only": True,
            "is_sell_or_rebalance_target": False,
            "not_summed_into_ai_sleeve": True,
            "symbols": [h.symbol for h in context.existing_related_holdings],
            "note": (
                "既存NISA保有等の関連保有。read-only参照であり、AI検証枠の投資済み額には"
                "合算しない。売却・是正提案の主対象にしない。"
            ),
        },
        "review_purpose": context.policy.core_manual_policy.review_purpose,
        "do_not": [
            "コア資産の売却・是正提案を出さない",
            "既存関連保有(BOTZ/GRID等)の売却・是正提案を出さない",
            "既存関連保有をAI検証枠の投資済み額に合算しない",
            "コア資産の配分を変更しない",
            "全資産を最適化しない",
            "注文数量を計算しない",
            "自動売買・証券口座連携を行わない",
        ],
    }


def build_slack_context_line(context: PortfolioContext) -> list[str]:
    """Short Slack lines describing the read-only core / AI sleeve scope."""
    s = context.ai_sleeve
    lines = [
        "*🧭 ポートフォリオ文脈（read-only）:*",
        f"• 全資産コア資産: read-only（売却・是正提案の対象外） / コア{len(context.core_manual)}件",
        (f"• AI検証枠(ai_sleeve_state.csv基準): 予算{int(s.total_budget_jpy):,}円 / "
         f"現金{int(s.current_cash_jpy):,}円 / 投資済み{int(s.current_invested_jpy):,}円"),
        f"• 初回投入上限: 約{int(s.max_initial_deployment_jpy):,}円（方針）",
    ]
    related = context.existing_related_holdings
    if related:
        syms = ", ".join(h.symbol for h in related)
        lines.append(f"• 既存関連保有(read-only・AI検証枠に合算しない): {syms}")
    lines.append("• 今回の判断はAI検証枠が対象であり、全資産・既存保有の売却・是正提案ではない")
    return lines


def build_portfolio_context_markdown(context: PortfolioContext) -> list[str]:
    """Markdown lines describing the read-only core / AI sleeve scope (header banner)."""
    s = context.ai_sleeve
    lines = [
        "> 🧭 **ポートフォリオ文脈（read-only）**",
        f"> - 全資産コア資産(core_manual): **read-only**（売却・是正提案の主対象にしません） / コア{len(context.core_manual)}件",
        (f"> - AI検証枠(ai_sleeve・ai_sleeve_state.csv基準): 予算 {int(s.total_budget_jpy):,}円 / "
         f"現金 {int(s.current_cash_jpy):,}円 / 投資済み {int(s.current_invested_jpy):,}円"
         f"（cash {s.cash_ratio:.0%} / invested {s.invested_ratio:.0%}）"),
        f"> - 初回投入上限: 約{int(s.max_initial_deployment_jpy):,}円（方針）",
    ]
    related = context.existing_related_holdings
    if related:
        syms = ", ".join(h.symbol for h in related)
        lines.append(
            f"> - 既存関連保有(existing_related_holdings): **read-only** / {syms}"
            "（既存NISA保有等。AI検証枠の投資済み額には合算せず、売却・是正提案の主対象にしません）"
        )
    lines.append("> - 今回の判断はAI検証枠が対象であり、全資産・既存保有の売却・是正提案ではありません")
    return lines


def build_audit_context_notes(context: PortfolioContext) -> list[str]:
    """Lines appended to the Decision Audit safety section."""
    s = context.ai_sleeve
    notes = [
        "- core_manual（コア資産）は read-only。売却・是正提案の主対象にしません。",
        f"- Bot管理対象はAI検証枠(ai_sleeve)のみ（ai_sleeve_state.csv基準）：予算{int(s.total_budget_jpy):,}円 "
        f"/ 現金{int(s.current_cash_jpy):,}円 / 投資済み{int(s.current_invested_jpy):,}円。",
    ]
    related = context.existing_related_holdings
    if related:
        syms = ", ".join(h.symbol for h in related)
        notes.append(
            f"- 既存関連保有({syms})は read-only。AI検証枠の投資済み額には合算せず、"
            "売却・是正提案の主対象にしません。"
        )
    notes.append(
        "- AI判断と人間判断のズレは、AI検証枠内の判断として扱います（全資産・既存保有の売却提案ではありません）。"
    )
    return notes
