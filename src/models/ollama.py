"""Live local model provider adapter interfacing with Ollama."""

import json
import time
from typing import Any
import httpx

from src.models.base import LLMProvider
from src.schemas import LLMResponse


class OllamaModel(LLMProvider):
    """Local inference provider communicating with Ollama API endpoint."""

    def __init__(
        self,
        model_name: str = "qwen2.5:7b",
        base_url: str = "http://localhost:11434",
        timeout_seconds: float = 30.0,
    ) -> None:
        """Initialize Ollama provider with local server coordinates.

        Args:
            model_name: Model identifier (e.g. qwen2.5:7b).
            base_url: URL where Ollama daemon is reachable.
            timeout_seconds: Request timeout in seconds.
        """
        self.model_name = model_name
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout_seconds

    def generate(
        self,
        messages: list[dict[str, str]],
        tools: list[str] | None = None,
        turn_id: int = 1,
        **kwargs: Any,
    ) -> LLMResponse:
        """Issue prompt to Ollama chat endpoint and parse structured JSON.

        Args:
            messages: Conversation history list.
            tools: Whitelist of allowed tool names.
            turn_id: Current turn number.
            **kwargs: Extra generation parameters.

        Returns:
            LLMResponse: Structured response with intent and extracted slots.
        """
        system_instruction = (
            "You are an errand assistant. Respond ONLY in valid JSON matching this schema:\n"
            '{"intent": "<string>", "slots": {<key>: <value>}, "message": "<user-facing response>", '
            '"tool_calls": [{"name": "<tool_name>", "args": {<tool_args>}}]}\n'
            f"Allowed tool names: {', '.join(tools) if tools else 'None'}."
        )

        formatted_messages = [{"role": "system", "content": system_instruction}]
        for m in messages:
            formatted_messages.append({"role": m.get("role", "user"), "content": m.get("content", "")})

        start = time.time()
        try:
            with httpx.Client(timeout=self.timeout) as client:
                res = client.post(
                    f"{self.base_url}/api/chat",
                    json={
                        "model": self.model_name,
                        "messages": formatted_messages,
                        "format": "json",
                        "stream": False,
                    },
                )
                res.raise_for_status()
                data = res.json()
        except Exception as exc:
            return LLMResponse(
                intent="connection_error",
                slots={},
                message=f"Local Ollama inference failed: {exc}",
                raw_response=str(exc),
                usage={"latency_ms": (time.time() - start) * 1000},
            )

        latency_ms = (time.time() - start) * 1000
        content = data.get("message", {}).get("content", "")
        return self._parse_json_response(content, latency_ms)

    def _parse_json_response(self, content: str, latency_ms: float) -> LLMResponse:
        """Parse raw JSON output from model or handle malformed degradation.

        Args:
            content: Raw model generation string.
            latency_ms: Measured latency in milliseconds.

        Returns:
            LLMResponse: Parsed structured response or malformed sentinel.
        """
        try:
            parsed = json.loads(content)
            return LLMResponse(
                intent=parsed.get("intent", "general_chat"),
                slots=parsed.get("slots", {}),
                message=parsed.get("message", ""),
                tool_calls=parsed.get("tool_calls", []),
                raw_response=content,
                usage={"model": self.model_name, "latency_ms": latency_ms},
            )
        except Exception:
            return LLMResponse(
                intent="malformed_output",
                slots={},
                message="I encountered an issue formatting my response. Let me try again.",
                raw_response=content,
                usage={"model": self.model_name, "latency_ms": latency_ms},
            )
