"""Persistent memory store managing user preferences, provenance, and lifecycle."""

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
import sqlite3
from typing import Any


@dataclass
class MemoryRecord:
    """Represents a single persistent fact in the memory store.

    Attributes:
        id: Primary key identifier.
        fact: Key or concept name (e.g. 'preferred_area').
        value: Fact value string.
        provenance: Origin of fact ('explicit' or 'inferred').
        confirmed: Confirmation flag (1 = confirmed, 0 = unconfirmed).
        status: Lifecycle state ('active' or 'forgotten').
        source_turn: Turn index where fact was captured.
        created_at: ISO 8601 creation timestamp.
        updated_at: ISO 8601 update timestamp.
        user_id: Associated user identifier.
    """

    id: int
    fact: str
    value: str
    provenance: str
    confirmed: int
    status: str
    source_turn: int
    created_at: str
    updated_at: str
    user_id: str


class MemoryStore:
    """SQLite-backed store for user preferences and conversational facts."""

    def __init__(self, db_path: str = "data/errand_agent.db") -> None:
        """Initialize memory store with database path.

        Args:
            db_path: Path to SQLite DB file or ':memory:'.
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
        """Create memory table and search index if not already present."""
        conn = self._get_connection()
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS memory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fact TEXT NOT NULL,
                value TEXT NOT NULL,
                provenance TEXT NOT NULL,
                confirmed INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'active',
                source_turn INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                user_id TEXT NOT NULL DEFAULT 'user_default'
            );
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_memory_active
            ON memory(user_id, status, provenance, confirmed);
            """
        )
        conn.commit()

    def store(
        self,
        fact: str,
        value: str,
        provenance: str = "explicit",
        confirmed: int = 1,
        source_turn: int = 0,
        user_id: str = "user_default",
    ) -> int:
        """Store a new fact or update existing active fact with same name.

        Args:
            fact: Fact name or identifier.
            value: Associated string value.
            provenance: 'explicit' or 'inferred'.
            confirmed: 1 if confirmed, 0 if unconfirmed.
            source_turn: Current conversation turn.
            user_id: Target user ID.

        Returns:
            int: Inserted or updated record ID.
        """
        now = datetime.now(timezone.utc).isoformat()
        conn = self._get_connection()
        cursor = conn.execute(
            """
            INSERT INTO memory (
                fact, value, provenance, confirmed, status, source_turn,
                created_at, updated_at, user_id
            ) VALUES (?, ?, ?, ?, 'active', ?, ?, ?, ?)
            """,
            (fact, value, provenance, confirmed, source_turn, now, now, user_id),
        )
        conn.commit()
        return cursor.lastrowid or 0

    def get_active_facts(self, user_id: str = "user_default") -> list[MemoryRecord]:
        """Query active facts eligible for LLM context injection.

        Enforces at SQL level: forgotten facts and unconfirmed inferred facts
        are strictly excluded.

        Args:
            user_id: User identifier.

        Returns:
            list[MemoryRecord]: Eligible active facts sorted by recency.
        """
        conn = self._get_connection()
        cursor = conn.execute(
            """
            SELECT * FROM memory
            WHERE user_id = ?
              AND status = 'active'
              AND NOT (provenance = 'inferred' AND confirmed = 0)
            ORDER BY updated_at DESC
            """,
            (user_id,),
        )
        return [MemoryRecord(**dict(row)) for row in cursor.fetchall()]

    def confirm(self, fact_id: int) -> bool:
        """Promote an inferred fact to confirmed status.

        Args:
            fact_id: Record ID to confirm.

        Returns:
            bool: True if record was updated.
        """
        now = datetime.now(timezone.utc).isoformat()
        conn = self._get_connection()
        cursor = conn.execute(
            """
            UPDATE memory
            SET confirmed = 1, updated_at = ?
            WHERE id = ? AND status = 'active'
            """,
            (now, fact_id),
        )
        conn.commit()
        return cursor.rowcount > 0

    def correct(self, fact_id: int, new_value: str) -> bool:
        """Update fact value, marking provenance explicit and confirmed.

        Args:
            fact_id: Record ID to correct.
            new_value: New value string.

        Returns:
            bool: True if record was updated.
        """
        now = datetime.now(timezone.utc).isoformat()
        conn = self._get_connection()
        cursor = conn.execute(
            """
            UPDATE memory
            SET value = ?, provenance = 'explicit', confirmed = 1, updated_at = ?
            WHERE id = ? AND status = 'active'
            """,
            (new_value, now, fact_id),
        )
        conn.commit()
        return cursor.rowcount > 0

    def forget(self, fact_id: int) -> bool:
        """Mark a fact as forgotten, removing it from context queries.

        Args:
            fact_id: Record ID to forget.

        Returns:
            bool: True if record was forgotten.
        """
        now = datetime.now(timezone.utc).isoformat()
        conn = self._get_connection()
        cursor = conn.execute(
            """
            UPDATE memory
            SET status = 'forgotten', updated_at = ?
            WHERE id = ?
            """,
            (now, fact_id),
        )
        conn.commit()
        return cursor.rowcount > 0

    def list_facts(self, user_id: str = "user_default") -> list[MemoryRecord]:
        """Inspect all currently active and confirmed facts for a user.

        Args:
            user_id: Target user ID.

        Returns:
            list[MemoryRecord]: Active confirmed facts.
        """
        return self.get_active_facts(user_id)

    def load_seed_memories(
        self, json_path: str, user_id: str = "user_default"
    ) -> int:
        """Load seed memories from a JSON file into the database.

        Args:
            json_path: Path to seed memories JSON file.
            user_id: User identifier to assign memories to.

        Returns:
            int: Count of memories loaded.
        """
        with open(json_path, "r", encoding="utf-8") as f:
            items: list[dict[str, Any]] = json.load(f)

        count = 0
        for item in items:
            self.store(
                fact=item["fact"],
                value=item["value"],
                provenance=item.get("provenance", "explicit"),
                confirmed=item.get("confirmed", 1),
                source_turn=item.get("source_turn", 0),
                user_id=user_id,
            )
            count += 1
        return count

    def close(self) -> None:
        """Close database connection."""
        self._conn.close()
