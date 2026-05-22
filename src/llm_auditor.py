import json
import re

from src.llm_client import LlmClient
from src.logger import logger
from src.prompt_templates import SYSTEM_PROMPT, build_user_prompt
from src.schemas import LlmAuditResult

_FORBIDDEN_AUTO_TRADE = [
    r"自動売買",
    r"自動注文",
    r"注文を.{0,10}出し",
    r"売買.{0,10}実行",
    r"APIで.{0,10}注文",
    r"楽天証券.{0,10}注文",
]

_FORBIDDEN_NISA_MONTHLY = [
    r"NISA.{0,20}毎月.{0,20}積立",
    r"毎月.{0,20}スイッチング",
    r"NISA.{0,20}スイッチング",
]


def _extract_json(text: str) -> dict:
    stripped = text.strip()
    try:
        return json.loads(stripped)
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

    raise ValueError(f"No valid JSON found in LLM response (first 200 chars): {stripped[:200]}")


def _check_forbidden_patterns(text: str) -> list[str]:
    violations = []
    for pat in _FORBIDDEN_AUTO_TRADE:
        if re.search(pat, text):
            violations.append(f"auto-trade pattern detected: {pat}")
    for pat in _FORBIDDEN_NISA_MONTHLY:
        if re.search(pat, text):
            violations.append(f"NISA monthly-trade pattern detected: {pat}")
    return violations


def _validate_result(result: LlmAuditResult, weights: dict[str, float]) -> list[str]:
    errors: list[str] = []

    if result.apply_adjustment:
        errors.append("apply_adjustment must be false in Phase 2")

    for adj in result.adjustments:
        if adj.ticker not in weights:
            errors.append(f"unknown ticker in adjustments: {adj.ticker}")
        delta = abs(adj.suggested_weight - adj.current_weight)
        if delta > 0.05 + 1e-9:
            errors.append(
                f"adjustment delta exceeds ±5% for {adj.ticker}: {delta:.1%}"
            )
        if adj.suggested_weight < 0:
            errors.append(f"negative suggested_weight for {adj.ticker}")

    violations = _check_forbidden_patterns(result.summary)
    errors.extend(violations)

    return errors


def run_audit(
    context: dict,
    weights: dict[str, float],
    model: str,
    max_retries: int = 2,
) -> LlmAuditResult | None:
    """Run AI audit. Returns None on failure so caller can fall back to Phase 1 results."""
    try:
        client = LlmClient(model=model)
    except ValueError:
        logger.error("AI audit skipped — ANTHROPIC_API_KEY not set")
        return None

    user_prompt = build_user_prompt(context)

    for attempt in range(max_retries + 1):
        try:
            raw = client.complete(system=SYSTEM_PROMPT, user=user_prompt)
        except Exception as e:
            logger.warning(f"AI audit API call failed (attempt {attempt + 1}): {type(e).__name__}")
            if attempt == max_retries:
                logger.error("AI audit failed after all retries — Phase 1 results preserved")
                return None
            continue

        try:
            data = _extract_json(raw)
        except ValueError as e:
            logger.warning(f"AI audit JSON extraction failed (attempt {attempt + 1}): {e}")
            if attempt == max_retries:
                logger.error("AI audit JSON extraction failed — Phase 1 results preserved")
                return None
            continue

        data["apply_adjustment"] = False
        data["raw_response"] = raw

        try:
            result = LlmAuditResult.model_validate(data)
        except Exception as e:
            logger.warning(f"AI audit schema validation failed (attempt {attempt + 1}): {e}")
            if attempt == max_retries:
                logger.error("AI audit validation failed — Phase 1 results preserved")
                return None
            continue

        errors = _validate_result(result, weights)
        if errors:
            logger.warning(f"AI audit post-validation warnings: {errors}")

        logger.info(f"AI audit complete — status={result.status}")
        return result

    return None


def save_audit_result(result: LlmAuditResult, output_dir: str) -> str:
    """Save audit JSON to output_dir/audit_result.json. Returns the path."""
    from pathlib import Path

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / "audit_result.json"
    path.write_text(
        json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info(f"AI audit result saved to {path}")
    return str(path)
