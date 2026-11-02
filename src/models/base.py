"""Abstract base class for all LLM model providers."""

from abc import ABC, abstractmethod
from typing import Any

from src.schemas import LLMResponse


class LLMProvider(ABC):
    """Abstract base provider interface for language model interactions."""

    @abstractmethod
    def generate(
        self,
        messages: list[dict[str, str]],
        tools: list[str] | None = None,
        turn_id: int = 1,
        **kwargs: Any,
    ) -> LLMResponse:
        """Generate a structured response from the model.

        Args:
            messages: Conversation messages list of {'role': ..., 'content': ...}.
            tools: Whitelist of allowed tool names in current state.
            turn_id: Current conversation turn index.
            **kwargs: Extra model generation parameters.

        Returns:
            LLMResponse: Structured response including intent, slots, and message.
        """
        pass
