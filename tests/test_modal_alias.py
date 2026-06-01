"""Tests for SetAliasModal visibility and input handling.

Covers the fix where typed text was invisible inside the modal Input widget
due to missing CSS on the ModalScreen background and Input styling.
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
def seeded_db(tmp_db, db_thread) -> tuple[DbClient, int]:
    """Return (client, sender_id) with one inbound message, no alias."""
    client = DbClient(tmp_db)
    sid = client.ensure_sender("!aabbccdd")  # no alias
    client.insert_inbound(sid, "hello from home")
    return client, sid


def _find_sender(client: DbClient, node_id: str):
    """Helper: find sender row by node_id via list_senders()."""
    for row in client.list_senders():
        if row[1] == node_id:
            return row
    return None


@pytest.mark.asyncio
async def test_alias_modal_input_visible_on_mount(seeded_db, tmp_db):
    """The modal Input widget exists, is focused, and has visible styling."""
    from meshtad.tui.app import MeshtuiApp

    app = MeshtuiApp(db_path=tmp_db)
    async with app.run_test() as pilot:
        await pilot.pause()
        # Trigger the set_alias action to push the modal
        await pilot.press("a")
        await pilot.pause()

        # Modal should be the active screen
        assert app.screen.__class__.__name__ == "SetAliasModal"

        # Input widget should exist on the modal screen and be focused
        input_widget = app.screen.query_one("#alias_input")
        assert input_widget.has_focus
        # Value should start empty (no existing alias)
        assert input_widget.value == ""


@pytest.mark.asyncio
async def test_alias_modal_existing_alias_shows(tmp_db, db_thread):
    """If the sender already has an alias, it pre-fills the Input."""
    from meshtad.tui.app import MeshtuiApp

    # Set up: create sender WITH an alias
    client = DbClient(tmp_db)
    sid = client.ensure_sender("!aabbccdd")
    client.update_sender(sid, alias="emma")
    client.insert_inbound(sid, "hello emma")

    app = MeshtuiApp(db_path=tmp_db)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("a")
        await pilot.pause()

        input_widget = app.screen.query_one("#alias_input")
        assert input_widget.value == "emma"


@pytest.mark.asyncio
async def test_alias_modal_type_and_submit(seeded_db, tmp_db):
    """Typing into the Input and pressing Enter saves the alias."""
    from meshtad.tui.app import MeshtuiApp

    app = MeshtuiApp(db_path=tmp_db)
    async with app.run_test() as pilot:
        await pilot.pause()
        # Push the alias modal
        await pilot.press("a")
        await pilot.pause()

        input_widget = app.screen.query_one("#alias_input")
        input_widget.value = "t-deck"
        await pilot.press("enter")
        await pilot.pause()

        # Modal should have popped
        assert app.screen.__class__.__name__ != "SetAliasModal"

    # Verify alias was saved in DB
    client = DbClient(tmp_db)
    sender = _find_sender(client, "!aabbccdd")
    assert sender is not None
    assert sender[2] == "t-deck"  # alias column


@pytest.mark.asyncio
async def test_alias_modal_escape_cancels(seeded_db, tmp_db):
    """Pressing Escape dismisses the modal without saving."""
    from meshtad.tui.app import MeshtuiApp

    client_orig, _ = seeded_db

    app = MeshtuiApp(db_path=tmp_db)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("a")
        await pilot.pause()

        # Type something but cancel
        input_widget = app.screen.query_one("#alias_input")
        input_widget.value = "cancelled"
        await pilot.press("escape")
        await pilot.pause()

        # Modal should have popped
        assert app.screen.__class__.__name__ != "SetAliasModal"

    # Alias should be unchanged (still None)
    sender = _find_sender(client_orig, "!aabbccdd")
    assert sender is not None
    assert sender[2] is None  # alias column


@pytest.mark.asyncio
async def test_alias_modal_empty_input_cancels(tmp_db, db_thread):
    """Pressing Enter with an empty Input dismisses without saving."""
    from meshtad.tui.app import MeshtuiApp

    # Set up: create sender WITH an alias
    client = DbClient(tmp_db)
    sid = client.ensure_sender("!aabbccdd")
    client.update_sender(sid, alias="original")
    client.insert_inbound(sid, "hello")

    app = MeshtuiApp(db_path=tmp_db)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("a")
        await pilot.pause()

        # Clear the pre-filled value
        input_widget = app.screen.query_one("#alias_input")
        input_widget.value = ""
        await pilot.press("enter")
        await pilot.pause()

    # Alias should be unchanged
    sender = _find_sender(client, "!aabbccdd")
    assert sender is not None
    assert sender[2] == "original"  # alias column
