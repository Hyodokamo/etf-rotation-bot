"""Phase 5.0: ETF Master Loader / Candidate Enrichment.

Loads ``data/etf_master.csv`` (the curated AI-sleeve ETF universe) and turns each
row into read-only *enrichment* for Candidate Review / the Investment Committee.

Design intent (per spec):
- ``universe_status == "active_core"`` are the **normal** Candidate Review targets.
- ``research`` / ``support`` / ``fallback`` / ``low_priority`` are **NOT excluded** —
  they are handled with a limited role / review frequency.
- ``agent_affinity`` / ``agent_concern`` are **hints** for generating support /
  objection arguments. They are NOT a filter: every committee member still
  evaluates every ETF independently.
- ``needs_order_screen_check == true`` → surface "発注前確認要" in Slack / Markdown.
- ``data_quality_status`` in ``needs_*`` / ``insufficient`` → the AI Auditor warns.
- An ETF absent from the master is **not dropped**: it is treated as
  ``unknown`` metadata.

Hard guarantees (consistent with every prior phase): never changes
``final_allocation`` / weights, never computes order quantity, no auto-trading,
no brokerage integration. This module is read-only enrichment only.
"""
from __future__ import annotations

import csv
from pathlib import Path

from pydantic import BaseModel, Field, field_validator

from src.logger import logger

DEFAULT_MASTER_PATH = "data/etf_master.csv"
DEFAULT_ACTIVE_CORE_PATH = "data/etf_master_active_core.csv"

# universe_status vocabulary
STATUS_ACTIVE_CORE = "active_core"
STATUS_RESEARCH = "research"
STATUS_SUPPORT = "support"
STATUS_FALLBACK = "fallback"
STATUS_LOW_PRIORITY = "low_priority"
STATUS_UNKNOWN = "unknown"

# Role label + review frequency per universe_status. research/support/fallback/
# low_priority are kept (not excluded) but with a limited role.
REVIEW_ROLE_BY_STATUS: dict[str, tuple[str, str]] = {
    STATUS_ACTIVE_CORE: ("通常レビュー対象", "monthly"),
    STATUS_RESEARCH: ("限定レビュー（必要時/四半期）", "quarterly"),
    STATUS_SUPPORT: ("支援役・参照のみ（緩衝材/比較）", "reference_only"),
    STATUS_FALLBACK: ("代替候補（主候補が使えない時のみ）", "fallback_only"),
    STATUS_LOW_PRIORITY: ("監視のみ（主戦場化しない）", "watch_only"),
    STATUS_UNKNOWN: ("メタデータ未登録（個別確認）", "ad_hoc"),
}

# Columns the loader reads. Extra columns are tolerated and ignored.
_BOOL_FIELDS = [
    "include_in_active_universe", "trend_candidate", "value_candidate",
    "quality_candidate", "macro_theme_candidate", "defensive_candidate",
    "cash_buffer_candidate", "needs_order_screen_check",
]
_LIST_FIELDS = ["agent_affinity", "agent_concern", "source_urls"]
_AGENT_LIST_FIELDS = {"agent_affinity", "agent_concern"}


def _to_bool(v) -> bool:
    if isinstance(v, bool):
        return v
    return str(v or "").strip().lower() in ("true", "1", "yes", "y")


def _normalize_agent_id(name: str) -> str:
    """'Paul Tudor Jones' -> 'paul_tudor_jones'; leave member_ids unchanged."""
    return "_".join(name.strip().lower().split())


def _split_list(v, *, agents: bool) -> list[str]:
    if isinstance(v, list):
        items = v
    else:
        items = [x for x in str(v or "").split(";")]
    out: list[str] = []
    for x in items:
        x = x.strip()
        if not x:
            continue
        out.append(_normalize_agent_id(x) if agents else x)
    return out


class EtfMasterEntry(BaseModel):
    symbol: str
    name: str = ""
    issuer: str = ""
    asset_class: str = ""
    category: str = ""
    sector: str = ""
    theme: str = ""
    role_in_ai_sleeve: str = ""
    offense_defense: str = ""
    primary_use: str = ""
    universe_status: str = STATUS_UNKNOWN
    include_in_active_universe: bool = False
    agent_affinity: list[str] = Field(default_factory=list)
    agent_concern: list[str] = Field(default_factory=list)
    expense_ratio: str = ""
    expense_ratio_source: str = ""
    expense_ratio_as_of_date: str = ""
    liquidity_tier: str = ""
    aum_or_liquidity_note: str = ""
    trend_candidate: bool = False
    value_candidate: bool = False
    quality_candidate: bool = False
    macro_theme_candidate: bool = False
    defensive_candidate: bool = False
    cash_buffer_candidate: bool = False
    nisa_eligible_status: str = ""
    nisa_usage_policy: str = ""
    preferred_account: str = ""
    rakuten_available: str = ""
    sbi_available: str = ""
    monex_available: str = ""
    nomura_available: str = ""
    broker_availability_note: str = ""
    needs_order_screen_check: bool = False
    expected_overlap_with_core: str = ""
    core_overlap_reason: str = ""
    main_risks: str = ""
    required_checks: str = ""
    entry_checks: str = ""
    avoid_entry_conditions: str = ""
    invalidation_conditions: str = ""
    data_quality_status: str = ""
    source_urls: list[str] = Field(default_factory=list)
    notes: str = ""
    # False when synthesized for an ETF not present in the master.
    is_known: bool = True

    @field_validator(*_BOOL_FIELDS, mode="before")
    @classmethod
    def _parse_bool(cls, v):
        return _to_bool(v)

    @field_validator("agent_affinity", "agent_concern", mode="before")
    @classmethod
    def _parse_agent_list(cls, v):
        return _split_list(v, agents=True)

    @field_validator("source_urls", mode="before")
    @classmethod
    def _parse_url_list(cls, v):
        return _split_list(v, agents=False)

    @property
    def is_normal_review_target(self) -> bool:
        return self.universe_status == STATUS_ACTIVE_CORE

    @property
    def needs_data_quality_warning(self) -> bool:
        s = (self.data_quality_status or "").strip().lower()
        return s.startswith("needs_") or s == "insufficient"

    @property
    def review_role(self) -> str:
        return REVIEW_ROLE_BY_STATUS.get(self.universe_status, REVIEW_ROLE_BY_STATUS[STATUS_UNKNOWN])[0]

    @property
    def review_frequency(self) -> str:
        return REVIEW_ROLE_BY_STATUS.get(self.universe_status, REVIEW_ROLE_BY_STATUS[STATUS_UNKNOWN])[1]


