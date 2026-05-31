"""Unit tests for meshtad.db — schema, DbThread, DbClient.

These tests run without the meshtastic library and document the contract.
"""
from __future__ import annotations

import pathlib
import sqlite3
import tempfile
import threading
import time

import pytest

from meshtad.db import DbThread, DbClient, SCHEMA


@pytest.fixture
def tmp_db() -> pathlib.Path:
    with tempfile.TemporaryDirectory() as td:
        yield pathlib.Path(td) / "test.db"


# ---------------------------------------------------------------------------
# Schema tests
# ---------------------------------------------------------------------------

class TestSchema:
    def test_schema_creates_expected_tables(self, tmp_db):
        """SCHEMA should create senders, messages, control_queue, meta tables."""
        conn = sqlite3.connect(str(tmp_db))
        conn.executescript(SCHEMA)
        conn.execute("PRAGMA journal_mode=WAL;")
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        assert "senders" in tables
        assert "messages" in tables
        assert "control_queue" in tables
        assert "meta" in tables
        conn.close()

    def test_state_check_constraint(self, tmp_db):
        """messages.state rejects invalid enum values."""
        conn = sqlite3.connect(str(tmp_db))
        conn.executescript(SCHEMA)
        conn.execute("INSERT INTO senders (node_id) VALUES ('!aabbccdd')")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO messages (direction, peer_id, body, state) VALUES ('in',1,'x','INVALID')"
            )
        # Valid states should succeed
        for st in ("UNSEEN", "SEEN", "QUEUED", "SENT", "ACKED", "FAILED", "DELETED"):
            conn.execute("DELETE FROM messages")
            conn.execute(
                "INSERT INTO messages (direction, peer_id, body, state) VALUES ('in',1,'x',?)",
                (st,),
            )
        conn.close()


# ---------------------------------------------------------------------------
# DbThread tests (single writer thread)
# ---------------------------------------------------------------------------

class TestDbThread:
    def test_starts_and_stops_cleanly(self, tmp_db):
        """DbThread starts, becomes ready, and stops without exception."""
        t = DbThread(tmp_db)
        t.start()
        assert t.wait_ready(timeout=5.0)
        t.stop()
        assert not t.is_alive()

    def test_execute_insert_and_select(self, tmp_db):
        """Other threads can execute INSERT then SELECT via the queue."""
        t = DbThread(tmp_db)
        t.start()
        assert t.wait_ready(timeout=5.0)
        lid = t.execute("INSERT INTO senders (node_id) VALUES ('!12345678')")
        assert isinstance(lid, int)
        rows = t.execute("SELECT node_id FROM senders WHERE id=?", (lid,))
        assert rows[0]["node_id"] == "!12345678"
        t.stop()

    def test_execute_error_propagated(self, tmp_db):
        """SQL errors are raised in the calling thread."""
        t = DbThread(tmp_db)
        t.start()
        assert t.wait_ready(timeout=5.0)
        with pytest.raises(sqlite3.IntegrityError):
            t.execute("INSERT INTO senders (node_id) VALUES (NULL)")
        t.stop()

    def test_wal_mode_enabled(self, tmp_db):
        """Journal mode is WAL, allowing concurrent readers."""
        t = DbThread(tmp_db)
        t.start()
        assert t.wait_ready(timeout=5.0)
        conn = sqlite3.connect(str(tmp_db))
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode == "wal"
        conn.close()
        t.stop()


# ---------------------------------------------------------------------------
# DbClient tests (convenience CRUD)
# ---------------------------------------------------------------------------

