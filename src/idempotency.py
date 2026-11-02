"""Deterministic idempotency key derivation and management."""

import hashlib


def derive_idempotency_key(
    user_id: str,
    action: str,
    provider_id: str,
    slot: str,
    attempt: int = 0,
) -> str:
    """Derive a deterministic idempotency key for side-effect operations.

    The attempt counter increments strictly after get_booking_status confirms
    the previous attempt did not succeed.

    Args:
        user_id: Unique user identifier.
        action: Action name (e.g. 'book', 'cancel', 'reschedule').
        provider_id: Identifier of the service provider.
        slot: Target slot timestamp string.
        attempt: Attempt counter, starting at 0.

    Returns:
        str: Deterministic 32-character hexadecimal SHA-256 slice.
    """
    payload = f"{user_id}|{action}|{provider_id}|{slot}|{attempt}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]