# ── loading ──────────────────────────────────────────────────────────────────


def load_etf_master(path: str | Path = DEFAULT_MASTER_PATH) -> dict[str, EtfMasterEntry]:
    """Load the ETF master CSV into ``{symbol -> EtfMasterEntry}``.

    Missing file -> ``{}`` (graceful). A bad row is skipped with a warning rather
    than aborting the whole load. Extra columns are ignored.
    """
    p = Path(path)
    if not p.exists():
        logger.info(f"ETF master not found (enrichment skipped): {p}")
        return {}
    master: dict[str, EtfMasterEntry] = {}
    known_fields = set(EtfMasterEntry.model_fields)
    with open(p, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames or "symbol" not in reader.fieldnames:
            logger.warning("ETF master missing 'symbol' column; skipping load")
            return {}
        for i, row in enumerate(reader, 1):
            data = {k: v for k, v in row.items() if k in known_fields and v is not None}
            sym = (data.get("symbol") or "").strip()
            if not sym:
                continue
            try:
                entry = EtfMasterEntry.model_validate(data)
                master[entry.symbol.strip().upper()] = entry
            except Exception as e:  # never abort the whole master on one bad row
                logger.warning(f"skipping ETF master row {i} ({sym}): {e}")
    return master


def unknown_entry(symbol: str) -> EtfMasterEntry:
    """Synthesize an ``unknown`` metadata entry for an ETF absent from the master."""
    return EtfMasterEntry(symbol=symbol, universe_status=STATUS_UNKNOWN, is_known=False)


def get_entry(
    master: dict[str, EtfMasterEntry] | None, symbol: str
) -> EtfMasterEntry:
    """Look up a symbol; never drop an unknown ETF — synthesize unknown metadata."""
    if master:
        hit = master.get((symbol or "").strip().upper())
        if hit is not None:
            return hit
    return unknown_entry(symbol)


def select_active_core(master: dict[str, EtfMasterEntry]) -> list[EtfMasterEntry]:
    """The normal Candidate Review universe (active_core only)."""
    return [e for e in master.values() if e.is_normal_review_target]


def select_by_status(
    master: dict[str, EtfMasterEntry], status: str
) -> list[EtfMasterEntry]:
    return [e for e in master.values() if e.universe_status == status]


# ── enrichment (read-only; aggregates / hints only) ────────────────────────────


def build_enrichment(entry: EtfMasterEntry) -> dict:
    """Read-only enrichment block for the committee context.

    No weights, no order quantity, no auto-trade — hints and flags only.
    """
    needs_dq = entry.needs_data_quality_warning
    return {
        "known_in_master": entry.is_known,
        "universe_status": entry.universe_status,
        "review_role": entry.review_role,
        "review_frequency": entry.review_frequency,
        "is_normal_review_target": entry.is_normal_review_target,
        "role_in_ai_sleeve": entry.role_in_ai_sleeve,
        "offense_defense": entry.offense_defense,
        "expected_overlap_with_core": entry.expected_overlap_with_core,
        "core_overlap_reason": entry.core_overlap_reason,
        "nisa_usage_policy": entry.nisa_usage_policy,
        "preferred_account": entry.preferred_account,
        # Hints only — NOT a filter. Every member evaluates every ETF.
        "agent_affinity_hint": entry.agent_affinity,
        "agent_concern_hint": entry.agent_concern,
        "hint_note": (
            "agent_affinity / agent_concern は評価対象の制限ではなく、賛成/反対論点生成の補助です。"
            "全メンバーが全ETFを独立に評価してください。"
        ),
        "needs_order_screen_check": entry.needs_order_screen_check,
        "order_screen_note": (
            "発注前確認要：ブローカー取扱い・最新費用等を注文画面で必ず確認（自動発注は行いません）"
            if entry.needs_order_screen_check else ""
        ),
        "data_quality_status": entry.data_quality_status,
        "data_quality_needs_warning": needs_dq,
        "data_quality_note": (
            f"data_quality_status={entry.data_quality_status}。"
            "AI Auditor(core_ai_auditor) はデータ品質警告を出し、発注前にデータの裏取りを求めること。"
            if needs_dq else ""
        ),
    }


def build_order_screen_label(entry: EtfMasterEntry) -> str:
    """Short display label for needs_order_screen_check (empty if not needed)."""
    return "⚠️ 発注前確認要" if entry.needs_order_screen_check else ""
