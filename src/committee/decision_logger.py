"""Phase 3.2: Committee Decision Log.

Append-only, versioned JSONL log of each monthly Committee judgment, plus an
optional human decision. Lets us later verify "what did the committee say, and
how did it change over time".

Shadow-mode invariant: this module only *reads* the committee result and final
allocation to record them — it never mutates allocation or returns weights.

Safety:
- Append-only (`a` mode). One write = one JSON line.
- No API keys, prompt text, or secrets are stored (whitelist + redaction pass).
- A single corrupt line never breaks reading the rest of the log.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone, timedelta
from enum import Enum
from pathlib import Path

from src.committee.models import CommitteeResult
from src.logger import logger

COMMITTEE_LOG_SCHEMA_VERSION = "1.0"
DEFAULT_COMMITTEE_LOG_PATH = "logs/committee_decision_log.jsonl"

_JST = timezone(timedelta(hours=9))

# Substrings that must never appear as keys in a persisted log entry.
_SENSITIVE_KEY_SUBSTRINGS = (
    "api_key", "apikey", "secret", "password", "token",
    "prompt", "raw_response", "raw_text", "signing", "credential",
)

# Whitelisted member fields persisted to the log (no prompts / no raw responses).
_MEMBER_FIELDS = (
    "member_id", "display_name", "tier", "verdict", "confidence",
    "rationale", "strongest_support", "strongest_objection", "dissenting_view",
    "key_risks", "required_checks", "next_review_triggers", "action_implication",
)


class HumanCommitteeDecision(str, Enum):
    HOLD = "HOLD"
    BUY = "BUY"
    ADD = "ADD"
    TRIM = "TRIM"
    EXIT = "EXIT"
    WAIT = "WAIT"
    SKIP = "SKIP"


def _now_jst_iso() -> str:
    return datetime.now(_JST).isoformat()


def _is_sensitive_key(key: str) -> bool:
    k = key.lower()
    return any(sub in k for sub in _SENSITIVE_KEY_SUBSTRINGS)


def _redact(obj):
    """Recursively drop any sensitive keys from dicts/lists (defense in depth)."""
    if isinstance(obj, dict):
        return {k: _redact(v) for k, v in obj.items() if not _is_sensitive_key(str(k))}
    if isinstance(obj, list):
        return [_redact(v) for v in obj]
    return obj


def _safe_member(member) -> dict:
    """Project a MemberOutput (or dict) onto the whitelisted, non-sensitive fields."""
    data = member.model_dump(mode="json") if hasattr(member, "model_dump") else dict(member)
    return {f: data.get(f) for f in _MEMBER_FIELDS if f in data}


def build_committee_log_entry(
    *,
    committee_result: CommitteeResult,
    run_date: str,
    strategy_variant: str | None,
    risk_mode: str | None,
    final_allocation: dict[str, float] | None,
    ai_audit_status: str | None,
    run_id: str | None = None,
    human_decision: str | None = None,
    human_note: str | None = None,
) -> dict:
    """Build a versioned, redacted log entry from a CommitteeResult.

    Does not mutate ``final_allocation`` (a rounded copy is stored).
    ``human_decision`` / ``human_note`` default to None.
    """
    members = [_safe_member(m) for m in committee_result.members]
    dissenting_views = {
        m.member_id: m.dissenting_view for m in committee_result.members if m.dissenting_view
    }

    # Validate human_decision against the enum if provided (store the .value).
    hd_value: str | None = None
    if human_decision is not None and str(human_decision) != "":
        hd_value = HumanCommitteeDecision(human_decision).value

    entry = {
        "schema_version": COMMITTEE_LOG_SCHEMA_VERSION,
        "run_id": run_id or uuid.uuid4().hex,
        "timestamp": _now_jst_iso(),
        "date": run_date,
        "strategy_variant": strategy_variant,
        "risk_mode": risk_mode,
        "final_allocation": (
            {t: round(w, 4) for t, w in final_allocation.items()} if final_allocation else {}
        ),
        "ai_audit_status": ai_audit_status,
        "core_committee_verdict": committee_result.core_committee_verdict.value,
        "satellite_committee_verdict": committee_result.satellite_committee_verdict.value,
        "final_committee_verdict": committee_result.final_committee_verdict.value,
        "recommended_action": committee_result.recommended_action,
        # Shadow mode: always False (mirrors the hard-locked result field).
        "allocation_override": bool(committee_result.allocation_override),
        "member_outputs": members,
        "dissenting_views": dissenting_views,
        "next_review_triggers": list(committee_result.next_review_triggers),
        "satellite_activated": committee_result.satellite_activated,
        "satellite_activation_reason": committee_result.satellite_activation_reason,
        "human_decision": hd_value,
        "human_note": human_note if human_note else None,
    }
    return _redact(entry)


def append_committee_decision_log(
    entry: dict,
    log_path: str | Path = DEFAULT_COMMITTEE_LOG_PATH,
) -> str:
    """Append one entry as a single JSON line. Creates the directory if missing.

    Append-only: never truncates or rewrites existing lines.
    """
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    safe_entry = _redact(entry)
    line = json.dumps(safe_entry, ensure_ascii=False)
    with open(path, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    logger.info(f"Committee decision log appended to {path} (run_id={safe_entry.get('run_id')})")
    return str(path)


def read_committee_decision_log(log_path: str | Path = DEFAULT_COMMITTEE_LOG_PATH) -> list[dict]:
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
            logger.warning(f"Skipping corrupt committee log line {i} in {path}")
            continue
    return entries
