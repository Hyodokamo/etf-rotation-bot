from __future__ import annotations

import os

import anthropic

from src.llm.base import BaseLlmClient
from src.logger import logger


class ClaudeClient(BaseLlmClient):
    def __init__(self, model: str, api_key: str | None = None):
        self.model = model
        key = api_key or os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if not key:
            raise ValueError("ANTHROPIC_API_KEY is not set")
        self._client = anthropic.Anthropic(api_key=key)

    def complete(self, system: str, user: str, max_tokens: int = 4096) -> str:
        try:
            response = self._client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
            return response.content[0].text
        except anthropic.AuthenticationError:
            logger.error("Anthropic authentication failed — check ANTHROPIC_API_KEY")
            raise
        except anthropic.RateLimitError:
            logger.error("Anthropic rate limit exceeded")
            raise
        except anthropic.APIConnectionError as e:
            logger.error(f"Anthropic API connection error: {e}")
            raise
        except anthropic.APITimeoutError:
            logger.error("Anthropic API timeout")
            raise
        except anthropic.APIError as e:
            logger.error(f"Anthropic API error: {e}")
            raise
