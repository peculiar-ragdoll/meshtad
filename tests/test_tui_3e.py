"""Tests for TUI 3e — Help overlay, theme, config integration."""
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
    client = DbClient(tmp_db)
    sid = client.ensure_sender("!aabbccdd", alias="home")
    client.insert_inbound(sid, "hello from mesh")
    return client


# ------------------------------------------------------------------
# Help overlay
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_question_mark_opens_help(tmp_db, seeded_db):
    """Pressing '?' opens the HelpModal."""
    from meshtad.tui.app import MeshtuiApp

    app = MeshtuiApp(db_path=tmp_db)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("question_mark")
        await pilot.pause()
        from meshtad.tui.screens import HelpModal
        assert isinstance(app.screen, HelpModal)


@pytest.mark.asyncio
async def test_help_shows_bindings(tmp_db, seeded_db):
    """HelpModal renders the current screen's key bindings."""
    from meshtad.tui.app import MeshtuiApp

    app = MeshtuiApp(db_path=tmp_db)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("question_mark")
        await pilot.pause()
        help_text = app.screen.query_one("#help_text")
        rendered = str(help_text.render()).lower()
        assert "n" in rendered and "new" in rendered


@pytest.mark.asyncio
async def test_q_dismisses_help(tmp_db, seeded_db):
    """Pressing 'q' in HelpModal dismisses it back to InboxScreen."""
    from meshtad.tui.app import MeshtuiApp
    from meshtad.tui.screens import HelpModal

    app = MeshtuiApp(db_path=tmp_db)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.push_screen(HelpModal(bindings=[]))
        await pilot.pause()
        await pilot.press("q")
        await pilot.pause()
        assert not isinstance(app.screen, HelpModal)


@pytest.mark.asyncio
async def test_escape_dismisses_help(tmp_db, seeded_db):
    """Pressing 'escape' in HelpModal dismisses it."""
    from meshtad.tui.app import MeshtuiApp
    from meshtad.tui.screens import HelpModal

    app = MeshtuiApp(db_path=tmp_db)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.push_screen(HelpModal(bindings=[]))
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, HelpModal)


# ------------------------------------------------------------------
# Theme / $NO_COLOR
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_no_color_disables_rich_colors(tmp_db, seeded_db, monkeypatch):
    """Textual natively respects $NO_COLOR via pseudo-classes."""
    monkeypatch.setenv("NO_COLOR", "1")
    from meshtad.tui.app import MeshtuiApp

    app = MeshtuiApp(db_path=tmp_db)
    assert "nocolor" in str(app.pseudo_classes)
