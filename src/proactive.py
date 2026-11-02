"""Proactive follow-up handler responding to upstream provider cancellations."""

from datetime import datetime, timezone
from typing import Any

from src.memory import MemoryStore
from src.schemas import ProviderCancelledEvent
from src.tools.contracts import ProviderSummary
from src.tools.mock_env import MockEnvironment


class ProactiveHandler:
    """Detects provider cancellation, searches replacements, and requests fresh approval."""

    def __init__(self, mock_env: MockEnvironment, memory: MemoryStore) -> None:
        """Initialize ProactiveHandler with mock tools and user memory.

        Args:
            mock_env: Mock environment for searching providers.
            memory: MemoryStore for retrieving stored user preferences.
        """
        self._mock_env = mock_env
        self._memory = memory

    def handle(
        self, event: ProviderCancelledEvent, user_id: str = "user_default"
    ) -> dict[str, Any]:
        """Process cancellation, determine urgency, and search replacement providers.

        Args:
            event: The provider cancellation event.
            user_id: Target user ID.

        Returns:
            dict[str, Any]: Structured result with notification message and replacements.
        """
        urgency = self._determine_urgency(event.original_slot, event.cancelled_at)
        stored_facts = {f.fact: f.value for f in self._memory.get_active_facts(user_id)}
        area = stored_facts.get("preferred_area", "Koramangala")

        replacements = self._mock_env.search_providers(
            service=event.service,
            area=area,
            date_range="upcoming",
        )
        # Filter out the cancelled provider
        candidates = [p for p in replacements if p.provider_id != event.provider_id]

        msg = (
            f"Notice ({urgency} Urgency): Your {event.service} booking {event.booking_id} "
            f"with {event.provider_name} on {event.original_slot} has been cancelled "
            f"by the provider ({event.reason})."
        )

        return {
            "notification": msg,
            "urgency": urgency,
            "candidates": candidates,
            "top_choice": candidates[0] if candidates else None,
            "area": area,
        }

    def _determine_urgency(self, slot_str: str, cancelled_at_str: str) -> str:
        """Calculate whether cancellation is urgent (<24 hours notice).

        Args:
            slot_str: ISO 8601 string of booked slot.
            cancelled_at_str: ISO 8601 string when cancellation occurred.

        Returns:
            str: 'High' if < 24 hours until slot, else 'Normal'.
        """
        try:
            slot_dt = datetime.fromisoformat(slot_str)
            cancel_dt = datetime.fromisoformat(cancelled_at_str)
            hours_diff = (slot_dt - cancel_dt).total_seconds() / 3600.0
            return "High" if hours_diff < 24.0 else "Normal"
        except Exception:
            return "Normal"
