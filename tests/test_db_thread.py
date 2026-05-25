"""Regression tests for DbThread robustness."""
from __future__ import annotations

import pathlib
import tempfile
import threading
import time

import pytest

from meshtad.db import DbThread, DbClient


@pytest.fixture
def tmp_db() -> pathlib.Path:
    with tempfile.TemporaryDirectory() as td:
        yield pathlib.Path(td) / "test.db"


class TestDbThreadTimeout:
    def test_execute_raises_on_dead_thread(self, tmp_db):
        """When the DbThread is stopped, execute() must raise RuntimeError
        instead of blocking forever."""
        t = DbThread(tmp_db)
        t.start()
        assert t.wait_ready(timeout=5.0)
        t.stop()
        assert not t.is_alive()
        with pytest.raises(RuntimeError, match="did not respond"):
            t.execute("SELECT 1")

    def test_stop_is_idempotent(self, tmp_db):
        """Calling stop() twice must not crash."""
        t = DbThread(tmp_db)
        t.start()
        t.wait_ready(timeout=5.0)
        t.stop()
        t.stop()  # should not raise
        assert not t.is_alive()

    def test_writer_survives_concurrent_pressure(self, tmp_db):
        """Multiple threads writing simultaneously should not deadlock or corrupt."""
        t = DbThread(tmp_db)
        t.start()
        t.wait_ready(timeout=5.0)

        errors: list = []
        def writer(label: str):
            try:
                for i in range(50):
                    t.execute(f"INSERT INTO senders (node_id) VALUES ('!{label}{i:04x}')")
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=writer, args=(f"t{j}",)) for j in range(4)]
        for th in threads:
            th.start()
        for th in threads:
            th.join(timeout=30.0)

        t.stop()
        assert not errors, f"Writer threads raised: {errors}"
        rows = DbClient(tmp_db).list_senders()
        assert len(rows) == 4 * 50


class TestDbClientWhitelist:
    def test_update_sender_rejects_disallowed_field(self, tmp_db):
        """update_sender must reject unknown field names to prevent SQL injection."""
        t = DbThread(tmp_db)
        t.start()
        t.wait_ready(timeout=5.0)
        t.stop()
        client = DbClient(tmp_db)
        sid = client.ensure_sender("!aabbccdd")
        with pytest.raises(ValueError, match="Disallowed"):
            client.update_sender(sid, node_id="evil")
