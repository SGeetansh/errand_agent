"""Bounded execution budget tracker and exit path mapping."""

from dataclasses import dataclass, field
import time
from typing import Any


@dataclass
class Budget:
    """Tracks turns, tool invocations, and elapsed wall-clock time.

    Attributes:
        max_model_turns: Hard limit on model invocations.
        max_tool_calls: Hard limit on tool calls across session.
        wall_clock_deadline_seconds: Maximum wall-clock runtime in seconds.
        per_tool_retry_caps: Maximum attempts allowed per individual tool.
        retry_backoff_base_seconds: Base backoff time between tool retries.
        model_turns_used: Model turn counter.
        tool_calls_used: Overall tool call counter.
        tool_call_counts: Map of tool name to invocation counts.
        start_time: Epoch start timestamp.
    """

    max_model_turns: int = 25
    max_tool_calls: int = 50
    wall_clock_deadline_seconds: float = 300.0
    per_tool_retry_caps: dict[str, int] = field(
        default_factory=lambda: {
            "book": 3,
            "cancel": 3,
            "reschedule": 3,
            "hold_slot": 2,
            "search_providers": 2,
            "get_provider": 2,
            "get_booking_status": 5,
            "list_bookings": 2,
        }
    )
    retry_backoff_base_seconds: float = 1.0

    model_turns_used: int = 0
    tool_calls_used: int = 0
    tool_call_counts: dict[str, int] = field(default_factory=dict)
    start_time: float = field(default_factory=time.time)

    def record_turn(self) -> None:
        """Increment model turns counter."""
        self.model_turns_used += 1

    def record_tool_call(self, tool_name: str) -> None:
        """Increment tool call counters globally and per tool.

        Args:
            tool_name: Name of tool invoked.
        """
        self.tool_calls_used += 1
        self.tool_call_counts[tool_name] = self.tool_call_counts.get(tool_name, 0) + 1

    def tool_retries_remaining(self, tool_name: str) -> int:
        """Calculate remaining allowed calls for a specific tool.

        Args:
            tool_name: Name of tool to check.

        Returns:
            int: Remaining allowed calls before cap is reached.
        """
        used = self.tool_call_counts.get(tool_name, 0)
        cap = self.per_tool_retry_caps.get(tool_name, 2)
        return max(0, cap - used)

    def exhausted(self, current_time: float | None = None) -> tuple[bool, str | None]:
        """Check whether any global bounding budget has been exhausted.

        Args:
            current_time: Optional mock timestamp.

        Returns:
            tuple[bool, str | None]: (is_exhausted, reason_string).
        """
        if self.model_turns_used >= self.max_model_turns:
            return True, "model_turn_limit"
        if self.tool_calls_used >= self.max_tool_calls:
            return True, "tool_call_limit"
        now = current_time if current_time is not None else time.time()
        if now - self.start_time >= self.wall_clock_deadline_seconds:
            return True, "wall_clock_timeout"
        return False, None

    def get_exit_message(
        self, reason: str, context: dict[str, Any] | None = None
    ) -> str:
        """Produce clear user-facing message corresponding to an exit condition.

        Args:
            reason: Exit condition reason identifier.
            context: Contextual parameters for formatting.

        Returns:
            str: User-facing exit message.
        """
        ctx = context or {}
        summary = ctx.get("summary", "errand in progress")
        if reason == "model_turn_limit":
            return f"I've reached my processing limit. Here's where we are: {summary}. Please start a new conversation to continue."
        if reason == "tool_call_limit":
            return f"I've used all available tool calls. Current status: {summary}. Please try again."
        if reason == "wall_clock_timeout":
            status_check = ctx.get("status_check_result", "checking status...")
            return f"This is taking too long. Let me check the current status... {status_check}"
        if reason == "retry_cap_book":
            return "Booking attempts exhausted. The slot may no longer be available. Shall I search for alternatives?"
        if reason == "retry_cap_cancel":
            attempts = ctx.get("attempts", 3)
            b_id = ctx.get("booking_id", "")
            return f"Unable to cancel after {attempts} attempts. Please try again shortly. Your booking {b_id} is still active."
        if reason == "retry_cap_reschedule":
            attempts = ctx.get("attempts", 3)
            return f"Rescheduling failed after {attempts} attempts due to slot conflicts. Would you like to try a different time?"
        if reason == "user_declined":
            return "No problem. Would you like to see other options, or is there anything else I can help with?"
        if reason == "no_providers":
            service = ctx.get("service", "requested")
            area = ctx.get("area", "your area")
            date_range = ctx.get("date_range", "the selected dates")
            return f"I couldn't find any {service} providers in {area} for {date_range}. Would you like to try different dates or a wider area?"
        return f"Operation ended: {reason}."
