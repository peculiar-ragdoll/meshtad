"""Regression test for monotonic-clock ACK timeout."""
from __future__ import annotations

import pathlib
import tempfile
import time

import pytest

from meshtad.config import Config
from meshtad.daemon import Daemon


class _StubRadio:
    def __init__(self, port=None):
        self.port = port
        self.connected = False
        self.local_node_id = "!self0001"
        self._callbacks: dict = {}

    def connect(self) -> bool:
        self.connected = True
        return True

    def disconnect(self) -> None:
        self.connected = False

    def send_text(self, dest: str, text: str, want_ack=True):
        return True, 777

    def subscribe(self, on_text, on_routing) -> None:
        self._callbacks["text"] = on_text
        self._callbacks["routing"] = on_routing


@pytest.fixture
def tmp_db() -> pathlib.Path:
    with tempfile.TemporaryDirectory() as td:
        yield pathlib.Path(td) / "test.db"


@pytest.fixture
def db_thread(tmp_db):
    from meshtad.db import DbThread
    t = DbThread(tmp_db)
    t.start()
    t.wait_ready(timeout=5.0)
    yield t
    t.stop()


class TestMonotonicTimeout:
    def test_ack_timeout_uses_monotonic_clock(self, tmp_db, db_thread, monkeypatch):
        """Even if wall-clock time.time() jumps backward, the ACK timeout
        must still fire correctly thanks to monotonic tracking."""
        d = Daemon(Config(db_path=tmp_db, ack_timeout_s=10.0))
        d.db = db_thread
        d.radio = _StubRadio()
        d.radio.subscribe(d._on_text, d._on_routing)
        d.radio.connect()

        # Seed a SENT message with an old monotonic timestamp
        fake_mono = 1000.0
        with d._inflight_lock:
            d._inflight[777] = {
                "msg_id": 1,
                "sent_at": 1234567890.0,
                "sent_at_mono": fake_mono,
            }

        db_thread.execute(
            "INSERT OR IGNORE INTO senders (node_id) VALUES ('!dest0001')"
        )
        db_thread.execute(
            "INSERT INTO messages (direction, peer_id, body, state, meshtastic_packet_id, retry_count, sent_at) "
            "VALUES ('out',1,'x','SENT',777,0,'2020-01-01T00:00:00Z')"
        )

        # Now simulate time.monotonic advancing past the timeout
        monkeypatch.setattr(time, "monotonic", lambda: fake_mono + 11.0)
        # Wall clock jumps *backward* — if we used time.time() it would NOT fire
        monkeypatch.setattr(time, "time", lambda: 1000.0)

        d._sched_tick()

        # A non-final ACK timeout requeues the message (SENT -> QUEUED) so the TX drain
        # resends it after next_attempt_at, and clears sent_at / packet_id so it leaves
        # the scheduler's SENT-timeout scan and a stale ACK can't match the resend.
        rows = db_thread.execute(
            "SELECT state, retry_count, error, next_attempt_at, sent_at, meshtastic_packet_id "
            "FROM messages WHERE id=1"
        )
        assert rows[0]["state"] == "QUEUED"
        assert rows[0]["retry_count"] == 1
        assert "timeout" in rows[0]["error"]
        assert rows[0]["next_attempt_at"] is not None
        assert rows[0]["sent_at"] is None
        assert rows[0]["meshtastic_packet_id"] is None

    def test_no_false_timeout_on_monotonic_boundary(self, tmp_db, db_thread, monkeypatch):
        """If mono has NOT crossed the threshold, no timeout should fire
        even if wall clock is far in the future."""
        d = Daemon(Config(db_path=tmp_db, ack_timeout_s=10.0))
        d.db = db_thread
        d.radio = _StubRadio()
        d.radio.subscribe(d._on_text, d._on_routing)

        fake_mono = 5000.0
        with d._inflight_lock:
            d._inflight[888] = {
                "msg_id": 2,
                "sent_at": time.time(),
                "sent_at_mono": fake_mono,
            }

        db_thread.execute(
            "INSERT OR IGNORE INTO senders (node_id) VALUES ('!dest0002')"
        )
        db_thread.execute(
            "INSERT INTO messages (direction, peer_id, body, state, meshtastic_packet_id, retry_count, sent_at) "
            "VALUES ('out',1,'x','SENT',888,0,?)",
            ("2020-01-01T00:00:00Z",),
        )

        # Mono has only advanced 5 seconds (under 10s timeout)
        monkeypatch.setattr(time, "monotonic", lambda: fake_mono + 5.0)
        # Wall clock is years ahead — should be ignored
        monkeypatch.setattr(time, "time", lambda: 9999999999.0)

        d._sched_tick()

        rows = db_thread.execute("SELECT state, error FROM messages WHERE meshtastic_packet_id=888")
        assert rows[0]["state"] == "SENT"
        assert rows[0]["error"] is None
