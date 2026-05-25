"""RED-phase tests for meshcli retry command.

These tests exercise `meshcli retry <id>` which moves a FAILED message
back to QUEUED with retry_count reset.  They will fail until the
implementation lands.
"""
from __future__ import annotations

import io
import pathlib
import sys
from contextlib import redirect_stdout
from unittest.mock import patch

import pytest

from meshtad.cli import main
from meshtad.db import DbClient, DbThread


@pytest.fixture
def tmp_db() -> pathlib.Path:
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        p = pathlib.Path(td) / "test.db"
        t = DbThread(p)
        t.start()
        t.wait_ready(timeout=5.0)
        t.stop()
        yield p


@pytest.fixture
def client(tmp_db) -> DbClient:
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


class TestRetry:
    def test_retry_moves_failed_to_queued(self, tmp_db, client):
        """retry <id> changes a FAILED message back to QUEUED."""
        sid = client.ensure_sender("!aabbccdd")
        mid = client.enqueue_outbound(sid, "hello")
        # Simulate FAILED state
        import sqlite3
        conn = sqlite3.connect(str(tmp_db))
        conn.execute("UPDATE messages SET state='FAILED', retry_count=5 WHERE id=?", (mid,))
        conn.commit()
        conn.close()

        code, out = _run(tmp_db, "retry", str(mid))
        assert code == 0
        assert "requeued" in out.lower() or "retry" in out.lower()
        row = client.get_message(mid)
        assert row["state"] == "QUEUED"

    def test_retry_resets_retry_count(self, tmp_db, client):
        """retry resets retry_count to 0 so the message gets fresh attempts."""
        sid = client.ensure_sender("!aabbccdd")
        mid = client.enqueue_outbound(sid, "hello")
        import sqlite3
        conn = sqlite3.connect(str(tmp_db))
        conn.execute("UPDATE messages SET state='FAILED', retry_count=5 WHERE id=?", (mid,))
        conn.commit()
        conn.close()

        _run(tmp_db, "retry", str(mid))
        row = client.get_message(mid)
        assert row["retry_count"] == 0

    def test_retry_clears_next_attempt_at(self, tmp_db, client):
        """retry clears the next_attempt_at back-off timer so drain picks it up immediately."""
        sid = client.ensure_sender("!aabbccdd")
        mid = client.enqueue_outbound(sid, "hello")
        import sqlite3
        conn = sqlite3.connect(str(tmp_db))
        conn.execute(
            "UPDATE messages SET state='FAILED', retry_count=3, next_attempt_at='2099-01-01T00:00:00Z' WHERE id=?",
            (mid,),
        )
        conn.commit()
        conn.close()

        _run(tmp_db, "retry", str(mid))
        row = client.get_message(mid)
        assert row["next_attempt_at"] is None

    def test_retry_rejects_non_failed(self, tmp_db, client):
        """retry on a QUEUED or SENT message is rejected."""
        sid = client.ensure_sender("!aabbccdd")
        mid = client.enqueue_outbound(sid, "hello")  # starts as QUEUED

        code, out = _run(tmp_db, "retry", str(mid))
        assert code == 1
        assert "not failed" in out.lower() or "cannot retry" in out.lower()

    def test_retry_missing_id(self, tmp_db):
        """retry on a non-existent id returns an error."""
        code, out = _run(tmp_db, "retry", "999")
        assert code == 1
        assert "not found" in out.lower()
