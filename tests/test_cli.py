"""Unit tests for meshtad.cli (meshcli).

Each test invokes the CLI entry point with patched sys.argv and temporary DB.
"""
from __future__ import annotations

import io
import pathlib
import sqlite3
import sys
from contextlib import redirect_stdout
from unittest.mock import patch

import pytest

from meshtad.cli import main
from meshtad.db import DbThread, DbClient


@pytest.fixture
def tmp_db() -> pathlib.Path:
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        p = pathlib.Path(td) / "test.db"
        # Initialize schema so CLI can use the db directly
        t = DbThread(p)
        t.start()
        t.wait_ready(timeout=5.0)
        t.stop()
        yield p


@pytest.fixture
def client(tmp_db) -> DbClient:
    t = DbThread(tmp_db)
    t.start()
    t.wait_ready(timeout=5.0)
    t.stop()
    return DbClient(tmp_db)


def _run(*args: str) -> tuple[int, str]:
    """Run CLI with given args, return (exit_code, stdout)."""
    buf = io.StringIO()
    with patch.object(sys, "argv", ["meshcli", "--db", str(args[0]), *args[1:]]):
        with redirect_stdout(buf):
            try:
                code = main()
            except SystemExit as exc:
                code = exc.code if isinstance(exc.code, int) else 0
    return code, buf.getvalue()


class TestSend:
    def test_send_queues_outbound(self, tmp_db, client):
        """send creates a QUEUED outbound message."""
        client.ensure_sender("!aabbccdd", alias="home")
        code, out = _run(tmp_db, "send", "home", "hello")
        assert code == 0
        assert "queued msg_id=" in out
        # Verify DB state
        rows = client.outbox()
        assert len(rows) == 1
        assert rows[0]["body"] == "hello"
        assert rows[0]["state"] == "QUEUED"

    def test_send_rejects_oversized(self, tmp_db, client):
        """send rejects messages exceeding MAX_PAYLOAD_BYTES."""
        client.ensure_sender("!aabbccdd", alias="home")
        big = "x" * 300
        code, out = _run(tmp_db, "send", "home", big)
        assert code == 1
        assert "message too long" in out
        assert client.outbox() == []

    def test_send_rejects_unknown_alias(self, tmp_db):
        """send exits with error when alias not found."""
        code, out = _run(tmp_db, "send", "nobody", "hello")
        assert code == 1
        assert "no alias or node-id matches" in out

    def test_send_multiword_body(self, tmp_db, client):
        """send captures remainder arguments as body."""
        client.ensure_sender("!aabbccdd", alias="home")
        code, out = _run(tmp_db, "send", "home", "hello", "world", "foo")
        assert code == 0
        rows = client.outbox()
        assert rows[0]["body"] == "hello world foo"


class TestInbox:
    def test_inbox_empty(self, tmp_db):
        code, out = _run(tmp_db, "inbox")
        assert code == 0
        assert "(empty)" in out

    def test_inbox_shows_unseen_flag(self, tmp_db, client):
        """UNSEEN messages show [*]; SEEN shows [ ]."""
        sid = client.ensure_sender("!aabbccdd", alias="home")
        client.insert_inbound(sid, "unread")
        i2 = client.insert_inbound(sid, "read")
        client.mark_read(i2)
        code, out = _run(tmp_db, "inbox")
        assert code == 0
        assert "[*]" in out
        assert "[ ]" in out

    def test_inbox_unseen_only(self, tmp_db, client):
        sid = client.ensure_sender("!aabbccdd")
        client.insert_inbound(sid, "alpha")
        i2 = client.insert_inbound(sid, "bravo")
        client.mark_read(i2)
        code, out = _run(tmp_db, "inbox", "--unseen-only")
        assert code == 0
        assert "alpha" in out
        assert "bravo" not in out


class TestRead:
    def test_read_marks_seen(self, tmp_db, client):
        """read marks UNSEEN -> SEEN and prints body."""
        sid = client.ensure_sender("!aabbccdd", alias="home")
        mid = client.insert_inbound(sid, "secret")
        code, out = _run(tmp_db, "read", str(mid))
        assert code == 0
        assert "secret" in out
        row = client.get_message(mid)
        assert row["state"] == "SEEN"

    def test_read_sets_auto_delete(self, tmp_db, client):
        """read schedules auto_delete when sender has policy."""
        sid = client.ensure_sender("!aabbccdd", alias="home")
        client.update_sender(sid, auto_delete_after_s=60)
        mid = client.insert_inbound(sid, "msg")
        code, out = _run(tmp_db, "read", str(mid))
        row = client.get_message(mid)
        assert row["auto_delete_at"] is not None

    def test_read_outbound_rejected(self, tmp_db, client):
        sid = client.ensure_sender("!aabbccdd")
        mid = client.enqueue_outbound(sid, "out")
        code, out = _run(tmp_db, "read", str(mid))
        assert code == 1
        assert "outbound" in out

    def test_read_missing(self, tmp_db):
        code, out = _run(tmp_db, "read", "999")
        assert code == 1
        assert "not found" in out


