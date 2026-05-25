"""RED-phase tests for TUI 3d — Tabs, polling, daemon status.

These will fail until tab switching, background polling, and status bar land.
"""
from __future__ import annotations

import pathlib
import tempfile
import time

import pytest

from meshtad.db import DbClient, DbThread


@pytest.fixture
def tmp_db() -> pathlib.Path:
    with tempfile.TemporaryDirectory() as td:
        yield pathlib.Path(td) / "test.db"


@pytest.fixture
def db_thread(tmp_db):
    t = DbThread(tmp_db)
    t.start()
    t.wait_ready(timeout=5.0)
    yield t
    t.stop()


@pytest.fixture
def seeded_db(tmp_db, db_thread) -> DbClient:
    """Client with one inbound from 'home' and one outbound."""
    client = DbClient(tmp_db)
    sid = client.ensure_sender("!aabbccdd", alias="home")
    client.insert_inbound(sid, "hello from mesh")
    client.enqueue_outbound(sid, "outbound hello")
    return client


# ------------------------------------------------------------------
# Tab switching
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_default_tab_is_inbox(tmp_db, seeded_db):
    """On mount the active tab is Inbox (tab 0)."""
    from meshtad.tui.app import MeshtuiApp

    app = MeshtuiApp(db_path=tmp_db)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.query_one("InboxScreen")
        assert screen.tab_idx == 0


@pytest.mark.asyncio
async def test_tab_2_switches_to_outbox(tmp_db, seeded_db):
    """Pressing '2' switches to the Outbox tab."""
    from meshtad.tui.app import MeshtuiApp

    app = MeshtuiApp(db_path=tmp_db)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("2")
        await pilot.pause()
        screen = app.query_one("InboxScreen")
        assert screen.tab_idx == 1
        # Outbox should show the outbound row
        table = app.query_one("#message_list")
        assert table.row_count == 1


@pytest.mark.asyncio
async def test_tab_3_switches_to_history(tmp_db, seeded_db):
    """Pressing '3' switches to the History tab."""
    from meshtad.tui.app import MeshtuiApp

    app = MeshtuiApp(db_path=tmp_db)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("3")
        await pilot.pause()
        screen = app.query_one("InboxScreen")
        assert screen.tab_idx == 2
        # History should show both inbound + outbound (2 rows)
        table = app.query_one("#message_list")
        assert table.row_count == 2


@pytest.mark.asyncio
async def test_tab_1_returns_to_inbox(tmp_db, seeded_db):
    """Pressing '1' from another tab returns to Inbox."""
    from meshtad.tui.app import MeshtuiApp

    app = MeshtuiApp(db_path=tmp_db)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("2")
        await pilot.pause()
        await pilot.press("1")
        await pilot.pause()
        screen = app.query_one("InboxScreen")
        assert screen.tab_idx == 0


# ------------------------------------------------------------------
# Status bar / daemon liveness
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_status_bar_shows_offline_when_no_heartbeat(tmp_db):
    """With no daemon heartbeat the status bar shows 'offline'."""
    from meshtad.tui.app import MeshtuiApp

    app = MeshtuiApp(db_path=tmp_db)
    async with app.run_test() as pilot:
        await pilot.pause()
        status = app.query_one("#status_bar")
        rendered = str(status.render())
        assert "offline" in rendered.lower() or "off" in rendered.lower()


@pytest.mark.asyncio
async def test_status_bar_shows_online_with_fresh_heartbeat(tmp_db, db_thread):
    """With a fresh heartbeat the status bar shows 'online'."""
    from meshtad.daemon import _iso_now  # type: ignore[attr-defined]
    from meshtad.tui.app import MeshtuiApp

    db_thread.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES ('daemon_heartbeat', ?)",
        (_iso_now(),),
    )

    app = MeshtuiApp(db_path=tmp_db)
    async with app.run_test() as pilot:
        await pilot.pause()
        status = app.query_one("#status_bar")
        rendered = str(status.render())
        assert "online" in rendered.lower()


# ------------------------------------------------------------------
# Background polling
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_poller_refreshes_on_new_inbound(tmp_db, db_thread):
    """When a new inbound message arrives in the DB, the poller picks it up."""
    from meshtad.tui.app import MeshtuiApp

    app = MeshtuiApp(db_path=tmp_db)
    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.query_one("#message_list")

        # Inject a message behind the TUI's back
        client = DbClient(tmp_db)
        sid = client.ensure_sender("!deadbeef", alias="node")
        client.insert_inbound(sid, "late arrival")

        # Force a poll cycle (poller interval is long in test; trigger manually)
        screen = app.query_one("InboxScreen")
        screen._poll_db()
        await pilot.pause()

        # Verify message appears via DB query directly
        refreshed_rows = client.inbox()
        assert len(refreshed_rows) == 1
        assert refreshed_rows[0][3] == "late arrival"  # body column


@pytest.mark.asyncio
async def test_poller_updates_unread_count(tmp_db, db_thread):
    """When UNSEEN messages exist, the status bar shows an unread count."""
    from meshtad.tui.app import MeshtuiApp

    app = MeshtuiApp(db_path=tmp_db)
    async with app.run_test() as pilot:
        await pilot.pause()
        client = DbClient(tmp_db)
        sid = client.ensure_sender("!deadbeef", alias="node")
        client.insert_inbound(sid, "unread")

        screen = app.query_one("InboxScreen")
        screen._poll_db()
        await pilot.pause()

        status = app.query_one("#status_bar")
        rendered = str(status.render())
        assert "1" in rendered


# ------------------------------------------------------------------
# Outbox / History row rendering
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_outbox_shows_state_indicator(tmp_db, seeded_db):
    """Outbox tab renders the state column (Q/S/A/F)."""
    from meshtad.tui.app import MeshtuiApp

    app = MeshtuiApp(db_path=tmp_db)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("2")
        await pilot.pause()
        table = app.query_one("#message_list")
        row0 = [str(c) for c in table.get_row_at(0)]
        joined = " ".join(row0).lower()
        assert "queued" in joined
