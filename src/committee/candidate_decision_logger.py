"""Phase 3.6: Candidate Review Decision Log.

Append-only, versioned JSONL log of each candidate review (one line per
candidate), plus an optional human decision. Lets us track a candidate's verdict
history over time and later detect LLM verdict drift (e.g. GRID swinging between
WAIT_FOR_BETTER_ENTRY and REJECT_FOR_NOW across runs).

Completely separate from the Monthly Review committee decision log
(`logs/committee_decision_log.jsonl`).

Shadow / advisory invariants preserved: only reads the review result; never
changes allocation, never computes order quantities. `intended_amount_jpy` is
stored as a consideration amount, never converted to shares/units.

Safety: append-only, no API keys / prompts / raw responses (whitelist member
fields are already safe; a recursive redaction pass is applied as defense in
depth), and a single corrupt line never breaks reading the rest.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from enum import Enum
from pathlib import Path

from src.committee.candidate_review import CandidateReviewResult
from src.logger import logger

CANDIDATE_LOG_SCHEMA_VERSION = "1.0"
DEFAULT_CANDIDATE_LOG_PATH = "logs/candidate_review_log.jsonl"

_JST = timezone(timedelta(hours=9))

_SENSITIVE_KEY_SUBSTRINGS = (
    "api_key", "apikey", "secret", "password", "token",
    "prompt", "raw_response", "raw_text", "signing", "credential",
)


class HumanCandidateDecision(str, Enum):
    WATCHLIST = "WATCHLIST"
    SMALL_TEST_BUY_CANDIDATE = "SMALL_TEST_BUY_CANDIDATE"
    WAIT = "WAIT"
    REJECT = "REJECT"
    RE_REVIEW = "RE_REVIEW"
    SKIP = "SKIP"


def _now_jst_iso() -> str:
    return datetime.now(_JST).isoformat()


def _is_sensitive_key(key: str) -> bool:
    k = key.lower()
    return any(sub in k for sub in _SENSITIVE_KEY_SUBSTRINGS)


def _redact(obj):
    if isinstance(obj, dict):
        return {k: _redact(v) for k, v in obj.items() if not _is_sensitive_key(str(k))}
    if isinstance(obj, list):
        return [_redact(v) for v in obj]
    return obj


def build_candidate_log_entry(
    result: CandidateReviewResult,
    *,
    human_decision: str | None = None,
    human_note: str | None = None,
) -> dict:
    """Build a versioned, redacted log entry from a CandidateReviewResult.

    ``human_decision`` / ``human_note`` default to None.
    """
    c = result.candidate or {}

    hd_value: str | None = None
    if human_decision is not None and str(human_decision) != "":
        hd_value = HumanCandidateDecision(human_decision).value

    entry = {
        "schema_version": CANDIDATE_LOG_SCHEMA_VERSION,
        "review_id": result.review_id,
        "timestamp": _now_jst_iso(),
        "review_date": result.review_date,
        "candidate_symbol": c.get("symbol"),
        "candidate_name": c.get("name"),
        "asset_type": c.get("asset_type"),
        "theme": c.get("theme"),
        "candidate_action": c.get("candidate_action"),
        # consideration amount only — never converted to shares/units
        "intended_amount_jpy": c.get("intended_amount_jpy_consideration_only"),
        "account": c.get("account"),
        "candidate_verdict": result.candidate_verdict.value,
        "confidence": result.confidence,
        "strongest_buy_thesis": result.strongest_buy_thesis,
        "strongest_rejection_thesis": result.strongest_rejection_thesis,
        "key_risks": list(result.key_risks),
        "required_checks": list(result.required_checks),
        "entry_conditions": list(result.entry_conditions),
        "invalidation_conditions": list(result.invalidation_conditions),
        "sizing_note": result.sizing_note,
        "final_advisory": result.final_advisory,
        "member_outputs": result.member_outputs,
        # advisory mode: always False (mirrors the hard-locked result field)
        "allocation_override": bool(result.allocation_override),
        "human_decision": hd_value,
        "human_note": human_note if human_note else None,
    }
    return _redact(entry)


def append_candidate_decision_log(
    entry: dict,
    log_path: str | Path = DEFAULT_CANDIDATE_LOG_PATH,
) -> str:
    """Append one entry as a single JSON line. Creates the directory if missing."""
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    safe_entry = _redact(entry)
    line = json.dumps(safe_entry, ensure_ascii=False)
    with open(path, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    logger.info(
        f"Candidate decision log appended to {path} "
        f"(symbol={safe_entry.get('candidate_symbol')}, verdict={safe_entry.get('candidate_verdict')})"
    )
    return str(path)


def read_candidate_decision_log(
    log_path: str | Path = DEFAULT_CANDIDATE_LOG_PATH,
) -> list[dict]:
    """Read all valid entries. Corrupt/partial lines are skipped, not fatal."""
    path = Path(log_path)
    if not path.exists():
        return []
    entries: list[dict] = []
    for i, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        raw = raw.strip()
        if not raw:
            continue
        try:
            entries.append(json.loads(raw))
        except json.JSONDecodeError:
            logger.warning(f"Skipping corrupt candidate log line {i} in {path}")
            continue
    return entries
