"""Tests for Phase 3.1: committee runner (batch + per_member, fallbacks)."""
import json

import pytest

from src.committee.models import (
    CommitteeConfig,
    CommitteeMemberConfig,
    CommitteeTier,
    CommitteeVerdict,
)
from src.committee.runner import (
    load_committee_config,
    run_committee,
    save_committee_result,
)
from src.llm.base import BaseLlmClient

V = CommitteeVerdict


# ── fixtures ──────────────────────────────────────────────────────────────────

def _cfg(mode="batch") -> CommitteeConfig:
    return CommitteeConfig(
        enabled=True,
        shadow_mode=True,
        llm_call_mode=mode,
        core_committee=[
            CommitteeMemberConfig(member_id="aqr_meb", display_name="AQR", focus="定量"),
            CommitteeMemberConfig(member_id="howard_marks", display_name="Marks", focus="サイクル"),
            CommitteeMemberConfig(member_id="rob_arnott", display_name="Arnott", focus="バリュエーション"),
            CommitteeMemberConfig(member_id="core_ai_auditor", display_name="Auditor", focus="監査"),
        ],
        satellite_committee=[
            CommitteeMemberConfig(member_id="buffett", display_name="Buffett", focus="長期"),
            CommitteeMemberConfig(member_id="paul_tudor_jones", display_name="PTJ", focus="防御"),
            CommitteeMemberConfig(member_id="druckenmiller", display_name="Druck", focus="大局"),
        ],
    )


def _member_json(member_id, verdict="PASS", extra=None):
    d = {
        "member_id": member_id,
        "verdict": verdict,
        "confidence": 0.7,
        "rationale": f"{member_id} rationale",
        "strongest_support": "support",
        "strongest_objection": "objection",
        "key_risks": ["risk1"],
        "required_checks": [f"check_{member_id}"],
        "action_implication": "advisory only",
    }
    if extra:
        d.update(extra)
    return d


class FakeBatchClient(BaseLlmClient):
    def __init__(self, verdict_map=None, raw=None):
        self.verdict_map = verdict_map or {}
        self.raw = raw
        self.calls = 0

    def complete(self, system: str, user: str, max_tokens: int = 4096) -> str:
        self.calls += 1
        if self.raw is not None:
            return self.raw
        ids = ["aqr_meb", "howard_marks", "rob_arnott", "core_ai_auditor",
               "buffett", "paul_tudor_jones", "druckenmiller"]
        arr = [_member_json(i, self.verdict_map.get(i, "PASS")) for i in ids]
        return json.dumps(arr, ensure_ascii=False)


class FakePerMemberClient(BaseLlmClient):
    def __init__(self, verdict_map=None):
        self.verdict_map = verdict_map or {}
        self.calls = 0

    def complete(self, system: str, user: str, max_tokens: int = 4096) -> str:
        self.calls += 1
        # member_id appears in the user prompt; find which member is asked
        ids = ["aqr_meb", "howard_marks", "rob_arnott", "core_ai_auditor",
               "buffett", "paul_tudor_jones", "druckenmiller"]
        mid = next((i for i in ids if i in user), "aqr_meb")
        return json.dumps(_member_json(mid, self.verdict_map.get(mid, "PASS")))


class FailingClient(BaseLlmClient):
    def complete(self, system: str, user: str, max_tokens: int = 4096) -> str:
        raise RuntimeError("boom")


# ── batch mode ────────────────────────────────────────────────────────────────

def test_batch_parses_all_seven_members():
    res = run_committee({}, _cfg("batch"), FakeBatchClient())
    assert len(res.members) == 7
    assert {m.member_id for m in res.members} == {
        "aqr_meb", "howard_marks", "rob_arnott", "core_ai_auditor",
        "buffett", "paul_tudor_jones", "druckenmiller",
    }


def test_batch_single_llm_call():
    client = FakeBatchClient()
    run_committee({}, _cfg("batch"), client)
    assert client.calls == 1


def test_batch_core_satellite_separated():
    client = FakeBatchClient(verdict_map={"howard_marks": "REJECT"})
    res = run_committee({}, _cfg("batch"), client)
    # REJECT is in core -> core REJECT, satellite all PASS
    assert res.core_committee_verdict == V.REJECT
    assert res.satellite_committee_verdict == V.PASS
    assert res.final_committee_verdict == V.REJECT


