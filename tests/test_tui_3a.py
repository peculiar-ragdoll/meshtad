"""RED-phase tests for TUI 3a — Daemon heartbeat + skeleton.

These tests exercise:
1. Daemon writes heartbeat metadata to the meta table
2. Heartbeat freshness can be checked (online/offline)
3. TUI package skeleton loads and basic screens render
4. meshcli tui subcommand exists

They will fail until the implementation lands.
"""
from __future__ import annotations

import pathlib
import tempfile
import time

import pytest


@pytest.fixture
def tmp_db() -> pathlib.Path:
    with tempfile.TemporaryDirectory() as td:
        yield pathlib.Path(td) / "test.db"


class TestDaemonHeartbeat:
    def test_heartbeat_written_to_meta_table(self, tmp_db):
        """Daemon._sched_tick writes daemon_pid and daemon_heartbeat to meta."""
        from meshtad.config import Config
        from meshtad.daemon import Daemon
        from meshtad.db import DbThread

        t = DbThread(tmp_db)
        t.start()
        t.wait_ready(timeout=5.0)

        cfg = Config(db_path=tmp_db)
        d = Daemon(cfg)
        d.db = t

        # Simulate one scheduler tick
        d._sched_tick()

        rows = t.execute("SELECT key, value FROM meta WHERE key IN ('daemon_pid','daemon_heartbeat')")
        keys = {r["key"] for r in rows}
        assert "daemon_pid" in keys
        assert "daemon_heartbeat" in keys

        pid_row = [r for r in rows if r["key"] == "daemon_pid"][0]
        assert int(pid_row["value"]) > 0

        t.stop()

    def test_heartbeat_timestamp_is_iso(self, tmp_db):
        """The heartbeat value is a parseable ISO-8601 UTC timestamp."""
        from meshtad.config import Config
        from meshtad.daemon import Daemon
        from meshtad.db import DbThread

        t = DbThread(tmp_db)
        t.start()
        t.wait_ready(timeout=5.0)

        d = Daemon(Config(db_path=tmp_db))
        d.db = t
        d._sched_tick()

        rows = t.execute("SELECT value FROM meta WHERE key='daemon_heartbeat'")
        ts = rows[0]["value"]
        assert ts.endswith("Z")
        # Should parse as ISO
        from datetime import datetime
        parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        assert parsed.year >= 2026

        t.stop()

    def test_heartbeat_updates_on_subsequent_ticks(self, tmp_db):
        """Each tick updates the heartbeat timestamp."""
        from meshtad.config import Config
        from meshtad.daemon import Daemon
        from meshtad.db import DbThread

        t = DbThread(tmp_db)
        t.start()
        t.wait_ready(timeout=5.0)

        d = Daemon(Config(db_path=tmp_db))
        d.db = t

        d._sched_tick()
        rows = t.execute("SELECT value FROM meta WHERE key='daemon_heartbeat'")
        ts1 = rows[0]["value"]

        time.sleep(1.1)  # ensure whole-second boundary rolls over
        d._sched_tick()
        rows = t.execute("SELECT value FROM meta WHERE key='daemon_heartbeat'")
        ts2 = rows[0]["value"]

        assert ts2 > ts1

        t.stop()


class TestHeartbeatChecker:
    def test_fresh_heartbeat_reports_online(self, tmp_db):
        """When heartbeat is recent, is_daemon_online returns True."""
        from meshtad.db import DbThread
        from meshtad.tui.heartbeat import is_daemon_online

        t = DbThread(tmp_db)
        t.start()
        t.wait_ready(timeout=5.0)

        from meshtad.daemon import _iso_now
        t.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES ('daemon_heartbeat', ?)",
            (_iso_now(),),
        )

        assert is_daemon_online(tmp_db, threshold_s=30) is True
        t.stop()

    def test_stale_heartbeat_reports_offline(self, tmp_db):
        """When heartbeat is older than threshold, is_daemon_online returns False."""
        from meshtad.db import DbThread
        from meshtad.tui.heartbeat import is_daemon_online

        t = DbThread(tmp_db)
        t.start()
        t.wait_ready(timeout=5.0)

        t.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES ('daemon_heartbeat', '2000-01-01T00:00:00Z')",
        )

        assert is_daemon_online(tmp_db, threshold_s=30) is False
        t.stop()

    def test_missing_heartbeat_reports_offline(self, tmp_db):
        """When no heartbeat row exists, is_daemon_online returns False."""
        from meshtad.db import DbThread
        from meshtad.tui.heartbeat import is_daemon_online

        t = DbThread(tmp_db)
        t.start()
        t.wait_ready(timeout=5.0)

        assert is_daemon_online(tmp_db, threshold_s=30) is False
        t.stop()


class TestTuiSkeleton:
    def test_tui_package_imports(self):
        """The tui package can be imported without error."""
        from meshtad.tui import app  # noqa: F401

    def test_inbox_screen_class_exists(self):
        """InboxScreen class exists in the tui package."""
        from meshtad.tui.screens import InboxScreen
        assert hasattr(InboxScreen, "compose")

    @pytest.mark.asyncio
    async def test_app_mounts_without_crash(self, tmp_db):
        """The Textual App can be instantiated and mounted in a test."""
        from meshtad.tui.app import MeshtuiApp

        app = MeshtuiApp(db_path=tmp_db)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.is_mounted


class TestCliTuiCommand:
    def test_meshcli_tui_subcommand_exists(self, tmp_db):
        """meshcli tui is a recognized subcommand."""
        import io
        import sys
        from unittest.mock import patch

        from meshtad.cli import main

        buf = io.StringIO()
        with patch.object(sys, "argv", ["meshcli", "--db", str(tmp_db), "tui", "--help"]):
            with patch("sys.stdout", new=buf):
                try:
                    code = main()
                except SystemExit as exc:
                    code = exc.code if isinstance(exc.code, int) else 0

        assert code == 0
        out = buf.getvalue()
        assert "tui" in out.lower()
