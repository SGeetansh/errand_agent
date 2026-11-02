"""Write-ahead intent log (WAL) and recovery manager for side effects."""

from datetime import datetime, timezone
import os
import sqlite3
from typing import Any

from src.tools.contracts import BookingStatus
from src.tools.mock_env import MockEnvironment


class IntentLog:
    """SQLite-backed write-ahead intent log for side-effect operations."""

    def __init__(self, db_path: str = "data/errand_agent.db") -> None:
        """Initialize intent log with SQLite database path.

        Args:
            db_path: Path to SQLite database file or ':memory:'.
        """
        self._db_path = db_path
        if db_path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
            self._conn = sqlite3.connect(db_path, check_same_thread=False)
        else:
            self._conn = sqlite3.connect(":memory:", check_same_thread=False)

        self._conn.row_factory = sqlite3.Row
        self.init_db()

    def _get_connection(self) -> sqlite3.Connection:
        """Return the active SQLite connection.

        Returns:
            sqlite3.Connection: Database connection.
        """
        return self._conn

    def init_db(self) -> None:
        """Create intent_log table and indexes if not present."""
        conn = self._get_connection()
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS intent_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                idempotency_key TEXT NOT NULL UNIQUE,
                action TEXT NOT NULL,
                provider_id TEXT NOT NULL,
                slot TEXT,
                quoted_price REAL,
                hold_id TEXT,
                booking_id TEXT,
                grant_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                attempt_count INTEGER DEFAULT 0,
                last_error TEXT
            );
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_intent_status
            ON intent_log(status);
            """
        )
        conn.commit()

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
        """Record intent to execute a side effect BEFORE invoking external tool.

        Args:
            idempotency_key: Deterministic key.
            action: Action name ('book', 'cancel', 'reschedule').
            provider_id: Target provider ID.
            slot: Slot string.
            quoted_price: Quoted price.
            grant_id: UUID of approval grant.
            hold_id: Optional hold ID.
            booking_id: Optional booking ID.
        """
        now = datetime.now(timezone.utc).isoformat()
        conn = self._get_connection()
        conn.execute(
            """
            INSERT INTO intent_log (
                idempotency_key, action, provider_id, slot, quoted_price,
                hold_id, booking_id, grant_id, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
            ON CONFLICT(idempotency_key) DO UPDATE SET
                updated_at = excluded.updated_at,
                attempt_count = attempt_count + 1
            """,
            (
                idempotency_key, action, provider_id, slot, quoted_price,
                hold_id, booking_id, grant_id, now, now
            ),
        )
        conn.commit()

    def update_status(
        self,
        idempotency_key: str,
        status: str,
        booking_id: str | None = None,
        error: str | None = None,
    ) -> None:
        """Update the status of a logged intent.

        Args:
            idempotency_key: Unique key.
            status: Target status ('sent', 'confirmed', 'failed', 'timeout').
            booking_id: Resulting booking ID if known.
            error: Error message if failed.
        """
        now = datetime.now(timezone.utc).isoformat()
        conn = self._get_connection()
        if booking_id:
            conn.execute(
                """
                UPDATE intent_log
                SET status = ?, booking_id = ?, updated_at = ?, last_error = ?
                WHERE idempotency_key = ?
                """,
                (status, booking_id, now, error, idempotency_key),
            )
        else:
            conn.execute(
                """
                UPDATE intent_log
                SET status = ?, updated_at = ?, last_error = ?
                WHERE idempotency_key = ?
                """,
                (status, now, error, idempotency_key),
            )
        conn.commit()

    def get_intent(self, idempotency_key: str) -> dict[str, Any] | None:
        """Retrieve a single intent record by idempotency key.

        Args:
            idempotency_key: Unique idempotency key.

        Returns:
            dict[str, Any] | None: Row dictionary or None.
        """
        conn = self._get_connection()
        cursor = conn.execute(
            "SELECT * FROM intent_log WHERE idempotency_key = ?",
            (idempotency_key,),
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    def get_pending_intents(self) -> list[dict[str, Any]]:
        """Retrieve all intents in pending, sent, or timeout states.

        Returns:
            list[dict[str, Any]]: List of unresolved intent records.
        """
        conn = self._get_connection()
        cursor = conn.execute(
            """
            SELECT * FROM intent_log
            WHERE status IN ('pending', 'sent', 'timeout')
            ORDER BY id ASC
            """
        )
        return [dict(r) for r in cursor.fetchall()]

    def close(self) -> None:
        """Close database connection."""
        self._conn.close()


class RecoveryManager:
    """Manages startup reconciliation and timeout resolution."""

    def __init__(self, wal: IntentLog, mock_env: MockEnvironment) -> None:
        """Initialize RecoveryManager with WAL and mock environment.

        Args:
            wal: Write-ahead intent log manager.
            mock_env: Mock environment source of truth.
        """
        self._wal = wal
        self._mock_env = mock_env

    def reconcile(self) -> list[dict[str, Any]]:
        """Query unresolved intents on restart and verify with source of truth.

        Returns:
            list[dict[str, Any]]: Summary of reconciliation actions taken.
        """
        unresolved = self._wal.get_pending_intents()
        results: list[dict[str, Any]] = []

        for intent in unresolved:
            key = intent["idempotency_key"]
            status_obj = self._mock_env.get_booking_status(key)

            if status_obj.status == "confirmed":
                self._wal.update_status(
                    idempotency_key=key,
                    status="confirmed",
                    booking_id=status_obj.booking_id,
                )
                results.append({"key": key, "resolution": "confirmed", "booking_id": status_obj.booking_id})
            else:
                self._wal.update_status(
                    idempotency_key=key,
                    status="failed",
                    error=f"reconciled_status_{status_obj.status}",
                )
                results.append({"key": key, "resolution": "failed", "status": status_obj.status})

        return results

    def handle_timeout(self, idempotency_key: str) -> BookingStatus:
        """Resolve ambiguous timeout outcome by querying source of truth.

        Args:
            idempotency_key: Key of operation that timed out.

        Returns:
            BookingStatus: Authoritative status from source of truth.
        """
        status_obj = self._mock_env.get_booking_status(idempotency_key)
        if status_obj.status == "confirmed":
            self._wal.update_status(
                idempotency_key=idempotency_key,
                status="confirmed",
                booking_id=status_obj.booking_id,
            )
        else:
            self._wal.update_status(
                idempotency_key=idempotency_key,
                status="failed",
                error="timeout_verified_not_booked",
            )
        return status_obj
