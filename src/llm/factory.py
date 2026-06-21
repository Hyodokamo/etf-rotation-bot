from __future__ import annotations

from src.llm.base import BaseLlmClient

_SUPPORTED = ("claude", "openai", "gemini")


def create_client(provider: str, model: str, api_key: str | None = None) -> BaseLlmClient:
    """Instantiate the correct LLM client for the given provider.

    Imports are lazy so that missing optional SDKs (openai, google-generativeai)
    only cause errors when actually selected, not at module load time.
    """
    if provider == "claude":
        from src.llm.claude_client import ClaudeClient
        return ClaudeClient(model=model, api_key=api_key)

    if provider == "openai":
        from src.llm.openai_client import OpenAIClient
        return OpenAIClient(model=model, api_key=api_key)

    if provider == "gemini":
        from src.llm.gemini_client import GeminiClient
        return GeminiClient(model=model, api_key=api_key)

    raise ValueError(
        f"Unknown LLM provider: {provider!r}. Supported providers: {', '.join(_SUPPORTED)}"
    )
