"""Integration tests for meshtad.daemon (with mock Radio).

These tests exercise the full daemon thread model without requiring
a physical Meshtastic radio.
"""
from __future__ import annotations

import pathlib
import sqlite3
import tempfile
import time
from typing import Any

import pytest

from meshtad.config import Config
from meshtad.db import DbThread


# ---------------------------------------------------------------------------
# Mock Radio
# ---------------------------------------------------------------------------

class MockRadio:
    """Drop-in replacement for meshtad.radio.Radio in tests."""

    def __init__(self, port=None):
        self.port = port
        self.connected = False
        self.local_node_id = "!self12345"
        self._subscribed = False
        self._callbacks: dict[str, Any] = {}
        self._sent: list[tuple[str, str]] = []
        self._next_packet_id = 100
        self._fail_next = False  # if True, send_text returns False even when connected

    def connect(self) -> bool:
        self.connected = True
        return True

    def disconnect(self) -> None:
        self.connected = False

    def send_text(self, dest: str, text: str, want_ack=True) -> tuple[bool, int | None]:
        if not self.connected:
            return False, None
        if self._fail_next:
            self._fail_next = False
            return False, None
        pkt_id = self._next_packet_id
        self._next_packet_id += 1
        self._sent.append((dest, text))
        return True, pkt_id

    def subscribe(self, on_text, on_routing) -> None:
        self._callbacks["text"] = on_text
        self._callbacks["routing"] = on_routing
        self._subscribed = True

    def inject_text(self, packet: dict) -> None:
        cb = self._callbacks.get("text")
        if cb:
            cb(packet, None)

    def inject_routing(self, packet: dict) -> None:
        cb = self._callbacks.get("routing")
        if cb:
            cb(packet, None)


def _daemon_with_mock(tmp_db: pathlib.Path, **cfg_overrides):
    cfg = Config(db_path=tmp_db, **cfg_overrides)
    from meshtad.daemon import Daemon
    d = Daemon(cfg)
    d.radio = MockRadio()
    d.radio.subscribe(d._on_text, d._on_routing)
    return d


@pytest.fixture
def tmp_db() -> pathlib.Path:
    with tempfile.TemporaryDirectory() as td:
        yield pathlib.Path(td) / "test.db"


@pytest.fixture
def db_thread(tmp_db) -> DbThread:
    t = DbThread(tmp_db)
    t.start()
    t.wait_ready(timeout=5.0)
    yield t
    t.stop()


# ---------------------------------------------------------------------------
# Daemon lifecycle
# ---------------------------------------------------------------------------

class TestDaemonLifecycle:
    def test_db_ready_before_threads(self, tmp_db):
        t = DbThread(tmp_db)
        t.start()
        assert t.wait_ready(timeout=5.0)
        t.stop()


# ---------------------------------------------------------------------------
# RX (inbound) thread
# ---------------------------------------------------------------------------

class TestRxThread:
    def test_inbound_creates_sender_and_unseen_message(self, tmp_db, db_thread):
        d = _daemon_with_mock(tmp_db, redact_bodies=False)
        d.db = db_thread
        d.radio.connect()
        d.radio.inject_text({
            "fromId": "!aabbccdd",
            "toId": d.radio.local_node_id,
            "id": 42,
            "decoded": {"text": "hello daemon"},
        })
        time.sleep(0.2)
        rows = db_thread.execute(
            "SELECT m.state, m.body, s.node_id FROM messages m JOIN senders s ON s.id = m.peer_id WHERE m.direction='in'"
        )
        assert len(rows) == 1
        assert rows[0]["state"] == "UNSEEN"
        assert rows[0]["body"] == "hello daemon"
        assert rows[0]["node_id"] == "!aabbccdd"

    def test_broadcast_ignored(self, tmp_db, db_thread):
        d = _daemon_with_mock(tmp_db)
        d.db = db_thread
        d.radio.connect()
        d.radio.inject_text({
            "fromId": "!aabbccdd",
            "toId": "^all",
            "id": 1,
            "decoded": {"text": "bcast"},
        })
        time.sleep(0.2)
        rows = db_thread.execute("SELECT COUNT(*) FROM messages")
        assert rows[0][0] == 0

    def test_empty_text_ignored(self, tmp_db, db_thread):
        d = _daemon_with_mock(tmp_db)
        d.db = db_thread
        d.radio.connect()
        d.radio.inject_text({
            "fromId": "!aabbccdd",
            "toId": d.radio.local_node_id,
            "id": 1,
            "decoded": {"text": ""},
        })
        time.sleep(0.2)
        rows = db_thread.execute("SELECT COUNT(*) FROM messages")
        assert rows[0][0] == 0


