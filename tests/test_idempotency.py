"""Unit tests for deterministic idempotency key derivation."""

from src.idempotency import derive_idempotency_key


def test_derive_idempotency_key_deterministic() -> None:
    """Test that identical parameters produce the exact same key."""
    key1 = derive_idempotency_key("user_default", "book", "P-PLUM-01", "2024-01-15T10:00:00", 0)
    key2 = derive_idempotency_key("user_default", "book", "P-PLUM-01", "2024-01-15T10:00:00", 0)
    assert key1 == key2
    assert len(key1) == 32


def test_derive_idempotency_key_distinctions() -> None:
    """Test that changing attempt, slot, action, or provider alters the key."""
    base = derive_idempotency_key("user_default", "book", "P-PLUM-01", "2024-01-15T10:00:00", 0)

    # Attempt changed
    diff_attempt = derive_idempotency_key("user_default", "book", "P-PLUM-01", "2024-01-15T10:00:00", 1)
    assert base != diff_attempt

    # Slot changed
    diff_slot = derive_idempotency_key("user_default", "book", "P-PLUM-01", "2024-01-15T11:00:00", 0)
    assert base != diff_slot

    # Action changed
    diff_action = derive_idempotency_key("user_default", "cancel", "P-PLUM-01", "2024-01-15T10:00:00", 0)
    assert base != diff_action

    # Provider changed
    diff_provider = derive_idempotency_key("user_default", "book", "P-PLUM-02", "2024-01-15T10:00:00", 0)
    assert base != diff_provider
