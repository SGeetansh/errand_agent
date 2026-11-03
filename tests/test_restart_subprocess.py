"""Subprocess crash and restart test for write-ahead intent log reconciliation."""

import os
import subprocess
import sys
import tempfile
import time
import pytest

from src.idempotency import derive_idempotency_key
from src.recovery import IntentLog, RecoveryManager
from src.tools.mock_env import MockEnvironment
from src.tools.scenario_config import ScenarioConfig


def test_kill_real_subprocess_mid_booking_and_reconcile() -> None:
    """Kill a real subprocess mid-booking and verify restart reconciliation against source of truth.

    State persistence semantics:
    - Persisted: Write-ahead intent log (idempotency key, action, provider, slot, quoted price, hold ID, grant ID, status).
    - Not persisted: Transient in-memory state (ephemeral Python state machine objects, in-memory grant objects, active slots).
    - User outcome: On restart, RecoveryManager reconciles against authoritative upstream source of truth
      (get_booking_status). If upstream confirmed before crash, status resolves to confirmed; if upstream never
      received the call, status resolves to failed, safely preventing duplicate bookings.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "recovery_test.db")
        idem_key = derive_idempotency_key("user_default", "book", "P-PLUM-01", "2024-01-15T10:00:00")

        # Worker script that logs intent to WAL, simulates in-flight call, and waits for kill signal
        worker_code = f"""
import time
import sys
from src.recovery import IntentLog

wal = IntentLog(db_path={repr(db_path)})
wal.log_intent(
    idempotency_key={repr(idem_key)},
    action="book",
    provider_id="P-PLUM-01",
    slot="2024-01-15T10:00:00",
    quoted_price=1200.0,
    grant_id="grant-uuid-1234",
    hold_id="HOLD-9999",
)
wal.update_status({repr(idem_key)}, "sent")
# Signal ready
sys.stdout.write("LOGGED\\n")
sys.stdout.flush()

# Hang to simulate mid-booking crash before final status write
time.sleep(30)
"""
        proc = subprocess.Popen(
            [sys.executable, "-c", worker_code],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        try:
            # Wait for worker to log intent
            line = proc.stdout.readline()
            assert "LOGGED" in line

            # Confirm intent is recorded on disk
            wal_pre = IntentLog(db_path=db_path)
            intent_pre = wal_pre.get_intent(idem_key)
            assert intent_pre is not None
            assert intent_pre["status"] == "sent"
            wal_pre.close()

            # SIGKILL the process mid-flight
            proc.kill()
            proc.wait(timeout=5)
            assert proc.poll() is not None  # Process is dead

        finally:
            if proc.poll() is None:
                proc.kill()

        # RESTART PHASE: Spawn new process/manager on the SAME disk database
        mock_env = MockEnvironment(ScenarioConfig())
        wal_restart = IntentLog(db_path=db_path)
        recovery = RecoveryManager(wal_restart, mock_env)

        # Before reconcile: intent is still unresolved in 'sent' state
        pending = wal_restart.get_pending_intents()
        assert len(pending) == 1
        assert pending[0]["idempotency_key"] == idem_key

        # Run startup reconciliation
        reconciled_actions = recovery.reconcile()
        assert len(reconciled_actions) == 1
        assert reconciled_actions[0]["key"] == idem_key

        # Upstream source of truth had not booked -> status updated to failed
        intent_post = wal_restart.get_intent(idem_key)
        assert intent_post["status"] == "failed"
        assert "reconciled_status_" in intent_post["last_error"]

        wal_restart.close()