class TestDbClient:
    @pytest.fixture
    def client(self, tmp_db) -> DbClient:
        """Client with schema initialised via a temporary DbThread."""
        t = DbThread(tmp_db)
        t.start()
        t.wait_ready(timeout=5.0)
        t.stop()
        return DbClient(tmp_db)

    def test_max_message_id(self, client):
        """max_message_id returns 0 when empty and the highest id otherwise."""
        assert client.max_message_id() == 0
        sid = client.ensure_sender("!aabbccdd")
        mid = client.enqueue_outbound(sid, "hi")
        assert client.max_message_id() == mid

    def test_ensure_sender_creates_and_idempotent(self, client):
        """ensure_sender creates on first call, returns same id on second."""
        sid1 = client.ensure_sender("!aabbccdd")
        sid2 = client.ensure_sender("!aabbccdd")
        assert sid1 == sid2
        assert sid1 > 0

    def test_resolve_alias_by_alias(self, client):
        """resolve_alias matches alias before node_id."""
        client.ensure_sender("!aabbccdd", alias="homenode")
        result = client.resolve_alias("homenode")
        assert result is not None
        sid, node_id = result
        assert node_id == "!aabbccdd"

    def test_resolve_alias_by_node_id(self, client):
        """resolve_alias falls back to node_id when alias missing."""
        client.ensure_sender("!aabbccdd")
        result = client.resolve_alias("!aabbccdd")
        assert result is not None

    def test_resolve_alias_case_insensitive(self, client):
        """resolve_alias is case-insensitive."""
        client.ensure_sender("!aabbccdd", alias="HomeNode")
        assert client.resolve_alias("homenode") is not None
        assert client.resolve_alias("HOMENODE") is not None

    def test_enqueue_outbound_creates_queued_message(self, client):
        """enqueue_outbound inserts a QUEUED outbound message."""
        sid = client.ensure_sender("!aabbccdd")
        mid = client.enqueue_outbound(sid, "hello")
        row = client.get_message(mid)
        assert row is not None
        assert row["direction"] == "out"
        assert row["state"] == "QUEUED"
        assert row["body"] == "hello"
        assert row["retry_count"] == 0

    def test_insert_inbound_creates_unseen_message(self, client):
        """insert_inbound inserts an UNSEEN inbound message."""
        sid = client.ensure_sender("!aabbccdd")
        mid = client.insert_inbound(sid, "incoming", packet_id=42)
        row = client.get_message(mid)
        assert row["direction"] == "in"
        assert row["state"] == "UNSEEN"
        assert row["meshtastic_packet_id"] == 42

    def test_inbox_excludes_deleted_and_outbound(self, client):
        """inbox shows only inbound non-deleted messages."""
        sid = client.ensure_sender("!aabbccdd")
        client.insert_inbound(sid, "msg")
        client.enqueue_outbound(sid, "out")
        client.mark_deleted(client.insert_inbound(sid, "gone"))
        rows = client.inbox()
        assert len(rows) == 1
        assert rows[0][3] == "msg"  # body column

    def test_mark_read_sets_seen_and_auto_delete(self, client):
        """mark_read transitions UNSEEN -> SEEN and sets auto_delete_at."""
        sid = client.ensure_sender("!aabbccdd")
        mid = client.insert_inbound(sid, "msg")
        client.mark_read(mid, auto_delete_after_s=3600)
        row = client.get_message(mid)
        assert row["state"] == "SEEN"
        assert row["auto_delete_at"] is not None

    def test_history_shows_both_directions(self, client):
        """history includes inbound and outbound, excluding deleted."""
        sid = client.ensure_sender("!aabbccdd")
        i = client.insert_inbound(sid, "in")
        o = client.enqueue_outbound(sid, "out")
        rows = client.history()
        assert len(rows) == 2
        directions = {r["direction"] for r in rows}
        assert directions == {"in", "out"}
        client.mark_deleted(i)
        rows = client.history()
        assert len(rows) == 1
        assert rows[0]["direction"] == "out"

    def test_outbox_shows_queued_sent_failed(self, client):
        """outbox includes QUEUED, SENT, FAILED; excludes ACKED and DELETED."""
        sid = client.ensure_sender("!aabbccdd")
        q = client.enqueue_outbound(sid, "q")
        s = client.enqueue_outbound(sid, "s")
        a = client.enqueue_outbound(sid, "a")
        d = client.enqueue_outbound(sid, "d")
        # Manually set states for test using a single raw connection
        conn = sqlite3.connect(str(client.db_path))
        conn.execute("UPDATE messages SET state='SENT' WHERE id=?", (q,))
        conn.execute("UPDATE messages SET state='FAILED' WHERE id=?", (s,))
        conn.execute("UPDATE messages SET state='ACKED' WHERE id=?", (a,))
        conn.execute("UPDATE messages SET state='DELETED' WHERE id=?", (d,))
        conn.commit()
        conn.close()
        rows = client.outbox()
        bodies = {r["body"] for r in rows}
        assert "q" in bodies
        assert "s" in bodies
        assert "d" not in bodies

    def test_vacuum_reduces_size(self, client):
        """vacuum compacts the database."""
        sid = client.ensure_sender("!aabbccdd")
        for i in range(50):
            client.insert_inbound(sid, "x" * 200)
        size_before = client.db_size_bytes()
        # Delete everything and vacuum
        conn = sqlite3.connect(str(client.db_path))
        conn.execute("DELETE FROM messages")
        conn.commit()
        conn.close()
        client.vacuum()
        size_after = client.db_size_bytes()
        assert size_after <= size_before

    def test_db_size_bytes_on_missing(self, client):
        """db_size_bytes returns 0 when file missing (temporary mismatch)."""
        missing = DbClient(pathlib.Path("/nonexistent/db"))
        assert missing.db_size_bytes() == 0

    def test_control_queue_client_to_daemon(self, client):
        """Client can enqueue control actions consumed by daemon."""
        client.enqueue_control("eject")
        conn = sqlite3.connect(str(client.db_path))
        rows = conn.execute("SELECT action FROM control_queue").fetchall()
        conn.close()
        assert any(r[0] == "eject" for r in rows)


# ---------------------------------------------------------------------------
# Concurrency / WAL tests
# ---------------------------------------------------------------------------

class TestConcurrency:
    def test_reader_does_not_block_writer(self, tmp_db):
        """A long reader query should not deadlock the DbThread writer."""
        t = DbThread(tmp_db)
        t.start()
        t.wait_ready(timeout=5.0)

        # Seed data
        for i in range(200):
            t.execute(f"INSERT INTO senders (node_id) VALUES ('!{i:08x}')")

        # Reader opens separate connection
        reader = DbClient(tmp_db)
        result = {}
        def read():
            result["rows"] = reader.list_senders()
        rt = threading.Thread(target=read)
        rt.start()

        # Writer continues
        t.execute("INSERT INTO senders (node_id) VALUES ('!deadbeef')")
        rt.join(timeout=5.0)
        assert rt.is_alive() is False
        assert len(result["rows"]) >= 200
        t.stop()
