from src.llm.base import BaseLlmClient


class GeminiClient(BaseLlmClient):
    """Placeholder for future Google Gemini support.

    To implement: install google-generativeai, set GEMINI_API_KEY,
    then replace the NotImplementedError bodies with the real SDK calls.
    """

    def __init__(self, model: str, api_key: str | None = None):
        raise NotImplementedError(
            "GeminiClient is not yet implemented. "
            "Use provider='claude' or provider='openai'."
        )

    def complete(self, system: str, user: str, max_tokens: int = 4096) -> str:
        raise NotImplementedError("GeminiClient is not yet implemented.")
