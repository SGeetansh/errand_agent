"""Unit tests for Executor validating side-effect protection behind the approval gate."""

import pytest

from src.approval import create_approval_grant
from src.executor import Executor
from src.tools.contracts import BookResult, CancelResult, HoldResult, RescheduleResult
from src.tools.mock_env import MockEnvironment
from src.tools.scenario_config import ScenarioConfig


def test_executor_refuses_without_grant(mock_env: MockEnvironment) -> None:
    """Test Executor strictly refuses execution when no grant is provided."""
    executor = Executor(mock_env)
    with pytest.raises(PermissionError, match="No approval grant exists"):
        executor.execute(
            grant=None,
            action="book",
            provider_id="P-PLUM-01",
            slot="2024-01-15T10:00:00",
            quoted_price=1200.0,
            hold_id="HOLD-0001",
            idempotency_key="KEY-NO-GRANT",
        )


def test_executor_refuses_on_price_drift(mock_env: MockEnvironment) -> None:
    """Test Executor refuses to execute when actual price differs from approved price."""
    executor = Executor(mock_env)
    approved_grant = create_approval_grant(
        action="book",
        provider_id="P-PLUM-01",
        slot="2024-01-15T10:00:00",
        quoted_price=1200.0,
    )

    with pytest.raises(PermissionError, match="material change detected"):
        executor.execute(
            grant=approved_grant,
            action="book",
            provider_id="P-PLUM-01",
            slot="2024-01-15T10:00:00",
            quoted_price=1350.0,  # Price drifted!
            hold_id="HOLD-0001",
            idempotency_key="KEY-DRIFT",
        )


def test_executor_consumes_grant_preventing_reuse(mock_env: MockEnvironment) -> None:
    """Test Executor consumes the grant, prohibiting replay with the same grant."""
    mock_env.config.force_book_outcome = "success"
    executor = Executor(mock_env)

    hold = mock_env.hold_slot("P-PLUM-01", "2024-01-15T10:00:00")
    assert isinstance(hold, HoldResult)

    grant = create_approval_grant(
        action="book",
        provider_id="P-PLUM-01",
        slot="2024-01-15T10:00:00",
        quoted_price=1200.0,
    )

    result, consumed_grant = executor.execute(
        grant=grant,
        action="book",
        provider_id="P-PLUM-01",
        slot="2024-01-15T10:00:00",
        quoted_price=1200.0,
        hold_id=hold.hold_id,
        idempotency_key="KEY-CONSUME",
    )
    assert isinstance(result, BookResult)
    assert consumed_grant.used

    # Attempt reuse of consumed grant
    with pytest.raises(PermissionError, match="already consumed"):
        executor.execute(
            grant=consumed_grant,
            action="book",
            provider_id="P-PLUM-01",
            slot="2024-01-15T10:00:00",
            quoted_price=1200.0,
            hold_id=hold.hold_id,
            idempotency_key="KEY-REUSE",
        )


def test_executor_cancel_and_reschedule() -> None:
    """Test Executor mediates cancel and reschedule side effects."""
    env = MockEnvironment(ScenarioConfig(cancel_503_on_first=False))
    executor = Executor(env)

    # Cancel execution
    cancel_grant = create_approval_grant(
        action="cancel",
        provider_id="P-AC-01",
        slot="2024-01-14T15:00:00",
        quoted_price=1500.0,
        booking_id="BKG-AC-002",
    )
    res_cancel, _ = executor.execute(
        grant=cancel_grant,
        action="cancel",
        provider_id="P-AC-01",
        slot="2024-01-14T15:00:00",
        quoted_price=1500.0,
        booking_id="BKG-AC-002",
        idempotency_key="KEY-CAN-EXEC",
    )
    assert isinstance(res_cancel, CancelResult)
    assert res_cancel.status == "cancelled"

    # Reschedule execution
    reschedule_grant = create_approval_grant(
        action="reschedule",
        provider_id="P-ELEC-01",
        slot="2024-01-20T10:00:00",
        quoted_price=1200.0,
        booking_id="BKG-PAST-001",
    )
    res_resched, _ = executor.execute(
        grant=reschedule_grant,
        action="reschedule",
        provider_id="P-ELEC-01",
        slot="2024-01-20T10:00:00",
        quoted_price=1200.0,
        booking_id="BKG-PAST-001",
        idempotency_key="KEY-RESCH-EXEC",
    )
    assert isinstance(res_resched, RescheduleResult)
    assert res_resched.status == "rescheduled"
