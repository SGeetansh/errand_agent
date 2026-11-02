"""Unit tests for MemoryStore: CRUD, SQL-level query filters, and seed memory loading."""

import pytest

from src.memory import MemoryStore


@pytest.fixture
def memory_store() -> MemoryStore:
    """Fixture providing an in-memory MemoryStore instance.

    Returns:
        MemoryStore: Initialized in-memory store.
    """
    return MemoryStore(db_path=":memory:")


def test_memory_crud_explicit(memory_store: MemoryStore) -> None:
    """Test storing and reading explicit facts."""
    fact_id = memory_store.store(
        fact="preferred_area",
        value="Koramangala",
        provenance="explicit",
        confirmed=1,
    )
    assert fact_id > 0

    facts = memory_store.get_active_facts()
    assert len(facts) == 1
    rec = facts[0]
    assert rec.fact == "preferred_area"
    assert rec.value == "Koramangala"
    assert rec.provenance == "explicit"
    assert rec.confirmed == 1
    assert rec.status == "active"


def test_unconfirmed_inferred_facts_excluded_at_sql_level(memory_store: MemoryStore) -> None:
    """Test that unconfirmed inferred facts are unreachable via get_active_facts."""
    # Store explicit confirmed fact
    memory_store.store("explicit_pref", "val1", provenance="explicit", confirmed=1)

    # Store inferred unconfirmed fact
    inferred_id = memory_store.store("inferred_pref", "val2", provenance="inferred", confirmed=0)

    active = memory_store.get_active_facts()
    assert len(active) == 1
    assert active[0].fact == "explicit_pref"

    # Promoting to confirmed makes it reachable
    memory_store.confirm(inferred_id)
    active_after = memory_store.get_active_facts()
    assert len(active_after) == 2
    assert any(f.fact == "inferred_pref" for f in active_after)


def test_correct_fact(memory_store: MemoryStore) -> None:
    """Test correcting a fact updates value and marks provenance explicit."""
    fact_id = memory_store.store("preferred_area", "Koramangala", provenance="inferred", confirmed=1)
    memory_store.correct(fact_id, "Indiranagar")

    active = memory_store.get_active_facts()
    assert len(active) == 1
    assert active[0].value == "Indiranagar"
    assert active[0].provenance == "explicit"
    assert active[0].confirmed == 1


def test_forget_fact_excluded_at_sql_level(memory_store: MemoryStore) -> None:
    """Test that forgotten facts are excluded by query filter but retained for audit."""
    fact_id = memory_store.store("time_pref", "mornings only", provenance="explicit", confirmed=1)
    assert len(memory_store.get_active_facts()) == 1

    # Forget fact
    success = memory_store.forget(fact_id)
    assert success
    assert len(memory_store.get_active_facts()) == 0

    # Verify audit row still exists in SQLite table with status 'forgotten'
    conn = memory_store._get_connection()
    row = conn.execute("SELECT * FROM memory WHERE id = ?", (fact_id,)).fetchone()
    assert row is not None
    assert row["status"] == "forgotten"


def test_load_seed_memories_and_filter(memory_store: MemoryStore) -> None:
    """Test loading 50+ seed memories and asserting unconfirmed items are filtered."""
    count = memory_store.load_seed_memories("eval/seed_memories.json")
    assert count >= 50

    active_facts = memory_store.get_active_facts()
    # Ensure unconfirmed inferred items were not returned
    for f in active_facts:
        assert f.status == "active"
        if f.provenance == "inferred":
            assert f.confirmed == 1