class TestDelete:
    def test_delete_soft(self, tmp_db, client):
        sid = client.ensure_sender("!aabbccdd")
        mid = client.insert_inbound(sid, "x")
        code, out = _run(tmp_db, "delete", str(mid))
        assert code == 0
        row = client.get_message(mid)
        assert row["state"] == "DELETED"
        assert row["deleted_at"] is not None


class TestHistory:
    def test_history_both_directions(self, tmp_db, client):
        sid = client.ensure_sender("!aabbccdd", alias="home")
        client.insert_inbound(sid, "in")
        client.enqueue_outbound(sid, "out")
        code, out = _run(tmp_db, "history")
        assert code == 0
        assert "<<<" in out
        assert ">>>" in out

    def test_history_filter_direction(self, tmp_db, client):
        sid = client.ensure_sender("!aabbccdd")
        client.insert_inbound(sid, "in")
        client.enqueue_outbound(sid, "out")
        code, out = _run(tmp_db, "history", "--direction", "in")
        assert code == 0
        assert "in" in out
        assert "out" not in out  # Only inbound shown

    def test_history_excludes_deleted(self, tmp_db, client):
        sid = client.ensure_sender("!aabbccdd")
        mid = client.insert_inbound(sid, "gone")
        client.mark_deleted(mid)
        code, out = _run(tmp_db, "history")
        assert code == 0
        assert "gone" not in out


class TestOutbox:
    def test_outbox_shows_retry_info(self, tmp_db, client):
        sid = client.ensure_sender("!aabbccdd", alias="home")
        client.enqueue_outbound(sid, "msg")
        code, out = _run(tmp_db, "outbox")
        assert code == 0
        assert "QUEUED" in out
        assert "retries=0" in out


class TestStatus:
    def test_status_shows_fields(self, tmp_db, client):
        sid = client.ensure_sender("!aabbccdd", alias="home")
        mid = client.enqueue_outbound(sid, "msg")
        code, out = _run(tmp_db, "status", str(mid))
        assert code == 0
        assert "QUEUED" in out
        assert "retries:" in out

    def test_status_missing(self, tmp_db):
        code, out = _run(tmp_db, "status", "999")
        assert code == 1
        assert "not found" in out


class TestAlias:
    def test_alias_creates_sender(self, tmp_db, client):
        code, out = _run(tmp_db, "alias", "!aabbccdd", "home")
        assert code == 0
        assert "created alias" in out
        result = client.resolve_alias("home")
        assert result is not None

    def test_alias_updates_existing(self, tmp_db, client):
        client.ensure_sender("!aabbccdd", alias="old")
        code, out = _run(tmp_db, "alias", "!aabbccdd", "new")
        assert code == 0
        assert "updated alias" in out
        assert client.resolve_alias("new") is not None

    def test_alias_auto_delete(self, tmp_db, client):
        code, out = _run(tmp_db, "alias", "!aabbccdd", "home", "--auto-delete", "3600")
        sid, _ = client.resolve_alias("home")
        row = client.get_sender_by_id(sid)
        assert row["auto_delete_after_s"] == 3600


class TestAliases:
    def test_empty(self, tmp_db):
        code, out = _run(tmp_db, "aliases")
        assert code == 0
        assert "(no senders)" in out

    def test_lists_senders(self, tmp_db, client):
        client.ensure_sender("!aabbccdd", alias="home")
        code, out = _run(tmp_db, "aliases")
        assert code == 0
        assert "home" in out


class TestDbStatus:
    def test_db_status_shows_size(self, tmp_db):
        code, out = _run(tmp_db, "db-status")
        assert code == 0
        assert "Size:" in out


class TestVacuum:
    def test_vacuum_runs(self, tmp_db):
        code, out = _run(tmp_db, "vacuum")
        assert code == 0
        assert "Vacuumed" in out


class TestDongleEject:
    def test_eject_enqueues_control(self, tmp_db, client):
        code, out = _run(tmp_db, "dongle-eject")
        assert code == 0
        assert "eject requested" in out
        conn = sqlite3.connect(str(tmp_db))
        rows = conn.execute("SELECT action FROM control_queue").fetchall()
        conn.close()
        assert any(r[0] == "eject" for r in rows)
