"""Phase 5.1: signal committee runner.

Reuses the existing 7 Investment Committee members (from config/committee.yaml)
to evaluate whether an ETF is a buy candidate during a crash/correction.

Design:
- No new agent types created. All 7 existing members evaluate all ETFs.
- agent_affinity in ETF master is a hint only — it does NOT restrict which
  members evaluate which ETFs.
- When ``client`` is None, returns neutral fallbacks (score=0, no veto).
- Output: list[SignalMemberOutput] — one per committee member.
- SELL signals are never generated; ai_auditor vetos on data quality issues.
"""
from __future__ import annotations

import json
import re

from src.committee.models import CommitteeConfig, CommitteeMemberConfig, CommitteeTier
from src.committee.runner import load_committee_config
from src.logger import logger
from src.signals.signal_models import MemberStance, SignalContext, SignalMemberOutput

# Signal evaluation lenses for the existing 7 members — NOT new agent types.
# Keys must match exactly the member_ids in config/committee.yaml.
_SIGNAL_PERSONAS: dict[str, str] = {
    "aqr_meb": (
        "AQRスタイルの定量・モメンタム分析者として評価する。"
        "1M/3M/6M/12Mモメンタム、200日線乖離、ルールベースのエントリー妥当性に注目する。"
    ),
    "howard_marks": (
        "Howard Marks型のリスク・サイクル分析者として評価する。"
        "市場心理の過熱または恐怖、安全余地、下落が機会か構造悪化かを見る。"
        "ナイフ掴みリスクに特に警戒する。"
    ),
    "rob_arnott": (
        "Rob Arnott型のバリュエーション・平均回帰分析者として評価する。"
        "バリュエーション、グロース押し目買いの危険性、勝者集中リスクに注目する。"
    ),
    "core_ai_auditor": (
        "AI品質監査者として評価する。"
        "data_quality_status・needs_order_screen_check・既存コアとの重複・"
        "注文数量計算がないことを監査する。"
        "data_quality_status=insufficientの場合はveto=trueにする。"
    ),
    "buffett": (
        "Buffett型の長期品質・事業価値分析者として評価する。"
        "ETFの中身の理解可能性、長期保有耐性、価格妥当性を見る。"
    ),
    "paul_tudor_jones": (
        "Paul Tudor Jones型のトレンド・ボラティリティ分析者として評価する。"
        "トレンド方向、下落速度、明確な撤退条件、小さく入る妥当性を見る。"
    ),
    "druckenmiller": (
        "Druckenmiller型の大局テーマ・集中投資分析者として評価する。"
        "大きなテーマの持続性、非対称な機会、中途半端な分散ではないかを見る。"
    ),
}

_OUTPUT_FORMAT = """
Output a JSON array with one object per member in this exact format:
[
  {
    "member_id": "<member_id from input>",
    "stance": "<positive|neutral|cautious|reject>",
    "score": <integer -2 to +2>,
    "confidence": <0.0 to 1.0>,
    "rationale": "<concrete Japanese reason, 1-2 sentences>",
    "dissenting_view": "<strongest objection to buying this ETF now>",
    "risk_flags": ["<flag>"],
    "veto": <true only for ai_auditor on data_quality=insufficient or rule violations>,
    "next_review_triggers": ["<trigger>"]
  }
]
Rules:
- score: positive>=1, neutral=0, cautious<=-1, reject=-2
- No 買え/売れ/注文/購入実行/売却実行 wording
- BUY_CANDIDATE = Watchlist候補化, not an actual order
- SELL signal is reserved; do not generate SELL output
- Do not fabricate data not in the signal context
- Do not propose selling core_manual/satellite_legacy/existing_related_holdings
"""


