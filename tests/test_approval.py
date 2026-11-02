"""Unit tests for approval gate, cryptographic grant binding, and confirmation detection."""

import time
import pytest

from src.approval import (
    ApprovalGrant,
    approval_gate,
    create_approval_grant,
    detect_confirmation,
)


def test_create_approval_grant_hash() -> None:
    """Test creating grant derives valid material hash."""
    grant = create_approval_grant(
        action="book",
        provider_id="P-PLUM-01",
        slot="2024-01-15T10:00:00",
        quoted_price=1200.0,
    )
    expected_hash = ApprovalGrant.compute_hash(
        action="book",
        provider_id="P-PLUM-01",
        slot="2024-01-15T10:00:00",
        quoted_price=1200.0,
    )
    assert grant.material_hash == expected_hash
    assert not grant.used
    assert grant.expires_at > grant.created_at


def test_approval_grant_validity_and_material_checks() -> None:
    """Test grant validity rejects any change in material fields."""
    grant = create_approval_grant(
        action="book",
        provider_id="P-PLUM-01",
        slot="2024-01-15T10:00:00",
        quoted_price=1200.0,
    )
    # Valid call
    assert grant.is_valid("book", "P-PLUM-01", "2024-01-15T10:00:00", 1200.0)

    # Material change: price drift
    assert not grant.is_valid("book", "P-PLUM-01", "2024-01-15T10:00:00", 1300.0)

    # Material change: slot changed
    assert not grant.is_valid("book", "P-PLUM-01", "2024-01-15T11:00:00", 1200.0)

    # Material change: provider changed
    assert not grant.is_valid("book", "P-PLUM-02", "2024-01-15T10:00:00", 1200.0)

    # Material change: action changed
    assert not grant.is_valid("cancel", "P-PLUM-01", "2024-01-15T10:00:00", 1200.0)


def test_approval_gate_edge_cases() -> None:
    """Test approval gate with None grant, consumed grant, and expired grant."""
    # 1. No grant
    allowed, msg = approval_gate(None, "book", "P-01", "2024-01-01", 100.0)
    assert not allowed
    assert "No approval grant exists" in msg

    grant = create_approval_grant("book", "P-01", "2024-01-01", 100.0, ttl_seconds=10)

    # 2. Used grant
    used_grant = grant.mark_used()
    allowed_used, msg_used = approval_gate(used_grant, "book", "P-01", "2024-01-01", 100.0)
    assert not allowed_used
    assert "already consumed" in msg_used

    # 3. Expired grant
    fake_time = grant.expires_at + 10.0
    allowed_exp, msg_exp = approval_gate(
        grant, "book", "P-01", "2024-01-01", 100.0, current_time=fake_time
    )
    assert not allowed_exp
    assert "expired" in msg_exp


def test_detect_confirmation_whitelist() -> None:
    """Test confirmation detection strictly honors confirm/deny whitelists."""
    assert detect_confirmation("yes") == "confirmed"
    assert detect_confirmation("YES, please proceed") == "confirmed"
    assert detect_confirmation("confirm") == "confirmed"
    assert detect_confirmation("book it") == "confirmed"
    assert detect_confirmation("ok") == "confirmed"
    assert detect_confirmation("sure") == "confirmed"

    assert detect_confirmation("no") == "denied"
    assert detect_confirmation("cancel") == "denied"
    assert detect_confirmation("don't do that") == "denied"
    assert detect_confirmation("stop") == "denied"
    assert detect_confirmation("never mind") == "denied"

    assert detect_confirmation("tell me a joke") == "ambiguous"
    assert detect_confirmation("maybe tomorrow") == "ambiguous"