def test_batch_member_id_forced_from_config():
    # LLM returns wrong/extra member_id; runner forces config ids and fills missing
    bad = json.dumps([_member_json("totally_unknown", "PASS")])
    res = run_committee({}, _cfg("batch"), FakeBatchClient(raw=bad))
    # unknown ignored, all 7 config members present, all fallback INSUFFICIENT
    assert len(res.members) == 7
    assert all(m.verdict == V.INSUFFICIENT_DATA for m in res.members)


# ── malformed / failure handling ─────────────────────────────────────────────

def test_malformed_json_falls_back_to_insufficient():
    res = run_committee({}, _cfg("batch"), FakeBatchClient(raw="not json at all"))
    assert len(res.members) == 7
    assert res.final_committee_verdict == V.INSUFFICIENT_DATA


def test_failing_client_falls_back():
    res = run_committee({}, _cfg("batch"), FailingClient())
    assert res.final_committee_verdict == V.INSUFFICIENT_DATA
    assert all(m.verdict == V.INSUFFICIENT_DATA for m in res.members)


def test_none_client_all_insufficient():
    res = run_committee({}, _cfg("batch"), None)
    assert len(res.members) == 7
    assert res.final_committee_verdict == V.INSUFFICIENT_DATA
    assert res.allocation_override is False


def test_no_members_insufficient():
    cfg = CommitteeConfig(enabled=True, core_committee=[], satellite_committee=[])
    res = run_committee({}, cfg, FakeBatchClient())
    assert res.final_committee_verdict == V.INSUFFICIENT_DATA
    assert res.members == []


# ── per_member mode ──────────────────────────────────────────────────────────

def test_per_member_one_call_per_member():
    client = FakePerMemberClient()
    res = run_committee({}, _cfg("per_member"), client)
    assert client.calls == 7
    assert len(res.members) == 7
    assert res.llm_call_mode == "per_member"


def test_per_member_verdicts_aggregated():
    client = FakePerMemberClient(verdict_map={"buffett": "WATCH"})
    res = run_committee({}, _cfg("per_member"), client)
    assert res.satellite_committee_verdict == V.WATCH


# ── forbidden auto-trade scrub ───────────────────────────────────────────────

def test_forbidden_auto_trade_text_scrubbed():
    bad = json.dumps([
        _member_json("aqr_meb", "PASS", extra={"action_implication": "自動売買を実行すべき"})
    ])
    res = run_committee({}, _cfg("batch"), FakeBatchClient(raw=bad))
    aqr = next(m for m in res.members if m.member_id == "aqr_meb")
    assert "自動売買" not in aqr.action_implication
    assert "auto_trade_language_detected" in aqr.key_risks


# ── next_review_triggers / output ────────────────────────────────────────────

def test_next_review_triggers_deduped():
    res = run_committee({}, _cfg("batch"), FakeBatchClient())
    # each member adds check_<id>; all unique -> 7 triggers
    assert len(res.next_review_triggers) == 7
    assert len(set(res.next_review_triggers)) == len(res.next_review_triggers)


def test_save_committee_result(tmp_path):
    res = run_committee({}, _cfg("batch"), FakeBatchClient())
    path = save_committee_result(res, str(tmp_path))
    data = json.loads(open(path, encoding="utf-8").read())
    assert data["allocation_override"] is False
    assert data["shadow_mode"] is True
    assert len(data["members"]) == 7


# ── config loading ───────────────────────────────────────────────────────────

def test_load_committee_config_from_repo():
    cfg = load_committee_config()
    assert cfg.enabled is True
    assert cfg.shadow_mode is True
    assert cfg.llm_call_mode == "batch"
    ids = {m.member_id for m in cfg.core_committee} | {m.member_id for m in cfg.satellite_committee}
    assert ids == {
        "aqr_meb", "howard_marks", "rob_arnott", "core_ai_auditor",
        "buffett", "paul_tudor_jones", "druckenmiller",
    }


def test_load_committee_config_missing_file_disabled(tmp_path):
    cfg = load_committee_config(tmp_path / "nope.yaml")
    assert cfg.enabled is False
