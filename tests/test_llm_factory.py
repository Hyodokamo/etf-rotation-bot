"""Tests for src/llm/ provider infrastructure (no real API calls)."""
from unittest.mock import MagicMock, patch

import pytest

from src.llm.base import BaseLlmClient
from src.llm.factory import create_client


# ──────────────────────────────────────────────
# BaseLlmClient
# ──────────────────────────────────────────────


def test_base_client_is_abstract():
    with pytest.raises(TypeError):
        BaseLlmClient()


def test_concrete_client_must_implement_complete():
    class Incomplete(BaseLlmClient):
        pass

    with pytest.raises(TypeError):
        Incomplete()


# ──────────────────────────────────────────────
# factory — unknown / unsupported providers
# ──────────────────────────────────────────────


def test_factory_unknown_provider_raises():
    with pytest.raises(ValueError, match="Unknown LLM provider"):
        create_client(provider="bogus", model="x")


def test_factory_gemini_raises_not_implemented():
    with pytest.raises(NotImplementedError):
        create_client(provider="gemini", model="gemini-1.5-pro")


# ──────────────────────────────────────────────
# factory — Claude (mocked constructor)
# ──────────────────────────────────────────────


def test_factory_claude_returns_base_client():
    with patch("src.llm.claude_client.anthropic.Anthropic"):
        import os
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-test"}):
            client = create_client(provider="claude", model="claude-3-5-sonnet-latest")
    assert isinstance(client, BaseLlmClient)


def test_factory_claude_missing_api_key_raises():
    import os
    with patch.dict(os.environ, {}, clear=True):
        # Ensure ANTHROPIC_API_KEY is absent
        os.environ.pop("ANTHROPIC_API_KEY", None)
        with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
            create_client(provider="claude", model="claude-3-5-sonnet-latest")


# ──────────────────────────────────────────────
# factory — OpenAI (mocked constructor)
# ──────────────────────────────────────────────


def test_factory_openai_returns_base_client():
    with patch("src.llm.openai_client.openai.OpenAI"):
        import os
        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"}):
            client = create_client(provider="openai", model="gpt-4o")
    assert isinstance(client, BaseLlmClient)


def test_factory_openai_missing_api_key_raises():
    import os
    with patch.dict(os.environ, {}, clear=True):
        os.environ.pop("OPENAI_API_KEY", None)
        with pytest.raises(ValueError, match="OPENAI_API_KEY"):
            create_client(provider="openai", model="gpt-4o")


# ──────────────────────────────────────────────
# ClaudeClient.complete() — happy path
# ──────────────────────────────────────────────


def test_claude_client_complete_calls_sdk():
    from src.llm.claude_client import ClaudeClient

    mock_sdk = MagicMock()
    mock_sdk.messages.create.return_value.content = [MagicMock(text='{"status":"PASS"}')]

    with patch("src.llm.claude_client.anthropic.Anthropic", return_value=mock_sdk):
        client = ClaudeClient(model="claude-3-5-sonnet-latest", api_key="sk-ant-test")
        result = client.complete(system="sys", user="usr")

    assert result == '{"status":"PASS"}'
    mock_sdk.messages.create.assert_called_once()


# ──────────────────────────────────────────────
# OpenAIClient.complete() — Responses API
# ──────────────────────────────────────────────


def test_openai_client_complete_calls_responses_api():
    """OpenAIClient must use client.responses.create (Responses API)."""
    from src.llm.openai_client import OpenAIClient

    mock_sdk = MagicMock()
    mock_sdk.responses.create.return_value.output_text = '{"status":"PASS"}'

    with patch("src.llm.openai_client.openai.OpenAI", return_value=mock_sdk):
        client = OpenAIClient(model="gpt-5.4-mini", api_key="sk-test")
        result = client.complete(system="sys", user="usr")

    assert result == '{"status":"PASS"}'
    mock_sdk.responses.create.assert_called_once()
    mock_sdk.chat.completions.create.assert_not_called()


def test_openai_responses_api_uses_max_output_tokens():
    """Responses API must use max_output_tokens — never max_tokens or max_completion_tokens."""
    from src.llm.openai_client import OpenAIClient

    mock_sdk = MagicMock()
    mock_sdk.responses.create.return_value.output_text = '{"status":"PASS"}'

    with patch("src.llm.openai_client.openai.OpenAI", return_value=mock_sdk):
        client = OpenAIClient(model="gpt-5.4-mini", api_key="sk-test")
        client.complete(system="sys", user="usr", max_tokens=2048)

    _, kwargs = mock_sdk.responses.create.call_args
    assert kwargs.get("max_output_tokens") == 2048
    assert "max_tokens" not in kwargs
    assert "max_completion_tokens" not in kwargs


@pytest.mark.parametrize("model", [
    "gpt-5.4-mini", "gpt-4.1", "o1", "o3-mini", "o4-mini", "gpt-4o",
])
def test_openai_no_model_sends_max_tokens(model):
    """All models must NOT use max_tokens (Responses API uses max_output_tokens)."""
    from src.llm.openai_client import OpenAIClient

    mock_sdk = MagicMock()
    mock_sdk.responses.create.return_value.output_text = '{"status":"PASS"}'

    with patch("src.llm.openai_client.openai.OpenAI", return_value=mock_sdk):
        client = OpenAIClient(model=model, api_key="sk-test")
        client.complete(system="sys", user="usr")

    _, kwargs = mock_sdk.responses.create.call_args
    assert "max_tokens" not in kwargs, f"max_tokens was sent for model {model!r}"


# ──────────────────────────────────────────────
# run_audit works with any BaseLlmClient
# ──────────────────────────────────────────────


def test_run_audit_provider_agnostic():
    """run_audit() accepts any BaseLlmClient implementation."""
    import json
    from src.llm_auditor import run_audit

    class FakeClient(BaseLlmClient):
        def complete(self, system, user, max_tokens=4096):
            return json.dumps({
                "status": "PASS",
                "summary": "テスト用監査結果",
                "apply_adjustment": False,
            })

    result = run_audit(
        context={"run_date": "2026-05-24", "allocation": []},
        weights={"VOO": 0.5, "TLT": 0.5},
        client=FakeClient(),
    )

    assert result is not None
    assert result.status.value == "PASS"
    assert result.apply_adjustment is False
