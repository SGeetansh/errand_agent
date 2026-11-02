"""Scenario configuration for deterministic and mock tool behavior."""

from dataclasses import dataclass, field


@dataclass
class ScenarioConfig:
    """Controls deterministic mock behavior for a test scenario.

    Attributes:
        seed: Random seed for reproducible mock responses.
        num_providers: Number of providers returned per search (3-8).
        provider_services: Supported service categories for provider generation.
        price_drift: Quoted price difference applied during hold_slot in INR.
        hold_duration_seconds: Duration in seconds for which a hold remains valid.
        force_book_outcome: Forced outcome for booking ('success', 'slot_taken',
            'timeout_booked', 'timeout_not_booked', or None for seeded probability).
        cancel_503_on_first: Whether the first cancel call returns 503 Service Unavailable.
        slot_conflict_slots: List of ISO 8601 slots that trigger slot_conflict on reschedule.
        inject_review: Custom prompt injection text; default used if None.
        provider_cancel_booking_id: Booking ID to simulate proactive cancellation for.
        provider_cancel_after_seconds: Delay before proactive cancellation event fires.
    """

    seed: int = 42
    num_providers: int = 5
    provider_services: list[str] = field(
        default_factory=lambda: ["plumber", "ac_service", "electrician"]
    )
    price_drift: float | None = None
    hold_duration_seconds: int = 600
    force_book_outcome: str | None = None
    cancel_503_on_first: bool = True
    slot_conflict_slots: list[str] = field(default_factory=list)
    inject_review: str | None = None
    provider_cancel_booking_id: str | None = None
    provider_cancel_after_seconds: float = 5.0
