"""Deterministic scripted model provider for offline automated evaluations."""

import json
from typing import Any

from src.models.base import LLMProvider
from src.schemas import LLMResponse


class ScriptedModel(LLMProvider):
    """Replays deterministic responses based on turn index or input substring matching."""

    def __init__(
        self,
        responses: list[dict[str, Any]] | None = None,
        model_id: str = "scripted_default",
    ) -> None:
        """Initialize ScriptedModel with response rules.

        Args:
            responses: List of rule dicts matching turn or input_contains.
            model_id: Identifier name for scripted scenario.
        """
        self.model_id = model_id
        self.responses = responses or []

    @classmethod
    def from_file(cls, json_path: str) -> "ScriptedModel":
        """Instantiate ScriptedModel from a scenario JSON file.

        Args:
            json_path: Path to scripted response JSON file.

        Returns:
            ScriptedModel: Loaded scripted model instance.
        """
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls(
            responses=data.get("responses", []),
            model_id=data.get("model_id", "scripted_from_file"),
        )

    def generate(
        self,
        messages: list[dict[str, str]],
        tools: list[str] | None = None,
        turn_id: int = 1,
        **kwargs: Any,
    ) -> LLMResponse:
        """Generate response matching the current turn or user input substring.

        Args:
            messages: Conversation messages list.
            tools: Allowed tools list.
            turn_id: Current turn number.
            **kwargs: Extra parameters.

        Returns:
            LLMResponse: Matched scripted response or fallback.
        """
        user_text = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                user_text = m.get("content", "").lower()
                break

        for rule in self.responses:
            matches_turn = rule.get("turn") is None or rule.get("turn") == turn_id
            pattern = rule.get("input_contains", "").lower()
            matches_text = not pattern or pattern in user_text

            if matches_turn and matches_text:
                resp_data = rule["response"]
                return LLMResponse(
                    intent=resp_data.get("intent", "general_chat"),
                    slots=resp_data.get("slots", {}),
                    message=resp_data.get("message", ""),
                    tool_calls=resp_data.get("tool_calls", []),
                    raw_response=json.dumps(resp_data),
                    usage={"model": self.model_id, "prompt_tokens": 10, "completion_tokens": 10},
                )

        # Fallback response for unscripted input
        return LLMResponse(
            intent="unknown",
            slots={},
            message="I'm sorry, I did not understand that request. Could you please clarify?",
            raw_response="fallback_unmatched",
            usage={"model": self.model_id, "prompt_tokens": 5, "completion_tokens": 5},
        )
