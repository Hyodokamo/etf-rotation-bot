"""Phase 5: Integrated Decision Audit — data models."""
from __future__ import annotations

from pydantic import BaseModel, Field


class HumanDecisionRecord(BaseModel):
    decision: str | None = None
    user_id: str | None = None
    timestamp: str | None = None
    source: str = ""  # committee_log | slack_log | candidate_log


class MonthlyAuditSummary(BaseModel):
    committee_verdict: str | None = None
    core_verdict: str | None = None
    satellite_verdict: str | None = None
    human_decisions: list[HumanDecisionRecord] = Field(default_factory=list)


class CandidateAuditEntry(BaseModel):
    symbol: str
    ai_verdict: str | None = None
    human_decision: str | None = None
    human_user_id: str | None = None
    stability: str | None = None
    alignment: str = "no_human_decision"  # aligned | mild_divergence | divergence | unknown | no_human_decision
    timestamp: str | None = None


class HumanNote(BaseModel):
    scope: str  # monthly_review | candidate_review
    target: str | None = None
    note: str = ""
    user_id: str | None = None
    timestamp: str | None = None


class DecisionAudit(BaseModel):
    month: str
    conclusion: str = ""
    monthly: MonthlyAuditSummary = Field(default_factory=MonthlyAuditSummary)
    candidates: list[CandidateAuditEntry] = Field(default_factory=list)
    divergences: list[CandidateAuditEntry] = Field(default_factory=list)
    unstable_candidates: list[CandidateAuditEntry] = Field(default_factory=list)
    human_notes: list[HumanNote] = Field(default_factory=list)
    next_review_items: list[str] = Field(default_factory=list)

    def to_dict(self) -> dict:
        return self.model_dump(mode="json")
