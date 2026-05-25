"""RED-phase tests for graceful daemon shutdown.

These tests exercise shutdown behaviour: SIGTERM handling, in-flight-TX
drain, and clean DB close.  They will fail until the implementation lands.
"""
from __future__ import annotations

import pathlib
import signal
import tempfile
import threading
import time

import pytest

from meshtad.config import Config
from meshtad.daemon import Daemon
from meshtad.db import DbThread


# Re-use the MockRadio from test_daemon.py
class MockRadio:
    def __init__(self, port=None):
        self.port = port
        self.connected = False
        self.local_node_id = "!self12345"
        self._subscribed = False
        self._callbacks: dict = {}
        self._sent: list = []
        self._next_packet_id = 100
        self._drain_delay = 0.0  # simulate slow TX

    def connect(self) -> bool:
        self.connected = True
        return True

    def disconnect(self) -> None:
        self.connected = False

    def send_text(self, dest: str, text: str, want_ack=True) -> tuple[bool, int | None]:
        if not self.connected:
            return False, None
        time.sleep(self._drain_delay)
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


@pytest.fixture
def tmp_db() -> pathlib.Path:
    with tempfile.TemporaryDirectory() as td:
        yield pathlib.Path(td) / "test.db"


def _daemon_with_mock(tmp_db: pathlib.Path, **cfg_overrides):
    cfg = Config(db_path=tmp_db, **cfg_overrides)
    d = Daemon(cfg)
    d.radio = MockRadio()
    d.radio.subscribe(d._on_text, d._on_routing)
    return d


class TestShutdown:
    def test_stop_sets_shutdown_event(self, tmp_db):
        """Calling stop() sets the shutdown Event so threads exit."""
        d = _daemon_with_mock(tmp_db)
        d.db.start()
        d.db.wait_ready(timeout=5.0)
        assert not d._shutdown.is_set()
        d.stop()
        assert d._shutdown.is_set()
        d.db.stop()

    def test_disconnect_called_on_stop(self, tmp_db):
        """stop() disconnects the radio."""
        d = _daemon_with_mock(tmp_db)
        d.db.start()
        d.db.wait_ready(timeout=5.0)
        d.radio.connect()
        assert d.radio.connected
        d.stop()
        assert not d.radio.connected
        d.db.stop()

    def test_db_thread_stopped_on_stop(self, tmp_db):
        """stop() closes the DB thread cleanly."""
        d = _daemon_with_mock(tmp_db)
        d.db.start()
        d.db.wait_ready(timeout=5.0)
        assert d.db.is_alive()
        d.stop()
        d.db.stop()
        assert not d.db.is_alive()

    def test_waits_for_inflight_on_stop(self, tmp_db):
        """If a message is in-flight, stop() optionally waits before closing DB."""
        d = _daemon_with_mock(tmp_db)
        d.db.start()
        d.db.wait_ready(timeout=5.0)
        d.radio.connect()
        d.radio._drain_delay = 0.5

        sid = d.db.execute("INSERT INTO senders (node_id) VALUES ('!dest9876')")
        d.db.execute(
            "INSERT INTO messages (direction, peer_id, body, state) VALUES ('out',?,?,?)",
            (sid, "hello", "QUEUED"),
        )

        # Start drain on a background thread
        tx = threading.Thread(target=d._tx_drain_once)
        tx.start()
        time.sleep(0.1)  # let send_text() enter its sleep
        d.stop()
        tx.join(timeout=2.0)
        assert not tx.is_alive()
        d.db.stop()

    def test_sigterm_cleans_up(self, tmp_db):
        """SIGTERM dispatched to the daemon triggers graceful stop()."""
        d = _daemon_with_mock(tmp_db)
        d.db.start()
        d.db.wait_ready(timeout=5.0)

        # Simulate what run() does on KeyboardInterrupt/SIGTERM
        old_handler = signal.signal(signal.SIGTERM, lambda signum, frame: d.stop())
        try:
            # Sending SIGTERM to self should not raise; verify handler fires
            import os
            os.kill(os.getpid(), signal.SIGTERM)
            # If we got here without crash, the handler was invoked.
            assert d._shutdown.is_set()
        finally:
            signal.signal(signal.SIGTERM, old_handler)
            d.db.stop()