# ---------------------------------------------------------------------------
# TX (drain) thread
# ---------------------------------------------------------------------------

class TestTxThread:
    def test_drains_queued_to_sent(self, tmp_db, db_thread):
        d = _daemon_with_mock(tmp_db)
        d.db = db_thread
        d.radio.connect()
        sid = db_thread.execute("INSERT INTO senders (node_id) VALUES ('!dest9876')")
        db_thread.execute(
            "INSERT INTO messages (direction, peer_id, body, state) VALUES ('out',?,?,?)",
            (sid, "hello", "QUEUED"),
        )
        d._tx_drain_once()
        rows = db_thread.execute("SELECT state, meshtastic_packet_id FROM messages WHERE direction='out'")
        assert rows[0]["state"] == "SENT"
        assert rows[0]["meshtastic_packet_id"] is not None
        assert len(d.radio._sent) == 1
        assert d.radio._sent[0] == ("!dest9876", "hello")

    def test_retry_on_send_failure(self, tmp_db, db_thread):
        d = _daemon_with_mock(tmp_db, retry_initial_s=1.0)
        d.db = db_thread
        d.radio.connect()
        d.radio._fail_next = True
        sid = db_thread.execute("INSERT INTO senders (node_id) VALUES ('!dest9876')")
        db_thread.execute(
            "INSERT INTO messages (direction, peer_id, body, state) VALUES ('out',?,?,?)",
            (sid, "hello", "QUEUED"),
        )
        d._tx_drain_once()
        rows = db_thread.execute("SELECT state, retry_count, error FROM messages WHERE direction='out'")
        assert rows[0]["state"] == "QUEUED"
        assert rows[0]["retry_count"] == 1
        assert rows[0]["error"] == "send_failed"

    def test_max_retries_to_failed(self, tmp_db, db_thread):
        d = _daemon_with_mock(tmp_db, max_retries=2, retry_initial_s=0.1)
        d.db = db_thread
        d.radio.connect()
        d.radio._fail_next = True
        sid = db_thread.execute("INSERT INTO senders (node_id) VALUES ('!dest9876')")
        db_thread.execute(
            "INSERT INTO messages (direction, peer_id, body, state, retry_count) VALUES ('out',?,?,?,2)",
            (sid, "hello", "QUEUED"),
        )
        d._tx_drain_once()
        rows = db_thread.execute("SELECT state, error FROM messages WHERE direction='out'")
        assert rows[0]["state"] == "FAILED"


# ---------------------------------------------------------------------------
# ACK / NAK handling
# ---------------------------------------------------------------------------

