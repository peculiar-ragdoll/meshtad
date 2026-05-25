"""RED-phase tests for TUI 3c — ComposeScreen: new, reply, send, cancel.

These will fail until ComposeScreen is implemented and wired to InboxScreen.
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
    """Client with one inbound message from 'home'."""
    client = DbClient(tmp_db)
    sid = client.ensure_sender("!aabbccdd", alias="home")
    client.insert_inbound(sid, "hello from mesh")
    return client


# ------------------------------------------------------------------
# ComposeScreen standalone
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_compose_screen_has_input_and_textarea(tmp_db):
    """ComposeScreen contains a To Input and a Body TextArea."""
    from meshtad.tui.app import MeshtuiApp
    from meshtad.tui.screens import ComposeScreen

    app = MeshtuiApp(db_path=tmp_db)
    async with app.run_test() as pilot:
        app.push_screen(ComposeScreen(db_path=tmp_db))
        await pilot.pause()
        assert app.screen.query_one("#compose_to") is not None
        assert app.screen.query_one("#compose_body") is not None


@pytest.mark.asyncio
async def test_compose_reply_prefills_to(tmp_db, seeded_db):
    """ComposeScreen opened in reply mode pre-fills the To field."""
    from meshtad.tui.app import MeshtuiApp
    from meshtad.tui.screens import ComposeScreen

    app = MeshtuiApp(db_path=tmp_db)
    async with app.run_test() as pilot:
        screen = ComposeScreen(db_path=tmp_db, to_alias="home", to_node_id="!aabbccdd")
        app.push_screen(screen)
        await pilot.pause()
        inp = app.screen.query_one("#compose_to")
        assert "home" in inp.value


@pytest.mark.asyncio
async def test_compose_new_has_empty_to(tmp_db):
    """ComposeScreen opened in new mode has an empty To field."""
    from meshtad.tui.app import MeshtuiApp
    from meshtad.tui.screens import ComposeScreen

    app = MeshtuiApp(db_path=tmp_db)
    async with app.run_test() as pilot:
        app.push_screen(ComposeScreen(db_path=tmp_db))
        await pilot.pause()
        inp = app.screen.query_one("#compose_to")
        assert inp.value == ""


@pytest.mark.asyncio
async def test_ctrl_s_saves_queued_outbound(tmp_db, seeded_db):
    """Ctrl+S in ComposeScreen writes a QUEUED outbound message to the DB."""
    from textual.widgets import TextArea

    from meshtad.tui.app import MeshtuiApp
    from meshtad.tui.screens import ComposeScreen

    app = MeshtuiApp(db_path=tmp_db)
    async with app.run_test() as pilot:
        app.push_screen(ComposeScreen(db_path=tmp_db, to_alias="home", to_node_id="!aabbccdd"))
        await pilot.pause()
        # Set body text directly
        body = app.screen.query_one("#compose_body", TextArea)
        body.text = "reply body"
        await pilot.press("ctrl+s")
        await pilot.pause()

    # After dismissal verify DB
    client = DbClient(tmp_db)
    outbox = client.outbox()
    assert len(outbox) == 1
    _, alias, node_id, body, state, *_ = outbox[0]
    assert state == "QUEUED"
    assert body == "reply body"
    assert alias == "home"


@pytest.mark.asyncio
async def test_ctrl_c_cancels_without_writing(tmp_db, seeded_db):
    """Ctrl+C in ComposeScreen dismisses without touching the DB."""
    from meshtad.tui.app import MeshtuiApp
    from meshtad.tui.screens import ComposeScreen

    app = MeshtuiApp(db_path=tmp_db)
    async with app.run_test() as pilot:
        app.push_screen(ComposeScreen(db_path=tmp_db, to_alias="home", to_node_id="!aabbccdd"))
        await pilot.pause()
        await pilot.press("ctrl+c")
        await pilot.pause()

    client = DbClient(tmp_db)
    assert len(client.outbox()) == 0


@pytest.mark.asyncio
async def test_q_with_empty_body_quits_immediately(tmp_db):
    """q with no typed text dismisses the ComposeScreen immediately."""
    from meshtad.tui.app import MeshtuiApp
    from meshtad.tui.screens import ComposeScreen

    app = MeshtuiApp(db_path=tmp_db)
    async with app.run_test() as pilot:
        app.push_screen(ComposeScreen(db_path=tmp_db))
        await pilot.pause(0.1)
        # Trigger the quit action directly via the screen’s binding, bypassing widget focus issues
        app.screen.action_try_quit()
        for _ in range(3):
            await pilot.pause()
        # Should no longer be on ComposeScreen
        assert not isinstance(app.screen, ComposeScreen)


@pytest.mark.asyncio
async def test_q_with_text_prompts_discard_then_y_quits(tmp_db):
    """q with typed text shows a confirmation; 'y' discards and quits."""
    from textual.widgets import TextArea

    from meshtad.tui.app import MeshtuiApp
    from meshtad.tui.screens import ComposeScreen

    app = MeshtuiApp(db_path=tmp_db)
    async with app.run_test() as pilot:
        app.push_screen(ComposeScreen(db_path=tmp_db))
        await pilot.pause()
        body = app.screen.query_one("#compose_body", TextArea)
        body.text = "some draft"
        # fire the binding directly;  widget focus swallows raw key in test
        app.screen.action_try_quit()
        await pilot.pause()
        # Confirmation modal should be on top
        await pilot.press("y")
        await pilot.pause()
        assert not isinstance(app.screen, ComposeScreen)


@pytest.mark.asyncio
async def test_q_with_text_prompts_discard_then_n_returns(tmp_db):
    """q with typed text shows a confirmation; 'n' returns to compose."""
    from textual.widgets import TextArea

    from meshtad.tui.app import MeshtuiApp
    from meshtad.tui.screens import ComposeScreen

    app = MeshtuiApp(db_path=tmp_db)
    async with app.run_test() as pilot:
        app.push_screen(ComposeScreen(db_path=tmp_db))
        await pilot.pause()
        body = app.screen.query_one("#compose_body", TextArea)
        body.text = "some draft"
        app.screen.action_try_quit()
        await pilot.pause()
        await pilot.press("n")
        await pilot.pause()
        assert isinstance(app.screen, ComposeScreen)


# ------------------------------------------------------------------
# InboxScreen → ComposeScreen integration
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_n_key_opens_compose_from_inbox(tmp_db, seeded_db):
    """Pressing 'n' on InboxScreen pushes a ComposeScreen with empty To."""
    from meshtad.tui.app import MeshtuiApp
    from meshtad.tui.screens import ComposeScreen

    app = MeshtuiApp(db_path=tmp_db)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("n")
        await pilot.pause()
        assert isinstance(app.screen, ComposeScreen)


@pytest.mark.asyncio
async def test_r_key_opens_reply_from_inbox(tmp_db, seeded_db):
    """Pressing 'r' on InboxScreen pushes a ComposeScreen pre-filled with sender alias."""
    from meshtad.tui.app import MeshtuiApp
    from meshtad.tui.screens import ComposeScreen

    app = MeshtuiApp(db_path=tmp_db)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("r")
        await pilot.pause()
        assert isinstance(app.screen, ComposeScreen)
        inp = app.screen.query_one("#compose_to")
        assert "home" in inp.value
