"""Compose screen for creating outbound DMs."""
from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.screen import Screen
from textual.widgets import Footer, Input, Static, TextArea

from meshtad.db import DbClient
from meshtad.tui.screens.modals import ConfirmDiscardModal


class ComposeScreen(Screen):
    """Screen for composing and queueing an outbound DM."""

    BINDINGS = [
        Binding("ctrl+s", "send", "Send", priority=True),
        Binding("ctrl+c", "cancel", "Cancel", priority=True),
        Binding("q", "try_quit", "Quit", priority=True),
    ]

    def __init__(self, db_path, to_alias: str | None = None, to_node_id: str | None = None, **kwargs):
        super().__init__(**kwargs)
        self.db_path = db_path
        self.to_alias = to_alias
        self.to_node_id = to_node_id

    def compose(self) -> ComposeResult:
        to_value = self.to_alias or ""
        yield Static("Compose Message", id="compose_title")
        yield Horizontal(
            Static("To:", id="compose_to_label"),
            Input(value=to_value, placeholder="Alias or node id", id="compose_to"),
            id="compose_to_row",
        )
        yield TextArea(id="compose_body")
        yield Static("", id="compose_status")
        yield Footer()

    def _has_unsent_text(self) -> bool:
        body = self.query_one("#compose_body", TextArea)
        to_inp = self.query_one("#compose_to", Input)
        return bool(body.text.strip() or to_inp.value.strip())

    def action_try_quit(self) -> None:
        if self._has_unsent_text():
            def on_confirm(confirmed: bool | None) -> None:
                if confirmed:
                    self.app.pop_screen()
            self.app.push_screen(ConfirmDiscardModal(), on_confirm)
        else:
            self.app.pop_screen()

    def action_cancel(self) -> None:
        self.app.pop_screen()

    def action_send(self) -> None:
        to_inp = self.query_one("#compose_to", Input)
        body_widget = self.query_one("#compose_body", TextArea)
        status = self.query_one("#compose_status", Static)

        alias_or_id = to_inp.value.strip()
        body = body_widget.text.strip()

        if not alias_or_id:
            status.update("! To field is required")
            return
        if not body:
            status.update("! Body is empty")
            return

        body_bytes = body.encode("utf-8")
        if len(body_bytes) > 220:
            status.update(f"! Message too long: {len(body_bytes)} bytes, limit 220")
            return

        client = DbClient(self.db_path)
        resolved = client.resolve_alias(alias_or_id)
        if resolved is None:
            if alias_or_id.startswith("!"):
                sender_id = client.ensure_sender(alias_or_id)
            else:
                status.update(f"! Unknown alias: '{alias_or_id}'")
                return
        else:
            sender_id, _ = resolved

        msg_id = client.enqueue_outbound(sender_id, body)
        status.update(f"Queued message #{msg_id}")
        self.app.pop_screen()
