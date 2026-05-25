"""Regression test for broadcast filter completeness."""
from __future__ import annotations

import pathlib
import tempfile
import time

import pytest

from meshtad.config import Config
from meshtad.daemon import Daemon


class MockRadio:
    def __init__(self, port=None):
        self.port = port
        self.connected = False
        self.local_node_id = "!self0001"
        self._callbacks: dict = {}
        self._subscribed = False

    def connect(self) -> bool:
        self.connected = True
        return True

    def disconnect(self) -> None:
        self.connected = False

    def send_text(self, dest: str, text: str, want_ack=True):
        return True, 1

    def subscribe(self, on_text, on_routing) -> None:
        self._callbacks["text"] = on_text
        self._callbacks["routing"] = on_routing
        self._subscribed = True

    def inject_text(self, packet: dict) -> None:
        cb = self._callbacks.get("text")
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


class TestBroadcastFilter:
    def test_bang_ffffffff_ignored(self, tmp_db, db_thread):
        """!ffffffff (the canonical broadcast form) must be dropped."""
        d = Daemon(Config(db_path=tmp_db))
        d.db = db_thread
        d.radio = MockRadio()
        d.radio.subscribe(d._on_text, d._on_routing)
        d.radio.connect()
        d.radio.inject_text({
            "fromId": "!aabbccdd",
            "toId": "!ffffffff",
            "id": 1,
            "decoded": {"text": "bcast"},
        })
        time.sleep(0.2)
        rows = db_thread.execute("SELECT COUNT(*) FROM messages")
        assert rows[0][0] == 0

    def test_numeric_ffffffff_ignored(self, tmp_db, db_thread):
        """ffffffff (numeric, no bang) must also be dropped."""
        d = Daemon(Config(db_path=tmp_db))
        d.db = db_thread
        d.radio = MockRadio()
        d.radio.subscribe(d._on_text, d._on_routing)
        d.radio.connect()
        d.radio.inject_text({
            "fromId": "!aabbccdd",
            "toId": "ffffffff",
            "id": 1,
            "decoded": {"text": "bcast"},
        })
        time.sleep(0.2)
        rows = db_thread.execute("SELECT COUNT(*) FROM messages")
        assert rows[0][0] == 0

    def test_dm_with_valid_dest_is_accepted(self, tmp_db, db_thread):
        """A real DM to our node must create a message."""
        d = Daemon(Config(db_path=tmp_db))
        d.db = db_thread
        d.radio = MockRadio()
        d.radio.subscribe(d._on_text, d._on_routing)
        d.radio.connect()
        d.radio.inject_text({
            "fromId": "!aabbccdd",
            "toId": d.radio.local_node_id,
            "id": 1,
            "decoded": {"text": "hello"},
        })
        time.sleep(0.2)
        rows = db_thread.execute("SELECT COUNT(*) FROM messages")
        assert rows[0][0] == 1
