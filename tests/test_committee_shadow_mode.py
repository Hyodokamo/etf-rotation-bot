"""Tests for Phase 3.1: shadow-mode guarantee — committee never changes allocation."""
import copy
import json

from src.committee.models import CommitteeConfig, CommitteeMemberConfig, CommitteeVerdict
from src.committee.runner import run_committee
from src.llm.base import BaseLlmClient

V = CommitteeVerdict


def _cfg() -> CommitteeConfig:
    return CommitteeConfig(
        enabled=True,
        shadow_mode=True,
        llm_call_mode="batch",
        core_committee=[
            CommitteeMemberConfig(member_id="aqr_meb", display_name="AQR", focus="定量"),
            CommitteeMemberConfig(member_id="core_ai_auditor", display_name="Auditor", focus="監査"),
        ],
        satellite_committee=[
            CommitteeMemberConfig(member_id="buffett", display_name="Buffett", focus="長期"),
        ],
    )


class OverrideClaimingClient(BaseLlmClient):
    """An adversarial client that tries to force allocation_override / weight changes."""

    def complete(self, system: str, user: str, max_tokens: int = 4096) -> str:
        arr = []
        for mid in ("aqr_meb", "core_ai_auditor", "buffett"):
            arr.append({
                "member_id": mid,
                "verdict": "REJECT",
                "confidence": 1.0,
                "rationale": "override now",
                "strongest_support": "s",
                "strongest_objection": "o",
                "key_risks": [],
                "required_checks": [],
                "action_implication": "配分を変更せよ",
                # adversarial extra fields that must be ignored:
                "allocation_override": True,
                "new_weights": {"VOO": 1.0},
            })
        return json.dumps(arr, ensure_ascii=False)


def test_allocation_override_locked_even_if_llm_demands():
    res = run_committee({}, _cfg(), OverrideClaimingClient())
    assert res.allocation_override is False


def test_result_carries_no_weights():
    res = run_committee({}, _cfg(), OverrideClaimingClient())
    d = res.to_dict()
    # The result schema has no weight fields at all.
    assert "new_weights" not in d
    assert "weights" not in d
    assert "final_allocation" not in d


def test_final_weights_unchanged_by_committee():
    """Running the committee must not mutate the caller's final allocation."""
    final_weights = {"BND": 0.376, "VOO": 0.346, "VTV": 0.151, "QQQM": 0.127}
    snapshot = copy.deepcopy(final_weights)

    context = {"allocation": [{"ticker": t, "weight": w} for t, w in final_weights.items()]}
    res = run_committee(context, _cfg(), OverrideClaimingClient())

    # committee produced a (REJECT) opinion but the allocation dict is untouched
    assert final_weights == snapshot
    assert res.final_committee_verdict == V.REJECT
    assert res.allocation_override is False


def test_shadow_mode_flag_propagated():
    res = run_committee({}, _cfg(), OverrideClaimingClient())
    assert res.shadow_mode is True
