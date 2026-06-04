"""Phase 3.1: Investment Committee OS — data models.

Two-tier advisory committee (Core / Satellite). Shadow mode only:
``allocation_override`` is hard-locked to ``False`` and never influences the
final quant allocation.
"""
from __future__ import annotations

from enum import Enum
from typing import Iterable, Literal

from pydantic import BaseModel, Field, field_validator


class CommitteeVerdict(str, Enum):
    PASS = "PASS"
    PASS_WITH_CAUTION = "PASS_WITH_CAUTION"
    WATCH = "WATCH"
    REJECT = "REJECT"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class CommitteeTier(str, Enum):
    CORE = "core"
    SATELLITE = "satellite"


# Severity ranking used for aggregation. Higher = more blocking.
# Aggregation is severity-based (worst-case dominant), NOT majority vote.
_SEVERITY: dict[CommitteeVerdict, int] = {
    CommitteeVerdict.PASS: 0,
    CommitteeVerdict.PASS_WITH_CAUTION: 1,
    CommitteeVerdict.WATCH: 2,
    CommitteeVerdict.INSUFFICIENT_DATA: 3,
    CommitteeVerdict.REJECT: 4,
}


def aggregate_verdict(verdicts: Iterable[CommitteeVerdict]) -> CommitteeVerdict:
    """Combine member verdicts into a single verdict.

    Severity-based, explicitly NOT a majority vote: a single REJECT dominates a
    field of PASS. Rules (in order):
      - no verdicts                       -> INSUFFICIENT_DATA
      - all INSUFFICIENT_DATA             -> INSUFFICIENT_DATA
      - any REJECT                        -> REJECT
      - any WATCH                         -> WATCH
      - any PASS_WITH_CAUTION             -> PASS_WITH_CAUTION
      - some (not all) INSUFFICIENT_DATA  -> PASS_WITH_CAUTION (data gap = caution)
      - otherwise                         -> PASS
    """
    vs = [CommitteeVerdict(v) for v in verdicts]
    if not vs:
        return CommitteeVerdict.INSUFFICIENT_DATA
    if all(v == CommitteeVerdict.INSUFFICIENT_DATA for v in vs):
        return CommitteeVerdict.INSUFFICIENT_DATA
    if any(v == CommitteeVerdict.REJECT for v in vs):
        return CommitteeVerdict.REJECT
    if any(v == CommitteeVerdict.WATCH for v in vs):
        return CommitteeVerdict.WATCH
    if any(v == CommitteeVerdict.PASS_WITH_CAUTION for v in vs):
        return CommitteeVerdict.PASS_WITH_CAUTION
    if any(v == CommitteeVerdict.INSUFFICIENT_DATA for v in vs):
        return CommitteeVerdict.PASS_WITH_CAUTION
    return CommitteeVerdict.PASS


class MemberOutput(BaseModel):
    """A single committee member's independent evaluation."""

    member_id: str
    display_name: str = ""
    tier: CommitteeTier
    verdict: CommitteeVerdict
    confidence: float = 0.0
    rationale: str = ""
    strongest_support: str = ""    # 最も支持する理由
    strongest_objection: str = ""  # 最も反対する理由
    key_risks: list[str] = Field(default_factory=list)
    required_checks: list[str] = Field(default_factory=list)
    action_implication: str = ""
    # What this investor-style framework would strongly disagree with (concrete).
    dissenting_view: str = ""
    # Per-member concrete conditions that would change the verdict next time.
    next_review_triggers: list[str] = Field(default_factory=list)

    @field_validator("confidence", mode="before")
    @classmethod
    def _clamp_confidence(cls, v) -> float:
        try:
            f = float(v)
        except (TypeError, ValueError):
            return 0.0
        return max(0.0, min(1.0, f))


class CommitteeMemberConfig(BaseModel):
    member_id: str
    display_name: str
    focus: str


class CommitteeConfig(BaseModel):
    enabled: bool = True
    shadow_mode: bool = True
    allocation_override_allowed: bool = False
    max_tokens_per_member: int = 1200
    llm_call_mode: Literal["batch", "per_member"] = "batch"
    # always: Satellite always runs. conditional: Satellite runs only when
    # activation triggers are met (theme/growth exposure, AI audit caution, etc.).
    satellite_activation: Literal["always", "conditional"] = "always"
    core_committee: list[CommitteeMemberConfig] = Field(default_factory=list)
    satellite_committee: list[CommitteeMemberConfig] = Field(default_factory=list)

    def all_members(self) -> list[tuple[CommitteeMemberConfig, CommitteeTier]]:
        return (
            [(m, CommitteeTier.CORE) for m in self.core_committee]
            + [(m, CommitteeTier.SATELLITE) for m in self.satellite_committee]
        )


class CommitteeResult(BaseModel):
    core_committee_verdict: CommitteeVerdict
    satellite_committee_verdict: CommitteeVerdict
    final_committee_verdict: CommitteeVerdict
    recommended_action: str
    # Shadow mode: hard-locked to False regardless of any LLM output.
    allocation_override: bool = False
    summary: str = ""
    next_review_triggers: list[str] = Field(default_factory=list)
    members: list[MemberOutput] = Field(default_factory=list)
    shadow_mode: bool = True
    llm_call_mode: str = "batch"
    # False when Satellite Committee was not activated (conditional mode).
    satellite_activated: bool = True
    satellite_activation_reason: str = ""

    @field_validator("allocation_override")
    @classmethod
    def _force_no_override(cls, v: bool) -> bool:
        # Phase 3.1 is shadow mode: the committee must never override allocation.
        return False

    def to_dict(self) -> dict:
        return self.model_dump(mode="json")
