"""Structured JSONL execution tracer with automatic PII and secret redaction."""

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import os
import re
from typing import Any

PII_PATTERNS = [
    (re.compile(r"\b\d{10}\b"), "[PHONE_REDACTED]"),
    (re.compile(r"\b[\w.-]+@[\w.-]+\.\w+\b"), "[EMAIL_REDACTED]"),
    (re.compile(r"sk-[a-zA-Z0-9]{32,}"), "[API_KEY_REDACTED]"),
]


def redact_pii(text: str) -> str:
    """Scrub sensitive PII patterns such as phone numbers and API keys.

    Args:
        text: Input string.

    Returns:
        str: Scrubbed string.
    """
    scrubbed = text
    for pattern, replacement in PII_PATTERNS:
        scrubbed = pattern.sub(replacement, scrubbed)
    return scrubbed


def _redact_structure(item: Any) -> Any:
    """Recursively redact strings in dictionaries and lists.

    Args:
        item: Any data structure.

    Returns:
        Any: Structure with PII-redacted string values.
    """
    if isinstance(item, str):
        return redact_pii(item)
    if isinstance(item, dict):
        return {k: _redact_structure(v) for k, v in item.items()}
    if isinstance(item, list):
        return [_redact_structure(sub) for sub in item]
    return item


@dataclass
class TraceEntry:
    """A single structured observability event in the JSONL trace log.

    Attributes:
        timestamp: ISO 8601 timestamp.
        turn_id: Current turn number.
        event_type: One of 'state_transition', 'model_call', 'model_response',
            'tool_call', 'tool_result', 'approval_check', 'wal_write', 'error',
            'user_input', 'agent_output', 'context_manifest', 'budget_status'.
        from_state: Previous state for transition events.
        to_state: Target state for transition events.
        trigger: Trigger reason for transition.
        model_name: Name of model invoked.
        prompt_tokens: Prompt token count.
        completion_tokens: Output token count.
        latency_ms: Execution duration in milliseconds.
        estimated_cost_usd: Estimated cost.
        tool_name: Name of tool invoked.
        tool_args: Tool arguments dictionary.
        tool_result: Sanitized tool result structure.
        context_manifest: Manifest dictionary for context events.
        message: Conversational message.
        error: Error description if applicable.
    """

    timestamp: str
    turn_id: int
    event_type: str
    from_state: str | None = None
    to_state: str | None = None
    trigger: str | None = None
    model_name: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    latency_ms: float | None = None
    estimated_cost_usd: float | None = None
    tool_name: str | None = None
    tool_args: dict[str, Any] | None = None
    tool_result: Any | None = None
    context_manifest: dict[str, Any] | None = None
    message: str | None = None
    error: str | None = None


class Tracer:
    """Records chronological, PII-scrubbed execution traces to JSONL."""

    def __init__(self, trace_file: str | None = None) -> None:
        """Initialize Tracer with output file path.

        Args:
            trace_file: Optional path to JSONL output log file.
        """
        self._trace_file = trace_file
        self.entries: list[dict[str, Any]] = []

        if self._trace_file:
            os.makedirs(os.path.dirname(os.path.abspath(self._trace_file)), exist_ok=True)

    def log(self, entry: TraceEntry) -> None:
        """Record and serialize a single trace entry.

        Args:
            entry: Structured TraceEntry.
        """
        raw_dict = asdict(entry)
        scrubbed_dict = _redact_structure(raw_dict)
        self.entries.append(scrubbed_dict)

        if self._trace_file:
            line = json.dumps(scrubbed_dict)
            with open(self._trace_file, "a", encoding="utf-8") as f:
                f.write(line + "\n")

    def log_event(
        self,
        event_type: str,
        turn_id: int = 0,
        **kwargs: Any,
    ) -> None:
        """Convenience method to construct and log a trace entry.

        Args:
            event_type: Event classification string.
            turn_id: Turn index.
            **kwargs: Extra attributes for TraceEntry.
        """
        now = datetime.now(timezone.utc).isoformat()
        entry = TraceEntry(
            timestamp=now,
            turn_id=turn_id,
            event_type=event_type,
            **kwargs,
        )
        self.log(entry)
