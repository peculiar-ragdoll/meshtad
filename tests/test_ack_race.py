"""Regression test for synchronous-ACK race.

When meshtastic fires the routing callback synchronously inside sendText
(before sendText returns), the ACK used to be dropped because _inflight
had not yet been populated. The fix adds a deferred-acks buffer.
"""
from __future__ import annotations

import pathlib
import tempfile
import time

import pytest

from meshtad.config import Config
from meshtad.daemon import Daemon


# Re-use MockRadio pattern from test_daemon.py
class _SyncAckRadio:
    """Simulates meshtastic lib firing routing callback before sendText returns."""

    def __init__(self, port=None):
        self.port = port
        self.connected = False
        self.local_node_id = "!self12345"
        self._subscribed = False
        self._callbacks: dict = {}
        self._sent: list = []
        self._next_packet_id = 200

    def connect(self) -> bool:
        self.connected = True
        return True

    def disconnect(self) -> None:
        self.connected = False

    def send_text(self, dest: str, text: str, want_ack=True) -> tuple[bool, int | None]:
        if not self.connected:
            return False, None
        pkt_id = self._next_packet_id
        self._next_packet_id += 1

        # *** Simulate synchronous callback from meshtastic lib ***
        cb = self._callbacks.get("routing")
        if cb:
            # This runs BEFORE send_text returns in the real lib
            cb({
                "requestId": pkt_id,
                "decoded": {"routing": {"errorReason": "NONE"}},
            }, None)

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


@pytest.fixture
def db_thread(tmp_db):
    from meshtad.db import DbThread
    t = DbThread(tmp_db)
    t.start()
    t.wait_ready(timeout=5.0)
    yield t
    t.stop()


def _daemon_with_sync(tmp_db: pathlib.Path, **cfg_overrides):
    cfg = Config(db_path=tmp_db, **cfg_overrides)
    d = Daemon(cfg)
    d.radio = _SyncAckRadio()
    d.radio.subscribe(d._on_text, d._on_routing)
    return d


class TestSynchronousAckRace:
    def test_ack_before_inflight_arrives_is_not_lost(self, tmp_db, db_thread):
        """If the meshtastic ACK fires synchronously inside sendText, the message
        must still reach ACKED state after _inflight is populated."""
        d = _daemon_with_sync(tmp_db, ack_timeout_s=0.5)
        d.db = db_thread
        d.radio.connect()

        sid = db_thread.execute("INSERT INTO senders (node_id) VALUES ('!dest9876')")
        db_thread.execute(
            "INSERT INTO messages (direction, peer_id, body, state) VALUES ('out',?,?,?)",
            (sid, "hello", "QUEUED"),
        )

        # This drain will encounter the synchronous ACK inside send_text
        d._tx_drain_once()
        time.sleep(0.2)

        rows = db_thread.execute("SELECT state FROM messages WHERE direction='out'")
        assert rows[0]["state"] == "ACKED", f"Expected ACKED, got {rows[0]['state']}"
