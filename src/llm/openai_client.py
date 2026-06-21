from __future__ import annotations

import os

import openai

from src.llm.base import BaseLlmClient
from src.logger import logger


class OpenAIClient(BaseLlmClient):
    def __init__(self, model: str, api_key: str | None = None):
        self.model = model
        key = api_key or os.environ.get("OPENAI_API_KEY", "").strip()
        if not key:
            raise ValueError("OPENAI_API_KEY is not set")
        self._client = openai.OpenAI(api_key=key)

    def complete(self, system: str, user: str, max_tokens: int = 4096) -> str:
        try:
            response = self._client.responses.create(
                model=self.model,
                instructions=system,
                input=user,
                max_output_tokens=max_tokens,
            )
            return response.output_text
        except openai.AuthenticationError:
            logger.error("OpenAI authentication failed — check OPENAI_API_KEY")
            raise
        except openai.RateLimitError:
            logger.error("OpenAI rate limit exceeded")
            raise
        except openai.APIConnectionError as e:
            logger.error(f"OpenAI API connection error: {e}")
            raise
        except openai.APITimeoutError:
            logger.error("OpenAI API timeout")
            raise
        except openai.APIError as e:
            logger.error(f"OpenAI API error: {e}")
            raise
