"""Data contracts and schemas for mock tools.

Defines all input/output dataclasses, error representations, and return
types matching the required tool contracts from the specification.
"""

from dataclasses import dataclass, field
from typing import Literal


@dataclass
class ProviderSummary:
    """Summary representation of a service provider in search results.

    Attributes:
        provider_id: Unique identifier of the provider.
        name: Name of the service provider or company.
        service: Service category (e.g. plumber, ac_service, electrician).
        price: Estimated price in INR.
        rating: Rating score between 1.0 and 5.0.
        available_slots: List of ISO 8601 datetime strings.
        attributes: Key-value attributes such as experience and specialty.
    """

    provider_id: str
    name: str
    service: str
    price: float
    rating: float
    available_slots: list[str]
    attributes: dict[str, str] = field(default_factory=dict)


@dataclass
class ProviderDetail:
    """Detailed information for a single provider, including reviews.

    Attributes:
        provider_id: Unique identifier of the provider.
        name: Name of the service provider.
        service: Service category.
        price: Base price in INR.
        rating: Rating score between 1.0 and 5.0.
        available_slots: List of ISO 8601 datetime strings.
        attributes: Key-value attributes.
        reviews: List of free-text customer reviews.
    """

    provider_id: str
    name: str
    service: str
    price: float
    rating: float
    available_slots: list[str]
    attributes: dict[str, str]
    reviews: list[str]


@dataclass
class HoldResult:
    """Result of holding a slot temporarily.

    Attributes:
        hold_id: Unique hold identifier.
        provider_id: Identifier of the provider held.
        slot: ISO 8601 datetime string of held slot.
        quoted_price: Price in INR quoted at hold time (may drift from search).
        expiry: ISO 8601 datetime string when the hold expires.
    """

    hold_id: str
    provider_id: str
    slot: str
    quoted_price: float
    expiry: str


@dataclass
class BookResult:
    """Result of a booking attempt.

    Attributes:
        status: One of 'confirmed', 'slot_taken', 'hold_expired', 'timeout'.
        booking_id: Confirmed booking identifier, or None if unconfirmed.
        message: Descriptive outcome message.
    """

    status: Literal["confirmed", "slot_taken", "hold_expired", "timeout"]
    booking_id: str | None
    message: str


@dataclass
class BookingStatus:
    """Authoritative booking status from source of truth.

    Attributes:
        status: One of 'confirmed', 'not_found', 'cancelled', 'hold_only', 'pending'.
        booking_id: Identifier of the booking if confirmed or cancelled.
        provider_id: Provider identifier if known.
        service: Service category if known.
        slot: Slot ISO datetime string if known.
        price: Price in INR if known.
    """

    status: Literal["confirmed", "not_found", "cancelled", "hold_only", "pending"]
    booking_id: str | None = None
    provider_id: str | None = None
    service: str | None = None
    slot: str | None = None
    price: float | None = None


@dataclass
class CancelResult:
    """Result of a booking cancellation.

    Attributes:
        status: Status 'cancelled' or 'error'.
        error_code: HTTP error code if error occurred (e.g. 503).
        message: Descriptive status message.
    """

    status: Literal["cancelled", "error"]
    error_code: int | None
    message: str


@dataclass
class RescheduleResult:
    """Result of rescheduling a booking.

    Attributes:
        status: One of 'rescheduled', 'slot_conflict', 'error'.
        new_slot: The newly confirmed slot, or None if failed.
        message: Descriptive status message.
    """

    status: Literal["rescheduled", "slot_conflict", "error"]
    new_slot: str | None
    message: str


@dataclass
class BookingRecord:
    """Record of a past or upcoming booking.

    Attributes:
        booking_id: Unique identifier for the booking.
        provider_id: Identifier of the provider.
        provider_name: Display name of the provider.
        service: Service category.
        slot: ISO 8601 datetime string of the booking.
        price: Confirmed price in INR.
        status: One of 'confirmed', 'cancelled', 'completed'.
    """

    booking_id: str
    provider_id: str
    provider_name: str
    service: str
    slot: str
    price: float
    status: Literal["confirmed", "cancelled", "completed"]


@dataclass
class ToolError:
    """Standardized tool error response.

    Attributes:
        error: Always True to indicate an error occurred.
        error_code: Numeric error code (e.g. 404, 500, 503).
        message: Detailed error message.
    """

    error: bool = True
    error_code: int = 500
    message: str = "Internal error"
