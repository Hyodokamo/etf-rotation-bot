from abc import ABC, abstractmethod


class BaseLlmClient(ABC):
    """Provider-agnostic interface for LLM completion."""

    @abstractmethod
    def complete(self, system: str, user: str, max_tokens: int = 4096) -> str:
        """Send a system + user turn and return the model's text response."""
        ...
