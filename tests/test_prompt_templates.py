import json

from src.prompt_templates import SYSTEM_PROMPT, build_user_prompt


def test_system_prompt_not_empty():
    assert len(SYSTEM_PROMPT) > 100


def test_system_prompt_contains_json_instruction():
    assert "JSON" in SYSTEM_PROMPT


def test_system_prompt_forbids_auto_trade():
    assert "自動売買" in SYSTEM_PROMPT


def test_system_prompt_apply_adjustment_false():
    assert "apply_adjustment" in SYSTEM_PROMPT
    assert "false" in SYSTEM_PROMPT


def test_build_user_prompt_contains_context():
    ctx = {"run_date": "2026-05-01", "allocation": [{"ticker": "VOO", "weight": 0.5}]}
    prompt = build_user_prompt(ctx)
    assert "2026-05-01" in prompt
    assert "VOO" in prompt


def test_build_user_prompt_is_valid_json_embedded():
    ctx = {"run_date": "2026-05-01", "risk_mode": "risk_on", "allocation": []}
    prompt = build_user_prompt(ctx)
    # JSON block should be parseable
    start = prompt.index("```json\n") + len("```json\n")
    end = prompt.index("\n```", start)
    extracted = json.loads(prompt[start:end])
    assert extracted["run_date"] == "2026-05-01"


def test_build_user_prompt_mentions_audit_topics():
    ctx = {}
    prompt = build_user_prompt(ctx)
    assert "集中リスク" in prompt
    assert "NISA" in prompt
