"""RED-phase tests for TUI 3b — InboxScreen messages, preview, hotkeys.

These will fail until InboxScreen is wired to read from the DB,
display messages in the DataTable, and respond to keys.
"""
from __future__ import annotations

import pathlib
import tempfile

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
    """Client with two inbound messages: one UNSEEN, one SEEN."""
    client = DbClient(tmp_db)
    sid = client.ensure_sender("!aabbccdd", alias="home")
    client.insert_inbound(sid, "alpha")          # UNSEEN
    i2 = client.insert_inbound(sid, "bravo")
    client.mark_read(i2)                        # SEEN
    return client


@pytest.mark.asyncio
async def test_inbox_shows_messages_on_mount(seeded_db, tmp_db):
    """On mount, InboxScreen populates the DataTable from the DB."""
    from meshtad.tui.app import MeshtuiApp

    app = MeshtuiApp(db_path=tmp_db)
    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.query_one("#message_list")
        assert table.row_count == 2


@pytest.mark.asyncio
async def test_inbox_shows_unseen_flag(seeded_db, tmp_db):
    """UNSEEN messages show [*]; SEEN shows [ ]."""
    from meshtad.tui.app import MeshtuiApp

    app = MeshtuiApp(db_path=tmp_db)
    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.query_one("#message_list")
        # First row (newest by default) should have [*]
        row0 = [str(c) for c in table.get_row_at(0)]
        assert "*" in " ".join(row0)


@pytest.mark.asyncio
async def test_preview_updates_on_selection(seeded_db, tmp_db):
    """When the user moves the cursor, the preview pane updates."""
    from meshtad.tui.app import MeshtuiApp

    app = MeshtuiApp(db_path=tmp_db)
    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.query_one("#message_list")
        preview = app.query_one("#preview")
        # Move cursor to second row and trigger selection change
        table.cursor_coordinate = (1, 0)
        await pilot.pause()
        rendered = str(preview.render())
        assert "bravo" in rendered


@pytest.mark.asyncio
async def test_m_key_marks_read(seeded_db, tmp_db):
    """Pressing 'm' marks the selected UNSEEN message as SEEN in the DB."""
    from meshtad.tui.app import MeshtuiApp

    app = MeshtuiApp(db_path=tmp_db)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("m")
        await pilot.pause()

    # After app exits, verify DB state
    client = DbClient(tmp_db)
    unseen = client.inbox(unseen_only=True)
    assert len(unseen) == 0  # both messages should be SEEN now


@pytest.mark.asyncio
async def test_delete_key_prompts_and_deletes(seeded_db, tmp_db):
    """Pressing 'd' prompts Y/N; confirming deletes the message."""
    from meshtad.tui.app import MeshtuiApp

    app = MeshtuiApp(db_path=tmp_db)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("d")
        await pilot.pause()
        # Confirm the delete prompt appeared and hit 'y'
        await pilot.press("y")
        await pilot.pause()

    client = DbClient(tmp_db)
    rows = client.inbox()
    assert len(rows) == 1  # one message deleted
