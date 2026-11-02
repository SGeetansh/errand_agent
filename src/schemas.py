"""Shared schemas and domain models across the errand agent system."""

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass
class ProviderCancelledEvent:
    """Event emitted when a service provider proactively cancels a booking.

    Attributes:
        booking_id: Unique booking identifier that was cancelled.
        provider_id: Unique identifier of the provider.
        provider_name: Name of the cancelling provider.
        service: Service category.
        original_slot: ISO 8601 string of original booked slot.
        cancelled_at: ISO 8601 timestamp of cancellation.
        reason: Cancellation reason string.
    """

    booking_id: str
    provider_id: str
    provider_name: str
    service: str
    original_slot: str
    cancelled_at: str
    reason: str = "provider_unavailable"


@dataclass
class LLMResponse:
    """Standardized response from an LLM provider.

    Attributes:
        intent: Detected intent string.
        slots: Extracted or updated slot dictionary.
        message: Conversational response text for the user.
        tool_calls: List of requested tool calls with names and arguments.
        raw_response: Raw completion text from model.
        usage: Token and execution metadata dictionary.
    """

    intent: str
    slots: dict[str, Any] = field(default_factory=dict)
    message: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    raw_response: str = ""
    usage: dict[str, Any] = field(default_factory=dict)
