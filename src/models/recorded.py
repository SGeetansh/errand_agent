"""Recorded model provider replaying historical LLM execution traces."""

import json
from typing import Any

from src.models.base import LLMProvider
from src.schemas import LLMResponse


class RecordedModel(LLMProvider):
    """Replays pre-recorded model responses from an execution trace."""

    def __init__(
        self,
        recordings: list[dict[str, Any]] | None = None,
        model_id: str = "recorded_trace",
    ) -> None:
        """Initialize RecordedModel with captured trace records.

        Args:
            recordings: List of trace records or model outputs indexed by turn.
            model_id: Identifier name for recorded session.
        """
        self.model_id = model_id
        self._recordings = recordings or []
        self._playback_index = 0

    @classmethod
    def from_trace_file(cls, trace_path: str) -> "RecordedModel":
        """Instantiate RecordedModel from a JSONL trace log file.

        Args:
            trace_path: Path to .jsonl trace file.

        Returns:
            RecordedModel: Model initialized with model_response events from trace.
        """
        records: list[dict[str, Any]] = []
        with open(trace_path, "r", encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if not stripped:
                    continue
                entry = json.loads(stripped)
                if entry.get("event_type") == "model_response":
                    records.append(entry)

        return cls(recordings=records, model_id=f"replay_{trace_path}")

    def generate(
        self,
        messages: list[dict[str, str]],
        tools: list[str] | None = None,
        turn_id: int = 1,
        **kwargs: Any,
    ) -> LLMResponse:
        """Replay next recorded response corresponding to turn or sequential playback.

        Args:
            messages: Conversation messages.
            tools: Allowed tools list.
            turn_id: Target turn number.
            **kwargs: Extra parameters.

        Returns:
            LLMResponse: Replayed response from trace.
        """
        # First attempt turn_id match
        for rec in self._recordings:
            if rec.get("turn_id") == turn_id:
                return self._build_response(rec)

        # Fallback to sequential playback
        if self._playback_index < len(self._recordings):
            rec = self._recordings[self._playback_index]
            self._playback_index += 1
            return self._build_response(rec)

        return LLMResponse(
            intent="unknown",
            slots={},
            message="No further recorded responses available.",
            raw_response="exhausted_recording",
        )

    def _build_response(self, rec: dict[str, Any]) -> LLMResponse:
        """Construct LLMResponse from raw trace entry.

        Args:
            rec: Recorded trace dictionary.

        Returns:
            LLMResponse: Formatted response object.
        """
        return LLMResponse(
            intent=rec.get("intent", "general_chat"),
            slots=rec.get("slots", {}),
            message=rec.get("message", ""),
            tool_calls=rec.get("tool_calls", []),
            raw_response=rec.get("raw_response", ""),
            usage=rec.get("usage", {}),
        )
