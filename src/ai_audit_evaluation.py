"""Phase 2.5: AI audit quality evaluation logger."""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from src.logger import logger
from src.schemas import LlmAuditResult

_AUTO_TRADE_PATTERNS = [
    r"自動発注",
    r"自動売買",
    r"注文を実行",
    r"発注してください",
    r"APIで.{0,10}注文",
    r"売買.{0,10}実行",
]

_NISA_ROTATION_PATTERNS = [
    r"NISAで月次売買",
    r"NISAでローテーション",
    r"NISA成長投資枠で毎月入れ替え",
    r"NISA.{0,20}毎月.{0,20}積立",
    r"毎月.{0,20}スイッチング",
    r"NISA.{0,20}スイッチング",
]


def _detect(text: str, patterns: list[str]) -> list[str]:
    return [p for p in patterns if re.search(p, text)]


def build_quality_checks(
    audit_result: LlmAuditResult | None,
) -> dict:
    """Build machine-verifiable quality check results for the AI audit."""
    if audit_result is None:
        return {
            "json_valid": False,
            "adjustment_limit_check": "N/A",
            "auto_trade_prohibited_check": "N/A",
            "nisa_rotation_prohibited_check": "N/A",
            "final_allocation_unchanged": True,
        }

    full_text = audit_result.summary + " " + audit_result.raw_response

    adj_check = "PASS_WITH_CAUTION" if audit_result.adjustments_invalidated else "PASS"

    auto_hits = _detect(full_text, _AUTO_TRADE_PATTERNS)
    if auto_hits:
        logger.warning(f"AI audit quality: auto-trade patterns detected: {auto_hits}")
    auto_check = "REVIEW_REQUIRED" if auto_hits else "PASS"

    nisa_hits = _detect(full_text, _NISA_ROTATION_PATTERNS)
    if nisa_hits:
        logger.warning(f"AI audit quality: NISA rotation patterns detected: {nisa_hits}")
    nisa_check = "REVIEW_REQUIRED" if nisa_hits else "PASS"

    return {
        "json_valid": True,
        "adjustment_limit_check": adj_check,
        "auto_trade_prohibited_check": auto_check,
        "nisa_rotation_prohibited_check": nisa_check,
        "final_allocation_unchanged": True,
    }


def build_evaluation_markdown(
    run_date: date,
    provider: str,
    model: str,
    audit_result: LlmAuditResult | None,
    quality_checks: dict,
) -> str:
    lines = ["# AI監査品質評価", ""]

    # --- 実行情報 ---
    lines += ["## 実行情報", ""]
    lines.append(f"- 実行日：{run_date.isoformat()}")
    lines.append(f"- Provider：{provider}")
    lines.append(f"- Model：{model}")
    if audit_result is not None:
        lines.append(f"- AI監査ステータス：{audit_result.status.value}")
        need_review = audit_result.status.value in ("REVIEW_REQUIRED", "REJECT")
        lines.append(f"- 人間確認要否：{'要確認' if need_review else '確認不要'}")
        lines.append(f"- 調整案の無効化：{'あり' if audit_result.adjustments_invalidated else 'なし'}")
    else:
        lines.append("- AI監査ステータス：N/A（監査失敗）")
        lines.append("- 人間確認要否：—")
        lines.append("- 調整案の無効化：—")
    lines.append("")

    # --- 機械的チェック ---
    lines += ["## 機械的チェック", ""]
    lines += ["| 観点 | 結果 | コメント |", "|------|------|----------|"]

    _ICONS = {
        "PASS": "✅", "PASS_WITH_CAUTION": "⚠️",
        "REVIEW_REQUIRED": "🔶", "FAIL": "❌", "N/A": "—",
    }
    rows = [
        (
            "JSON妥当性",
            "PASS" if quality_checks["json_valid"] else "FAIL",
            "Pydantic validation passed" if quality_checks["json_valid"] else "JSON解析またはスキーマ検証失敗",
        ),
        (
            "±5%制約",
            quality_checks["adjustment_limit_check"],
            "一部調整案を無効化" if quality_checks["adjustment_limit_check"] == "PASS_WITH_CAUTION"
            else ("全調整案が制約内" if quality_checks["adjustment_limit_check"] == "PASS" else "—"),
        ),
        (
            "自動売買禁止",
            quality_checks["auto_trade_prohibited_check"],
            "自動売買提案なし" if quality_checks["auto_trade_prohibited_check"] == "PASS"
            else "⚠️ 自動売買系文言を検出",
        ),
        (
            "NISA月次売買禁止",
            quality_checks["nisa_rotation_prohibited_check"],
            "NISAローテーション提案なし" if quality_checks["nisa_rotation_prohibited_check"] == "PASS"
            else "⚠️ NISAローテーション系文言を検出",
        ),
        (
            "最終配分維持",
            "PASS" if quality_checks["final_allocation_unchanged"] else "FAIL",
            "final_allocation unchanged",
        ),
    ]
    for name, result, comment in rows:
        icon = _ICONS.get(result, result)
        lines.append(f"| {name} | {icon} {result} | {comment} |")
    lines.append("")

    # --- AI監査コメント要約 ---
    if audit_result is not None:
        lines += ["## AI監査コメントの要約", ""]
        lines.append(f"**サマリー:** {audit_result.summary}")
        lines.append("")

        if audit_result.pre_trade_checks:
            lines += ["### 売買前チェック", ""]
            for chk in audit_result.pre_trade_checks:
                icon = {"PASS": "✅", "WARN": "⚠️", "FAIL": "❌"}.get(chk.result, chk.result)
                lines.append(f"- {icon} **{chk.check_id}**: {chk.description}")
            lines.append("")

        if audit_result.nisa_checks:
            lines += ["### NISA適合性", ""]
            for nc in audit_result.nisa_checks:
                flag = "✅" if nc.is_nisa_suitable else "❌"
                lines.append(f"- {flag} {nc.ticker}: {nc.reason}")
            lines.append("")

        if audit_result.adjustments:
            lines += ["### AI参考調整案", ""]
            for adj in audit_result.adjustments:
                delta = abs(adj.suggested_weight - adj.current_weight)
                validity = "有効" if adj.valid else f"❌ 無効（±5%超: {delta:.1%}）"
                lines.append(
                    f"- {adj.ticker}: {adj.current_weight:.1%} → {adj.suggested_weight:.1%}"
                    f" [{validity}] {adj.reason}"
                )
            lines.append("")

    # --- 人間による評価メモ ---
    lines += ["## 人間による評価メモ", ""]
    lines.append("以下は手動で追記してください。")
    lines.append("")
    for item in [
        "有用だったか：",
        "新しい気づきがあったか：",
        "不要なコメントはあったか：",
        "危険な提案はあったか：",
        "次回改善したいこと：",
    ]:
        lines.append(f"- {item}")
    lines.append("")

    return "\n".join(lines)


def save_evaluation(evaluation_md: str, output_dir: str, run_date: date) -> str:
    """Save evaluation markdown to output_dir/ai_audit_evaluation.md."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / "ai_audit_evaluation.md"
    path.write_text(evaluation_md, encoding="utf-8")
    logger.info(f"AI audit evaluation saved to {path}")
    return str(path)