def _build_signal_batch_prompt(
    members: list[tuple[CommitteeMemberConfig, CommitteeTier]],
    signal_ctx: SignalContext,
) -> tuple[str, str]:
    from src.portfolio_context import CORE_READ_ONLY_PREMISE
    member_lines = "\n".join(
        f"- {cfg.member_id}: {_SIGNAL_PERSONAS.get(cfg.member_id, cfg.focus)}"
        for cfg, _ in members
    )
    system = (
        "あなたはETFの急落・押し目シグナルを評価するInvestment Committee（7人）です。\n"
        "各メンバーは独立して評価し、他メンバーの結論を参照しないでください。\n\n"
        "# コア資産の扱い（read-only前提）\n"
        f"{CORE_READ_ONLY_PREMISE}\n\n"
        "# 重要なルール\n"
        "- BUY_CANDIDATEは買付指示ではなく、Watchlist候補化の評価です\n"
        "- 注文数量・自動売買・証券口座連携は一切行いません\n"
        "- SELL シグナルは今回MVPでは実装されません（reserved）\n"
        "- core_manual / satellite_legacy / existing_related_holdings の売却提案をしない\n"
        "- agent_affinity はヒントのみ。全メンバーが全ETFを評価します\n\n"
        "# メンバーとその評価軸\n"
        f"{member_lines}\n\n"
        f"{_OUTPUT_FORMAT}"
    )
    ctx_dict = signal_ctx.model_dump(mode="json")
    member_ids = [cfg.member_id for cfg, _ in members]
    user = (
        f"以下のシグナルコンテキストを評価し、各メンバーの見解をJSONで出力してください。\n\n"
        f"評価対象ETF: {signal_ctx.symbol}\n"
        f"Signal Side: {signal_ctx.signal_side.value} (SELL は今回対象外)\n"
        f"Members: {member_ids}\n\n"
        f"Signal Context:\n{json.dumps(ctx_dict, ensure_ascii=False, indent=2)}\n\n"
        "JSON array only:"
    )
    return system, user


def _extract_json_array(text: str) -> list:
    stripped = text.strip()
    try:
        data = json.loads(stripped)
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and isinstance(data.get("members"), list):
            return data["members"]
    except json.JSONDecodeError:
        pass
    for pat in (r"```(?:json)?\s*(\[.*?\])\s*```", r"(\[.*\])"):
        m = re.search(pat, stripped, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1))
            except json.JSONDecodeError:
                pass
    return []


def _neutral_member(cfg: CommitteeMemberConfig, reason: str = "LLMクライアントなし") -> SignalMemberOutput:
    return SignalMemberOutput(
        member_id=cfg.member_id,
        stance=MemberStance.NEUTRAL,
        score=0,
        confidence=0.0,
        rationale=reason,
        risk_flags=["insufficient_data"],
    )


def _parse_member(data: dict, cfg: CommitteeMemberConfig) -> SignalMemberOutput:
    payload = dict(data)
    payload["member_id"] = cfg.member_id  # always force from config
    try:
        return SignalMemberOutput.model_validate(payload)
    except Exception as e:
        logger.warning(f"Signal member {cfg.member_id} parse failed: {type(e).__name__}")
        return _neutral_member(cfg, f"応答解析失敗: {type(e).__name__}")


def run_signal_committee(
    signal_ctx: SignalContext,
    committee_cfg: CommitteeConfig | None = None,
    client=None,
    committee_config_path: str = "config/committee.yaml",
) -> list[SignalMemberOutput]:
    """Run existing 7 committee members on the signal context.

    All members evaluate all ETFs regardless of agent_affinity.
    When ``client`` is None, returns neutral fallbacks (score=0, veto=False).
    No new agent types are created — only the 7 members from committee.yaml.
    """
    if committee_cfg is None:
        committee_cfg = load_committee_config(committee_config_path)

    all_members: list[tuple[CommitteeMemberConfig, CommitteeTier]] = [
        (m, CommitteeTier.CORE) for m in committee_cfg.core_committee
    ] + [
        (m, CommitteeTier.SATELLITE) for m in committee_cfg.satellite_committee
    ]

    if not all_members:
        logger.warning("Signal committee: no members configured")
        return []

    if client is None:
        logger.info("Signal committee: no LLM client — neutral fallbacks for all members")
        return [_neutral_member(cfg) for cfg, _ in all_members]

    system, user = _build_signal_batch_prompt(all_members, signal_ctx)
    try:
        response = client.complete(
            system=system,
            user=user,
            max_tokens=committee_cfg.max_tokens_per_member * len(all_members),
        )
    except Exception as e:
        logger.warning(f"Signal committee LLM call failed: {type(e).__name__}: {e}")
        return [_neutral_member(cfg, f"LLMエラー: {type(e).__name__}") for cfg, _ in all_members]

    raw_list = _extract_json_array(response)
    by_id = {d.get("member_id"): d for d in raw_list if isinstance(d, dict)}

    return [_parse_member(by_id.get(cfg.member_id, {}), cfg) for cfg, _ in all_members]
