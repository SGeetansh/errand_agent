"""Unit tests for SQLite write-ahead intent log (WAL) and recovery reconciliation."""

import pytest

from src.approval import create_approval_grant
from src.executor import Executor
from src.idempotency import derive_idempotency_key
from src.recovery import IntentLog, RecoveryManager
from src.tools.contracts import HoldResult
from src.tools.mock_env import MockEnvironment
from src.tools.scenario_config import ScenarioConfig


@pytest.fixture
def memory_wal() -> IntentLog:
    """Fixture providing an in-memory IntentLog.

    Returns:
        IntentLog: Memory SQLite WAL instance.
    """
    return IntentLog(db_path=":memory:")


def test_wal_log_and_status_update(memory_wal: IntentLog) -> None:
    """Test logging an intent and updating status in WAL."""
    key = "TEST-IDEM-001"
    memory_wal.log_intent(
        idempotency_key=key,
        action="book",
        provider_id="P-PLUM-01",
        slot="2024-01-15T10:00:00",
        quoted_price=1200.0,
        grant_id="GRANT-123",
        hold_id="HOLD-001",
    )

    intent = memory_wal.get_intent(key)
    assert intent is not None
    assert intent["status"] == "pending"
    assert intent["quoted_price"] == 1200.0

    memory_wal.update_status(key, status="confirmed", booking_id="BKG-999")
    updated = memory_wal.get_intent(key)
    assert updated is not None
    assert updated["status"] == "confirmed"
    assert updated["booking_id"] == "BKG-999"


def test_executor_with_wal_integration(memory_wal: IntentLog, mock_env: MockEnvironment) -> None:
    """Test Executor logs intent to WAL before invoking mock tool."""
    mock_env.config.force_book_outcome = "success"
    executor = Executor(mock_env, wal=memory_wal)

    hold = mock_env.hold_slot("P-PLUM-01", "2024-01-15T10:00:00")
    assert isinstance(hold, HoldResult)

    key = derive_idempotency_key("user_default", "book", "P-PLUM-01", "2024-01-15T10:00:00")
    grant = create_approval_grant(
        action="book",
        provider_id="P-PLUM-01",
        slot="2024-01-15T10:00:00",
        quoted_price=1200.0,
    )

    res, _ = executor.execute(
        grant=grant,
        action="book",
        provider_id="P-PLUM-01",
        slot="2024-01-15T10:00:00",
        quoted_price=1200.0,
        hold_id=hold.hold_id,
        idempotency_key=key,
    )

    # WAL must record confirmed status
    record = memory_wal.get_intent(key)
    assert record is not None
    assert record["status"] == "confirmed"
    assert record["booking_id"] == res.booking_id


def test_timeout_after_possible_success_actually_booked(memory_wal: IntentLog) -> None:
    """Test timeout resolution when the booking actually succeeded upstream."""
    env = MockEnvironment(ScenarioConfig(force_book_outcome="timeout_booked"))
    executor = Executor(env, wal=memory_wal)
    recovery = RecoveryManager(memory_wal, env)

    hold = env.hold_slot("P-PLUM-01", "2024-01-15T10:00:00")
    assert isinstance(hold, HoldResult)
    key = "KEY-TIMEOUT-BOOKED"
    grant = create_approval_grant("book", "P-PLUM-01", "2024-01-15T10:00:00", 1200.0)

    res, _ = executor.execute(
        grant=grant,
        action="book",
        provider_id="P-PLUM-01",
        slot="2024-01-15T10:00:00",
        quoted_price=1200.0,
        hold_id=hold.hold_id,
        idempotency_key=key,
    )
    assert res.status == "timeout"

    # In WAL, status is currently timeout
    wal_entry = memory_wal.get_intent(key)
    assert wal_entry is not None
    assert wal_entry["status"] == "timeout"

    # Query source of truth via RecoveryManager
    status_obj = recovery.handle_timeout(key)
    assert status_obj.status == "confirmed"
    assert status_obj.booking_id is not None

    # WAL should now be reconciled to confirmed
    updated_wal = memory_wal.get_intent(key)
    assert updated_wal is not None
    assert updated_wal["status"] == "confirmed"
    assert updated_wal["booking_id"] == status_obj.booking_id


def test_timeout_after_possible_success_not_booked(memory_wal: IntentLog) -> None:
    """Test timeout resolution when the booking did NOT succeed upstream."""
    env = MockEnvironment(ScenarioConfig(force_book_outcome="timeout_not_booked"))
    executor = Executor(env, wal=memory_wal)
    recovery = RecoveryManager(memory_wal, env)

    hold = env.hold_slot("P-PLUM-01", "2024-01-15T10:00:00")
    assert isinstance(hold, HoldResult)
    key = "KEY-TIMEOUT-NOT-BOOKED"
    grant = create_approval_grant("book", "P-PLUM-01", "2024-01-15T10:00:00", 1200.0)

    executor.execute(
        grant=grant,
        action="book",
        provider_id="P-PLUM-01",
        slot="2024-01-15T10:00:00",
        quoted_price=1200.0,
        hold_id=hold.hold_id,
        idempotency_key=key,
    )

    status_obj = recovery.handle_timeout(key)
    assert status_obj.status != "confirmed"

    updated_wal = memory_wal.get_intent(key)
    assert updated_wal is not None
    assert updated_wal["status"] == "failed"


def test_startup_reconciliation_resolves_pending_intents(memory_wal: IntentLog) -> None:
    """Test that restarting the process reconciles pending or sent intents."""
    env = MockEnvironment(ScenarioConfig())
    recovery = RecoveryManager(memory_wal, env)

    # 1. Intent that was confirmed in upstream mock
    hold = env.hold_slot("P-PLUM-01", "2024-01-15T10:00:00")
    assert isinstance(hold, HoldResult)
    key_booked = "KEY-RECON-BOOKED"
    env.config.force_book_outcome = "success"
    env.book(hold.hold_id, key_booked)

    # In WAL, simulate crash before status update: left at 'sent'
    memory_wal.log_intent(
        idempotency_key=key_booked,
        action="book",
        provider_id="P-PLUM-01",
        slot="2024-01-15T10:00:00",
        quoted_price=1200.0,
        grant_id="G-1",
    )
    memory_wal.update_status(key_booked, status="sent")

    # 2. Intent that never made it to upstream (crash before request sent)
    key_lost = "KEY-RECON-LOST"
    memory_wal.log_intent(
        idempotency_key=key_lost,
        action="book",
        provider_id="P-PLUM-01",
        slot="2024-01-15T10:00:00",
        quoted_price=1200.0,
        grant_id="G-2",
    )

    # Reconcile on simulated restart
    results = recovery.reconcile()
    assert len(results) == 2

    # Verify key_booked became confirmed
    assert memory_wal.get_intent(key_booked)["status"] == "confirmed"

    # Verify key_lost became failed
    assert memory_wal.get_intent(key_lost)["status"] == "failed"
