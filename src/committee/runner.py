"""Phase 3.1: Investment Committee OS — orchestration.

Runs the two-tier committee against an already-final quant allocation. Shadow
mode only: never returns weights, never overrides allocation. Any failure
degrades gracefully to INSUFFICIENT_DATA so the main pipeline is never broken.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

from src.committee.models import (
    CommitteeConfig,
    CommitteeMemberConfig,
    CommitteeResult,
    CommitteeTier,
    CommitteeVerdict,
    MemberOutput,
    aggregate_verdict,
)
from src.committee.prompts import (
    build_batch_system_prompt,
    build_batch_user_prompt,
    build_member_system_prompt,
    build_member_user_prompt,
)
from src.llm.base import BaseLlmClient
from src.logger import logger

# Reused from the AI-audit defense layer: forbidden auto-trade phrasing.
_FORBIDDEN_AUTO_TRADE = [
    r"自動売買",
    r"自動注文",
    r"注文を.{0,10}出し",
    r"売買.{0,10}実行",
    r"APIで.{0,10}注文",
    r"楽天証券.{0,10}注文",
]

_DEFAULT_CONFIG_PATH = "config/committee.yaml"

_ACTION_BY_VERDICT: dict[CommitteeVerdict, str] = {
    CommitteeVerdict.PASS: "現状の定量配分を維持して問題なし（shadow: 配分への影響なし）。",
    CommitteeVerdict.PASS_WITH_CAUTION: "現状配分を維持しつつ、提示されたリスクを監視する（shadow: 配分への影響なし）。",
    CommitteeVerdict.WATCH: "配分変更は行わず、次回レビューまで重点的に注視する（shadow: 配分への影響なし）。",
    CommitteeVerdict.REJECT: "人間による再レビューを推奨。自動売買・自動変更は行わない（shadow: 配分への影響なし）。",
    CommitteeVerdict.INSUFFICIENT_DATA: "データ不足のため判断保留。人間が補足確認する（shadow: 配分への影響なし）。",
}


# ── config loading ─────────────────────────────────────────────────────────────


def load_committee_config(path: str | Path = _DEFAULT_CONFIG_PATH) -> CommitteeConfig:
    """Load config/committee.yaml into a CommitteeConfig.

    Returns a disabled default config if the file is missing.
    """
    p = Path(path)
    if not p.exists():
        logger.warning(f"Committee config not found at {p} — committee disabled.")
        return CommitteeConfig(enabled=False)
    with open(p, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    section = raw.get("committee", raw)
    return CommitteeConfig.model_validate(section)


# ── JSON extraction ──────────────────────────────────────────────────────────


def _extract_json_array(text: str) -> list:
    stripped = text.strip()
    try:
        data = json.loads(stripped)
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            # tolerate {"members": [...]} or a single object
            if isinstance(data.get("members"), list):
                return data["members"]
            return [data]
    except json.JSONDecodeError:
        pass

    m = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", stripped, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass

    m = re.search(r"\[.*\]", stripped, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass

    raise ValueError(f"No JSON array found in committee response (first 200 chars): {stripped[:200]}")


def _extract_json_object(text: str) -> dict:
    stripped = text.strip()
    try:
        data = json.loads(stripped)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass

    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", stripped, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass

    m = re.search(r"\{.*\}", stripped, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass

    raise ValueError(f"No JSON object found in committee response (first 200 chars): {stripped[:200]}")


def _scrub_forbidden(member: MemberOutput) -> None:
    """Defensive: flag any auto-trade phrasing that slipped into member text."""
    fields = (member.rationale, member.strongest_support, member.strongest_objection, member.action_implication)
    joined = " ".join(fields)
    for pat in _FORBIDDEN_AUTO_TRADE:
        if re.search(pat, joined):
            logger.warning(f"Committee member {member.member_id}: forbidden auto-trade phrasing detected ({pat})")
            member.action_implication = "（取引執行に関する不適切な表現を検出したため助言を保留。配分への影響なし）"
            member.key_risks = member.key_risks + ["auto_trade_language_detected"]
            break


# ── member construction ────────────────────────────────────────────────────────


def _fallback_member(
    member_cfg: CommitteeMemberConfig,
    tier: CommitteeTier,
    reason: str,
) -> MemberOutput:
    return MemberOutput(
        member_id=member_cfg.member_id,
        display_name=member_cfg.display_name,
        tier=tier,
        verdict=CommitteeVerdict.INSUFFICIENT_DATA,
        confidence=0.0,
        rationale=reason,
        strongest_support="(データ不足)",
        strongest_objection="(データ不足)",
        dissenting_view="(データ不足)",
        key_risks=["insufficient_data"],
        required_checks=["LLM応答の再取得"],
        next_review_triggers=["LLM応答の再取得"],
        action_implication="データ不足のため助言保留（shadow: 配分への影響なし）。",
    )


def _build_member_from_data(
    data: dict,
    member_cfg: CommitteeMemberConfig,
    tier: CommitteeTier,
) -> MemberOutput:
    """Build a MemberOutput from raw LLM data; member_id/display_name/tier forced from config."""
    payload = dict(data) if isinstance(data, dict) else {}
    payload["member_id"] = member_cfg.member_id
    payload["display_name"] = member_cfg.display_name
    payload["tier"] = tier.value
    try:
        member = MemberOutput.model_validate(payload)
    except Exception as e:
        logger.warning(f"Committee member {member_cfg.member_id} validation failed: {type(e).__name__} — fallback.")
        return _fallback_member(member_cfg, tier, reason=f"応答の検証に失敗: {type(e).__name__}")
    _scrub_forbidden(member)
    # Guarantee each member surfaces at least one concrete risk or check.
    if not member.key_risks and not member.required_checks:
        member.required_checks = ["具体的な確認項目が未提示 — 次回レビューで再評価を推奨。"]
    return member


# ── public API ───────────────────────────────────────────────────────────────


def _all_insufficient(members: list[tuple[CommitteeMemberConfig, CommitteeTier]], reason: str) -> list[MemberOutput]:
    return [_fallback_member(m, tier, reason) for m, tier in members]


def evaluate_satellite_activation(
    context: dict,
    ai_audit_status: str | None,
) -> tuple[bool, str]:
    """Decide whether the Satellite Committee should run (conditional mode).

    Data-driven triggers (any one activates):
      - theme/sector/emerging exposure present (テーマETFが入っている)
      - growth-equity exposure present (QQQM等の成長株比率)
      - turnover > 0 — there were buys/increases this cycle (新規買付・買い増し)
      - AI audit status is PASS_WITH_CAUTION or worse
    Note: the "Core Committee WATCH+" trigger cannot be evaluated pre-call in
    batch mode (core verdicts come from the same LLM call), so it is approximated
    by the data triggers above; in per_member mode the same applies for simplicity.
    """
    reasons: list[str] = []

    alloc = context.get("allocation") or []
    theme_cats = {"theme_equity", "sector", "emerging_equity"}
    if any(e.get("category") in theme_cats and (e.get("weight") or 0) > 0 for e in alloc):
        reasons.append("テーマ/セクターETFが配分に含まれる")

    cat_summary = context.get("category_summary") or {}
    growth = cat_summary.get("growth_equity")
    if growth is None:
        growth = sum(e.get("weight", 0) for e in alloc if e.get("category") == "growth_equity")
    if growth and growth > 0:
        reasons.append(f"成長株比率が存在（growth_equity={growth:.1%}）")

    turnover = context.get("turnover")
    if turnover is not None and turnover > 0:
        reasons.append(f"売買・入替あり（turnover={turnover:.1%}）")

    if ai_audit_status in ("PASS_WITH_CAUTION", "REVIEW_REQUIRED", "REJECT"):
        reasons.append(f"AI監査が要警戒（{ai_audit_status}）")

    if reasons:
        return True, "; ".join(reasons)
    return False, "起動トリガー（テーマ/成長株/売買増/AI監査警戒）がいずれも成立せず"


def _run_batch(
    context: dict,
    committee_cfg: CommitteeConfig,
    client: BaseLlmClient,
    members: list[tuple[CommitteeMemberConfig, CommitteeTier]],
) -> list[MemberOutput]:
    members_cfg = [m for m, _ in members]
    tier_by_id = {m.member_id: tier for m, tier in members}
    cfg_by_id = {m.member_id: m for m in members_cfg}

    system = build_batch_system_prompt(members_cfg)
    user = build_batch_user_prompt(context, members_cfg)
    try:
        raw = client.complete(system=system, user=user, max_tokens=committee_cfg.max_tokens_per_member * len(members_cfg))
        arr = _extract_json_array(raw)
    except Exception as e:
        logger.warning(f"Committee batch call failed: {type(e).__name__}: {e} — all members INSUFFICIENT_DATA.")
        return _all_insufficient(members, reason=f"バッチ呼び出し失敗: {type(e).__name__}")

    by_id = {}
    for item in arr:
        if isinstance(item, dict) and item.get("member_id") in cfg_by_id:
            by_id[item["member_id"]] = item

    members: list[MemberOutput] = []
    for mid, m_cfg in cfg_by_id.items():
        tier = tier_by_id[mid]
        if mid in by_id:
            members.append(_build_member_from_data(by_id[mid], m_cfg, tier))
        else:
            logger.warning(f"Committee member {mid} missing from batch response — fallback.")
            members.append(_fallback_member(m_cfg, tier, reason="バッチ応答に該当メンバーなし"))
    return members


def _run_per_member(
    context: dict,
    committee_cfg: CommitteeConfig,
    client: BaseLlmClient,
    members: list[tuple[CommitteeMemberConfig, CommitteeTier]],
) -> list[MemberOutput]:
    out: list[MemberOutput] = []
    for m_cfg, tier in members:
        system = build_member_system_prompt(m_cfg, tier)
        user = build_member_user_prompt(context, m_cfg)
        try:
            raw = client.complete(system=system, user=user, max_tokens=committee_cfg.max_tokens_per_member)
            data = _extract_json_object(raw)
            out.append(_build_member_from_data(data, m_cfg, tier))
        except Exception as e:
            logger.warning(f"Committee member {m_cfg.member_id} call failed: {type(e).__name__} — fallback.")
            out.append(_fallback_member(m_cfg, tier, reason=f"個別呼び出し失敗: {type(e).__name__}"))
    return out


def run_committee(
    context: dict,
    committee_cfg: CommitteeConfig,
    client: BaseLlmClient | None,
    ai_audit_status: str | None = None,
) -> CommitteeResult:
    """Run the two-tier committee in shadow mode.

    Returns a CommitteeResult whose ``allocation_override`` is always False and
    which never carries portfolio weights. Degrades to INSUFFICIENT_DATA on any
    failure or when no client/members are available.

    Satellite Committee may be skipped when ``satellite_activation == "conditional"``
    and no activation trigger is met; Core Committee always runs.
    """
    has_members = bool(committee_cfg.core_committee or committee_cfg.satellite_committee)

    # Decide Satellite activation (Core always runs).
    satellite_activated = True
    activation_reason = "always モード"
    if committee_cfg.satellite_activation == "conditional":
        satellite_activated, activation_reason = evaluate_satellite_activation(context, ai_audit_status)

    core_members = [(m, CommitteeTier.CORE) for m in committee_cfg.core_committee]
    sat_members = [(m, CommitteeTier.SATELLITE) for m in committee_cfg.satellite_committee]
    run_members = core_members + (sat_members if satellite_activated else [])

    if client is None or not has_members:
        reason = "LLMクライアント未提供" if client is None else "メンバー未定義"
        logger.info(f"Committee: {reason} — INSUFFICIENT_DATA result generated (shadow).")
        members = _all_insufficient(run_members, reason=reason)
    elif committee_cfg.llm_call_mode == "per_member":
        logger.info(f"Committee: running in per_member mode (satellite_activated={satellite_activated}).")
        members = _run_per_member(context, committee_cfg, client, run_members)
    else:
        logger.info(f"Committee: running in batch mode (satellite_activated={satellite_activated}).")
        members = _run_batch(context, committee_cfg, client, run_members)

    core_verdicts = [m.verdict for m in members if m.tier == CommitteeTier.CORE]
    sat_verdicts = [m.verdict for m in members if m.tier == CommitteeTier.SATELLITE]

    core_verdict = aggregate_verdict(core_verdicts)
    # Skipped satellite aggregates to INSUFFICIENT_DATA (no members evaluated).
    sat_verdict = aggregate_verdict(sat_verdicts)
    # Final = aggregate of the two tier verdicts (NOT a member-level majority vote).
    final_verdict = aggregate_verdict([core_verdict, sat_verdict])

    # Deduped review triggers across members (both per-member next_review_triggers
    # and required_checks), preserving order.
    triggers: list[str] = []
    for m in members:
        for c in list(m.next_review_triggers) + list(m.required_checks):
            if c and c not in triggers:
                triggers.append(c)

    sat_note = "" if satellite_activated else f" Satellite未起動（{activation_reason}）。"
    summary = (
        f"Core={core_verdict.value} / Satellite={sat_verdict.value} → "
        f"最終={final_verdict.value}。"
        f"{_ACTION_BY_VERDICT[final_verdict]}{sat_note}"
    )

    result = CommitteeResult(
        core_committee_verdict=core_verdict,
        satellite_committee_verdict=sat_verdict,
        final_committee_verdict=final_verdict,
        recommended_action=_ACTION_BY_VERDICT[final_verdict],
        allocation_override=False,  # forced False by validator regardless
        summary=summary,
        next_review_triggers=triggers,
        members=members,
        shadow_mode=committee_cfg.shadow_mode,
        llm_call_mode=committee_cfg.llm_call_mode,
        satellite_activated=satellite_activated,
        satellite_activation_reason=activation_reason,
    )
    logger.info(
        f"Committee complete — core={core_verdict.value} satellite={sat_verdict.value} "
        f"final={final_verdict.value} satellite_activated={satellite_activated} (shadow, override=False)"
    )
    return result


def save_committee_result(result: CommitteeResult, output_dir: str) -> str:
    """Save committee_result.json to output_dir; returns the file path."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / "committee_result.json"
    path.write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"Committee result saved to {path}")
    return str(path)
