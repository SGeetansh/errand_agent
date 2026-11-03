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
            "You are an errand assistant helping users arrange home services (plumber, ac_service, electrician).\n"
            "Respond ONLY in valid JSON matching this schema:\n"
            "{\n"
            '  "intent": "<search_providers | clarify | book | cancel | reschedule | general_chat>",\n'
            '  "slots": {\n'
            '    "service": "<plumber | ac_service | electrician | null>",\n'
            '    "area": "<neighborhood name | null>",\n'
            '    "date_range": "<preferred date or window e.g. this week, tomorrow | null>",\n'
            '    "time_preference": "<time window e.g. mornings only, afternoons, not before 10 a.m. | null>",\n'
            '    "budget_max": <number in INR or null>,\n'
            '    "specific_issue": "<task description e.g. kitchen tap leak | null>",\n'
            '    "provider_preference": "<preferred provider or null>"\n'
            "  },\n"
            '  "message": "<user-facing response explaining actions or clarifying missing details>",\n'
            '  "tool_calls": [{"name": "<tool_name>", "args": {<tool_args>}}]\n'
            "}\n"
            f"Allowed tool names: {', '.join(tools) if tools else 'None'}.\n"
            "Only populate non-null values for slot fields that the user explicitly stated or corrected.\n"
            "Required slots before searching or booking are: service, area, date_range. "
            "If any required slot is null, ask the user to provide it. "
            "Never claim a booking has been made or confirmed until a provider is explicitly approved by the user."
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
        prompt_tokens = data.get("prompt_eval_count", 0)
        completion_tokens = data.get("eval_count", 0)
        usage_data = {
            "model": self.model_name,
            "latency_ms": latency_ms,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        }
        content = data.get("message", {}).get("content", "")
        return self._parse_json_response(content, latency_ms, usage_data=usage_data)

    def _parse_json_response(
        self,
        content: str,
        latency_ms: float,
        usage_data: dict[str, Any] | None = None,
    ) -> LLMResponse:
        """Parse raw JSON output from model or handle malformed degradation.

        Args:
            content: Raw model generation string.
            latency_ms: Measured latency in milliseconds.
            usage_data: Optional token and model usage dictionary.

        Returns:
            LLMResponse: Parsed structured response or malformed sentinel.
        """
        usage = usage_data or {"model": self.model_name, "latency_ms": latency_ms}
        try:
            cleaned = content.strip()
            if cleaned.startswith("```"):
                lines = cleaned.splitlines()
                if lines[0].startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].startswith("```"):
                    lines = lines[:-1]
                cleaned = "\n".join(lines).strip()
            parsed = json.loads(cleaned)
            return LLMResponse(
                intent=parsed.get("intent", "general_chat"),
                slots=parsed.get("slots", {}),
                message=parsed.get("message", ""),
                tool_calls=parsed.get("tool_calls", []),
                raw_response=content,
                usage=usage,
            )
        except Exception:
            return LLMResponse(
                intent="malformed_output",
                slots={},
                message="I encountered an issue formatting my response. Let me try again.",
                raw_response=content,
                usage=usage,
            )