class TestAckNak:
    def test_ack_moves_sent_to_acked(self, tmp_db, db_thread):
        d = _daemon_with_mock(tmp_db)
        d.db = db_thread
        d.radio.connect()
        sid = db_thread.execute("INSERT INTO senders (node_id) VALUES ('!dest9876')")
        db_thread.execute(
            "INSERT INTO messages (direction, peer_id, body, state) VALUES (?,?,?,?)",
            ("out", sid, "hello", "QUEUED"),
        )
        # Drain to SENT
        d._tx_drain_once()
        rows = db_thread.execute("SELECT state, meshtastic_packet_id, id FROM messages WHERE direction='out'")
        assert rows[0]["state"] == "SENT"
        pkt_id = rows[0]["meshtastic_packet_id"]
        # Inject ACK with the real packet ID
        d.radio.inject_routing({
            "decoded": {"requestId": pkt_id, "routing": {"errorReason": "NONE"}},
        })
        time.sleep(0.2)
        rows = db_thread.execute("SELECT state FROM messages WHERE meshtastic_packet_id=?", (pkt_id,))
        assert rows[0]["state"] == "ACKED"

    def test_nak_requeues_and_resends(self, tmp_db, db_thread):
        d = _daemon_with_mock(tmp_db, max_retries=2, retry_initial_s=0.0)
        d.db = db_thread
        d.radio.connect()
        sid = db_thread.execute("INSERT INTO senders (node_id) VALUES ('!dest9876')")
        db_thread.execute(
            "INSERT INTO messages (direction, peer_id, body, state) VALUES (?,?,?,?)",
            ("out", sid, "hello", "QUEUED"),
        )
        d._tx_drain_once()
        rows = db_thread.execute("SELECT id, meshtastic_packet_id FROM messages WHERE direction='out'")
        msg_id = rows[0]["id"]
        pkt_id = rows[0]["meshtastic_packet_id"]
        # NAK requeues the message (does not leave it stuck in SENT)
        d.radio.inject_routing({
            "decoded": {"requestId": pkt_id, "routing": {"errorReason": "NO_CHANNEL"}},
        })
        time.sleep(0.2)
        rows = db_thread.execute(
            "SELECT state, retry_count, error, meshtastic_packet_id FROM messages WHERE id=?", (msg_id,)
        )
        assert rows[0]["state"] == "QUEUED"
        assert rows[0]["retry_count"] == 1
        assert "NAK" in rows[0]["error"]
        assert rows[0]["meshtastic_packet_id"] is None
        # The next drain actually puts it back on the air
        sent_before = len(d.radio._sent)
        d._tx_drain_once()
        assert len(d.radio._sent) == sent_before + 1
        rows = db_thread.execute("SELECT state FROM messages WHERE id=?", (msg_id,))
        assert rows[0]["state"] == "SENT"

    def test_nak_at_max_retries_fails(self, tmp_db, db_thread):
        d = _daemon_with_mock(tmp_db, max_retries=1, retry_initial_s=0.0)
        d.db = db_thread
        d.radio.connect()
        sid = db_thread.execute("INSERT INTO senders (node_id) VALUES ('!dest9876')")
        db_thread.execute(
            "INSERT INTO messages (direction, peer_id, body, state) VALUES (?,?,?,?)",
            ("out", sid, "hello", "QUEUED"),
        )

        def nak_current():
            row = db_thread.execute(
                "SELECT meshtastic_packet_id FROM messages WHERE direction='out'"
            )[0]
            d.radio.inject_routing({
                "requestId": row["meshtastic_packet_id"],
                "decoded": {"routing": {"errorReason": "NO_CHANNEL"}},
            })
            time.sleep(0.1)

        d._tx_drain_once()   # send #1
        nak_current()        # retry_count 0 -> 1, requeued
        d._tx_drain_once()   # send #2
        nak_current()        # retry_count 1 >= max_retries 1 -> FAILED
        rows = db_thread.execute("SELECT state, error FROM messages WHERE direction='out'")
        assert rows[0]["state"] == "FAILED"
        assert "NAK" in rows[0]["error"]

    def test_reconnect_without_crash(self, tmp_db, db_thread):
        d = _daemon_with_mock(tmp_db)
        d.db = db_thread
        assert d.radio.connect()
        assert d.radio.connected
        d.radio.disconnect()
        assert not d.radio.connected
        assert d.radio.connect()
        assert d.radio.connected


# ---------------------------------------------------------------------------
# Scheduler thread
# ---------------------------------------------------------------------------

class TestScheduler:
    def test_ack_timeout_retries(self, tmp_db, db_thread):
        d = _daemon_with_mock(tmp_db, ack_timeout_s=0.1, retry_initial_s=0.01)
        d.db = db_thread
        sid = db_thread.execute("INSERT INTO senders (node_id) VALUES ('!dest9876')")
        db_thread.execute(
            "INSERT INTO messages (direction, peer_id, body, state, sent_at, retry_count, meshtastic_packet_id) "
            "VALUES ('out',?,?,?,?,?,?)",
            (sid, "hello", "SENT", "2024-01-01T00:00:00Z", 0, 777),
        )
        d._sched_tick()
        rows = db_thread.execute("SELECT state, error, retry_count, next_attempt_at FROM messages WHERE direction='out'")
        assert rows[0]["state"] == "QUEUED"  # requeued for resend (was SENT)
        assert rows[0]["error"] == "ack_timeout"
        assert rows[0]["retry_count"] == 1
        assert rows[0]["next_attempt_at"] is not None

    def test_ack_timeout_requeues_then_resends(self, tmp_db, db_thread):
        """Regression: an unacked SENT message must be retransmitted, not just
        counted toward FAILED."""
        d = _daemon_with_mock(tmp_db, ack_timeout_s=0.1, retry_initial_s=0.0, max_retries=5)
        d.db = db_thread
        d.radio.connect()
        sid = db_thread.execute("INSERT INTO senders (node_id) VALUES ('!destAAAA')")
        db_thread.execute(
            "INSERT INTO messages (direction, peer_id, body, state) VALUES ('out',?,?,?)",
            (sid, "hello", "QUEUED"),
        )
        d._tx_drain_once()  # QUEUED -> SENT, inflight registered
        assert len(d.radio._sent) == 1
        rows = db_thread.execute("SELECT meshtastic_packet_id FROM messages WHERE direction='out'")
        pkt = rows[0]["meshtastic_packet_id"]
        # Age the inflight entry past the timeout
        with d._inflight_lock:
            d._inflight[pkt]["sent_at_mono"] = time.monotonic() - 999.0
        d._sched_tick()  # timeout -> requeue
        rows = db_thread.execute("SELECT state FROM messages WHERE direction='out'")
        assert rows[0]["state"] == "QUEUED"
        time.sleep(0.01)
        d._tx_drain_once()  # actually retransmit
        assert len(d.radio._sent) == 2
        rows = db_thread.execute("SELECT state FROM messages WHERE direction='out'")
        assert rows[0]["state"] == "SENT"

    def test_auto_delete_execution(self, tmp_db, db_thread):
        d = _daemon_with_mock(tmp_db)
        d.db = db_thread
        sid = db_thread.execute("INSERT INTO senders (node_id) VALUES ('!dest9876')")
        db_thread.execute(
            "INSERT INTO messages (direction, peer_id, body, state, auto_delete_at) "
            "VALUES ('in',?,?,?,'2000-01-01T00:00:00Z')",
            (sid, "old", "SEEN"),
        )
        d._sched_tick()
        rows = db_thread.execute("SELECT state FROM messages WHERE body='old'")
        assert rows[0]["state"] == "DELETED"

    def test_control_queue_eject(self, tmp_db, db_thread):
        d = _daemon_with_mock(tmp_db)
        d.db = db_thread
        d.radio.connect()
        db_thread.execute("INSERT INTO control_queue (action) VALUES ('eject')")
        d._sched_tick()
        assert not d.radio.connected


