from __future__ import annotations

# Backward-compatibility shim.
# New code should import from src.llm directly.
from src.llm.claude_client import ClaudeClient as LlmClient

__all__ = ["LlmClient"]
