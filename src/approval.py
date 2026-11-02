"""Approval enforcement, cryptographic hash binding, and intent gating."""

from dataclasses import dataclass, replace
import hashlib
import time
from typing import Literal
import uuid

CONFIRM_PATTERNS = [
    "yes", "confirm", "go ahead", "book it", "proceed",
    "approved", "ok", "sure", "do it", "yeah", "yep",
    "absolutely", "please", "let's do it", "sounds good",
]

DENY_PATTERNS = [
    "no", "cancel", "don't", "stop", "wait",
    "hold on", "not yet", "nope", "nah", "never mind",
]


@dataclass(frozen=True)
class ApprovalGrant:
    """Immutable proof of user approval bound cryptographically to action parameters.

    Attributes:
        grant_id: Unique UUID string for this grant.
        action: Permitted action ('book', 'cancel', 'reschedule', 'hold_slot').
        provider_id: Unique identifier of targeted provider.
        slot: Approved slot timestamp string.
        quoted_price: Approved price in INR.
        booking_id: Associated booking ID (for cancel/reschedule).
        material_hash: SHA-256 hash of material parameters.
        created_at: Epoch timestamp when grant was created.
        expires_at: Epoch timestamp when grant expires.
        used: Whether the grant has already been consumed.
    """

    grant_id: str
    action: str
    provider_id: str
    slot: str
    quoted_price: float
    material_hash: str
    created_at: float
    expires_at: float
    booking_id: str | None = None
    used: bool = False

    @staticmethod
    def compute_hash(
        action: str,
        provider_id: str,
        slot: str,
        quoted_price: float,
        booking_id: str | None = None,
    ) -> str:
        """Compute deterministic SHA-256 hash over material errand parameters.

        Args:
            action: Action name.
            provider_id: Provider identifier.
            slot: Slot string.
            quoted_price: Price in INR.
            booking_id: Optional booking identifier.

        Returns:
            str: Hex digest of SHA-256 hash.
        """
        payload = f"{action}|{provider_id}|{slot}|{quoted_price:.2f}|{booking_id or ''}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def is_valid(
        self,
        action: str,
        provider_id: str,
        slot: str,
        quoted_price: float,
        booking_id: str | None = None,
        current_time: float | None = None,
    ) -> bool:
        """Verify that grant is active, unexpired, and matches material details.

        Args:
            action: Action to check.
            provider_id: Target provider identifier.
            slot: Target slot timestamp.
            quoted_price: Quoted price.
            booking_id: Optional target booking ID.
            current_time: Optional mock timestamp for testing.

        Returns:
            bool: True if grant is valid and matches all material parameters.
        """
        if self.used:
            return False
        now = current_time if current_time is not None else time.time()
        if now > self.expires_at:
            return False

        expected_hash = self.compute_hash(action, provider_id, slot, quoted_price, booking_id)
        return self.material_hash == expected_hash

    def mark_used(self) -> "ApprovalGrant":
        """Produce an updated copy of the grant marked as used.

        Returns:
            ApprovalGrant: New immutable grant with used=True.
        """
        return replace(self, used=True)


def create_approval_grant(
    action: str,
    provider_id: str,
    slot: str,
    quoted_price: float,
    booking_id: str | None = None,
    ttl_seconds: float = 300.0,
) -> ApprovalGrant:
    """Create a new immutable ApprovalGrant with computed material hash.

    Args:
        action: Approved action name.
        provider_id: Target provider identifier.
        slot: Approved slot string.
        quoted_price: Quoted price in INR.
        booking_id: Optional booking identifier.
        ttl_seconds: Validity lifetime in seconds (default 5 min).

    Returns:
        ApprovalGrant: Newly generated grant instance.
    """
    now = time.time()
    mat_hash = ApprovalGrant.compute_hash(action, provider_id, slot, quoted_price, booking_id)
    return ApprovalGrant(
        grant_id=str(uuid.uuid4()),
        action=action,
        provider_id=provider_id,
        slot=slot,
        quoted_price=quoted_price,
        booking_id=booking_id,
        material_hash=mat_hash,
        created_at=now,
        expires_at=now + ttl_seconds,
        used=False,
    )


def approval_gate(
    grant: ApprovalGrant | None,
    action: str,
    provider_id: str,
    slot: str,
    quoted_price: float,
    booking_id: str | None = None,
    current_time: float | None = None,
) -> tuple[bool, str]:
    """Application code gate guarding all side-effect executions.

    Args:
        grant: Proposed ApprovalGrant, or None.
        action: Target action name.
        provider_id: Target provider identifier.
        slot: Target slot timestamp.
        quoted_price: Target action price.
        booking_id: Optional target booking identifier.
        current_time: Optional mock timestamp.

    Returns:
        tuple[bool, str]: (allowed, reason_message).
    """
    if grant is None:
        return False, "No approval grant exists"
    if grant.used:
        return False, "Approval grant already consumed"
    now = current_time if current_time is not None else time.time()
    if now > grant.expires_at:
        return False, "Approval grant has expired"

    if not grant.is_valid(action, provider_id, slot, quoted_price, booking_id, current_time=now):
        return False, "Approval grant does not match action parameters (material change detected)"
    return True, "Approved"


def detect_confirmation(user_text: str) -> Literal["confirmed", "denied", "ambiguous"]:
    """Detect explicit confirmation or denial from raw user text via whitelist.

    Args:
        user_text: Raw input string from the user.

    Returns:
        Literal['confirmed', 'denied', 'ambiguous']: Detected decision.
    """
    cleaned = user_text.strip().lower()
    for token in CONFIRM_PATTERNS:
        if cleaned == token or cleaned.startswith(f"{token} ") or cleaned.startswith(f"{token},"):
            return "confirmed"
    for token in DENY_PATTERNS:
        if cleaned == token or cleaned.startswith(f"{token} ") or cleaned.startswith(f"{token},"):
            return "denied"
    return "ambiguous"