# ---------------------------------------------------------------------------
# End-to-end state machine
# ---------------------------------------------------------------------------

class TestEndToEnd:
    def test_full_lifecycle_inbound(self, tmp_db, db_thread):
        """Inbound: UNSEEN -> SEEN -> DELETED."""
        d = _daemon_with_mock(tmp_db)
        d.db = db_thread
        d.radio.connect()
        d.radio.inject_text({
            "fromId": "!peer0001",
            "toId": d.radio.local_node_id,
            "id": 1,
            "decoded": {"text": "hi"},
        })
        time.sleep(0.2)
        rows = db_thread.execute("SELECT id, state FROM messages WHERE direction='in'")
        msg_id, state = rows[0]
        assert state == "UNSEEN"
        from meshtad.db import DbClient
        client = DbClient(tmp_db)
        client.mark_read(msg_id, auto_delete_after_s=0)
        d._sched_tick()
        rows = db_thread.execute("SELECT state FROM messages WHERE id=?", (msg_id,))
        assert rows[0]["state"] == "DELETED"

    def test_full_lifecycle_outbound(self, tmp_db, db_thread):
        """Outbound: QUEUED -> SENT -> ACKED."""
        d = _daemon_with_mock(tmp_db)
        d.db = db_thread
        d.radio.connect()
        from meshtad.db import DbClient
        client = DbClient(tmp_db)
        sid = client.ensure_sender("!dest9999")
        mid = client.enqueue_outbound(sid, "test")
        d._tx_drain_once()
        rows = db_thread.execute("SELECT state, meshtastic_packet_id FROM messages WHERE id=?", (mid,))
        assert rows[0]["state"] == "SENT"
        pkt_id = rows[0]["meshtastic_packet_id"]
        d.radio.inject_routing({
            "decoded": {"requestId": pkt_id, "routing": {"errorReason": "NONE"}},
        })
        time.sleep(0.2)
        rows = db_thread.execute("SELECT state FROM messages WHERE id=?", (mid,))
        assert rows[0]["state"] == "ACKED"

    def test_queued_survives_reconnect(self, tmp_db, db_thread):
        """Queued messages survive radio disconnect and send after reconnect."""
        d = _daemon_with_mock(tmp_db)
        d.db = db_thread
        d.radio.connect()
        from meshtad.db import DbClient
        client = DbClient(tmp_db)
        sid = client.ensure_sender("!dest9999")
        mid = client.enqueue_outbound(sid, "important")
        # Disconnect before drain
        d.radio.disconnect()
        d._tx_drain_once()
        rows = db_thread.execute("SELECT state, retry_count FROM messages WHERE id=?", (mid,))
        assert rows[0]["state"] == "QUEUED"
        assert rows[0]["retry_count"] == 1
        # Reconnect and drain
        d.radio.connect()
        db_thread.execute(
            "UPDATE messages SET next_attempt_at = strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id=?",
            (mid,),
        )
        d._tx_drain_once()
        rows = db_thread.execute("SELECT state FROM messages WHERE id=?", (mid,))
        assert rows[0]["state"] == "SENT"
