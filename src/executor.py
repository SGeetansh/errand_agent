"""Executor mediating side-effect execution strictly behind the approval gate."""

from typing import Any, Protocol

from src.approval import ApprovalGrant, approval_gate
from src.tools.mock_env import MockEnvironment


class IntentLogProtocol(Protocol):
    """Protocol for write-ahead intent log integration."""

    def log_intent(
        self,
        idempotency_key: str,
        action: str,
        provider_id: str,
        slot: str,
        quoted_price: float,
        grant_id: str,
        hold_id: str | None = None,
        booking_id: str | None = None,
    ) -> None:
        """Log intent before calling side-effect tool."""
        ...

    def update_status(
        self,
        idempotency_key: str,
        status: str,
        booking_id: str | None = None,
        error: str | None = None,
    ) -> None:
        """Update intent status in log."""
        ...


class Executor:
    """Executes consequential side-effect tools only upon valid approval grant."""

    def __init__(
        self,
        mock_env: MockEnvironment,
        wal: IntentLogProtocol | None = None,
    ) -> None:
        """Initialize Executor with mock tool environment and optional WAL.

        Args:
            mock_env: Mock environment handling tool contracts.
            wal: Optional intent log manager for write-ahead logging.
        """
        self._mock_env = mock_env
        self._wal = wal

    def execute(
        self,
        grant: ApprovalGrant | None,
        action: str,
        provider_id: str,
        slot: str,
        quoted_price: float,
        booking_id: str | None = None,
        current_time: float | None = None,
        **kwargs: Any,
    ) -> tuple[Any, ApprovalGrant]:
        """Execute a side-effect tool after verifying the approval gate.

        Args:
            grant: ApprovalGrant bound to action parameters.
            action: Action to perform ('book', 'cancel', 'reschedule', 'hold_slot').
            provider_id: Targeted provider ID.
            slot: Target slot timestamp.
            quoted_price: Target action price.
            booking_id: Optional booking identifier.
            current_time: Optional mock timestamp for expiry checks.
            **kwargs: Action-specific arguments (e.g. hold_id, idempotency_key).

        Returns:
            tuple[Any, ApprovalGrant]: (tool_result, consumed_grant).

        Raises:
            PermissionError: If approval gate denies permission.
            ValueError: If an unknown action is requested.
        """
        allowed, reason = approval_gate(
            grant=grant,
            action=action,
            provider_id=provider_id,
            slot=slot,
            quoted_price=quoted_price,
            booking_id=booking_id,
            current_time=current_time,
        )
        if not allowed or grant is None:
            raise PermissionError(f"Action '{action}' rejected by approval gate: {reason}")

        # Optional WAL write-ahead intent logging
        idem_key = kwargs.get("idempotency_key")
        if self._wal and idem_key:
            self._wal.log_intent(
                idempotency_key=idem_key,
                action=action,
                provider_id=provider_id,
                slot=slot,
                quoted_price=quoted_price,
                grant_id=grant.grant_id,
                hold_id=kwargs.get("hold_id"),
                booking_id=booking_id,
            )
            self._wal.update_status(idempotency_key=idem_key, status="sent")

        result = self._dispatch(action, provider_id, slot, booking_id, **kwargs)

        if self._wal and idem_key:
            self._record_wal_outcome(idem_key, result)

        used_grant = grant.mark_used()
        return result, used_grant

    def _dispatch(
        self,
        action: str,
        provider_id: str,
        slot: str,
        booking_id: str | None,
        **kwargs: Any,
    ) -> Any:
        """Dispatch call to underlying mock environment.

        Args:
            action: Action name.
            provider_id: Provider identifier.
            slot: Slot string.
            booking_id: Optional booking ID.
            **kwargs: Extra parameters.

        Returns:
            Any: Result from mock environment tool.

        Raises:
            ValueError: If unknown action requested.
        """
        if action == "book":
            return self._mock_env.book(
                hold_id=kwargs["hold_id"],
                idempotency_key=kwargs["idempotency_key"],
            )
        if action == "cancel":
            assert booking_id is not None
            return self._mock_env.cancel(
                booking_id=booking_id,
                idempotency_key=kwargs["idempotency_key"],
            )
        if action == "reschedule":
            assert booking_id is not None
            return self._mock_env.reschedule(
                booking_id=booking_id,
                slot=slot,
                idempotency_key=kwargs["idempotency_key"],
            )
        if action == "hold_slot":
            return self._mock_env.hold_slot(provider_id=provider_id, slot=slot)
        raise ValueError(f"Unknown side-effect action: {action}")

    def _record_wal_outcome(self, idem_key: str, result: Any) -> None:
        """Update WAL status based on tool call outcome.

        Args:
            idem_key: Idempotency key.
            result: Result from tool call.
        """
        if not self._wal:
            return
        status = getattr(result, "status", None)
        if status == "confirmed":
            self._wal.update_status(idem_key, status="confirmed", booking_id=getattr(result, "booking_id", None))
        elif status == "timeout":
            self._wal.update_status(idem_key, status="timeout")
        elif status in ("slot_taken", "hold_expired"):
            self._wal.update_status(idem_key, status="failed", error=status)
        elif status == "cancelled":
            self._wal.update_status(idem_key, status="confirmed")
        elif getattr(result, "error", False):
            self._wal.update_status(idem_key, status="failed", error=str(getattr(result, "message", "error")))
