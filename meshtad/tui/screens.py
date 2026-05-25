"""Textual screens for meshtad TUI."""
from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import DataTable, Footer, Input, Label, Static, TextArea

from meshtad.db import DbClient


class ConfirmDeleteModal(ModalScreen[bool]):
    """Y/N confirmation modal."""

    BINDINGS = [
        ("y", "confirm", "Yes"),
        ("n", "dismiss", "No"),
    ]

    def compose(self) -> ComposeResult:
        yield Static("Delete this message? [y/n]", id="question")

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_dismiss(self) -> None:
        self.dismiss(False)


class ConfirmDiscardModal(ModalScreen[bool]):
    """Y/N confirmation for discarding unsent draft."""

    BINDINGS = [
        ("y", "confirm", "Yes"),
        ("n", "dismiss", "No"),
    ]

    def compose(self) -> ComposeResult:
        yield Static("Discard unsent message? [y/n]", id="question")

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_dismiss(self) -> None:
        self.dismiss(False)


class InboxScreen(Screen):
    """Split-pane inbox viewer."""

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("r", "reply", "Reply"),
        ("n", "new", "New"),
        ("m", "mark_read", "Mark read"),
        ("d", "delete", "Delete"),
        ("question_mark", "help", "Help"),
    ]

    def __init__(self, db_path, **kwargs) -> None:
        super().__init__(**kwargs)
        self.db_path = db_path

    def compose(self) -> ComposeResult:
        yield DataTable(id="message_list")
        yield Vertical(
            Label("Select a message", id="preview"),
            id="detail_pane",
        )
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#message_list", DataTable)
        table.add_columns("Flag", "ID", "From", "Preview", "State", "Time")
        table.cursor_type = "row"
        self._refresh_table()

    def _refresh_table(self) -> None:
        """Reload inbox rows from DB."""
        table = self.query_one("#message_list", DataTable)
        table.clear()
        client = DbClient(self.db_path)
        try:
            rows = client.inbox(limit=100)
        except Exception:
            table.add_row("", "", "", "(no schema — is the daemon running?)", "", "")
            return
        for msg_id, alias, node_id, body, state, ts in rows:
            flag = "*" if state == "UNSEEN" else " "
            name = alias or node_id
            preview = body[:40] + "…" if len(body) > 40 else body
            table.add_row(flag, str(msg_id), name, preview, state, ts)
        if rows:
            table.move_cursor(row=0)
            self._update_preview(rows[0])

    def _update_preview(self, row) -> None:
        """Update the bottom preview pane from a DB row."""
        _, alias, node_id, body, state, ts = row
        name = alias or node_id
        text = f"From: {name}\nState: {state}\nTime: {ts}\n\n{body}"
        preview = self.query_one("#preview", Label)
        preview.update(text)

    def on_data_table_row_selected(self, event) -> None:
        """Handle cursor movement in the DataTable."""
        table = self.query_one("#message_list", DataTable)
        row_idx = event.cursor_row
        if row_idx is not None and 0 <= row_idx < table.row_count:
            client = DbClient(self.db_path)
            try:
                rows = client.inbox(limit=100)
            except Exception:
                return
            if row_idx < len(rows):
                self._update_preview(rows[row_idx])

    def on_data_table_row_highlighted(self, event) -> None:
        """Handle cursor highlight changes."""
        self.on_data_table_row_selected(event)

    def action_mark_read(self) -> None:
        """Mark the selected UNSEEN message as SEEN."""
        table = self.query_one("#message_list", DataTable)
        row_idx = table.cursor_row
        if row_idx is None:
            return
        client = DbClient(self.db_path)
        try:
            rows = client.inbox(limit=100)
        except Exception:
            return
        if row_idx >= len(rows):
            return
        msg_id = rows[row_idx][0]
        client.mark_read(msg_id)
        self._refresh_table()

    def action_delete(self) -> None:
        """Soft-delete the selected message after confirmation."""
        table = self.query_one("#message_list", DataTable)
        row_idx = table.cursor_row
        if row_idx is None:
            return
        client = DbClient(self.db_path)
        try:
            rows = client.inbox(limit=100)
        except Exception:
            return
        if row_idx >= len(rows):
            return
        msg_id = rows[row_idx][0]

        def on_confirm(confirmed: bool | None) -> None:
            if confirmed:
                client.mark_deleted(msg_id)
                self._refresh_table()

        self.app.push_screen(ConfirmDeleteModal(), on_confirm)

    def action_new(self) -> None:
        """Open compose screen for a new message."""
        self.app.push_screen(ComposeScreen(db_path=self.db_path))

    def action_reply(self) -> None:
        """Open compose screen pre-filled with selected sender."""
        table = self.query_one("#message_list", DataTable)
        row_idx = table.cursor_row
        if row_idx is None:
            return
        client = DbClient(self.db_path)
        try:
            rows = client.inbox(limit=100)
        except Exception:
            return
        if row_idx >= len(rows):
            return
        msg_id = rows[row_idx][0]
        msg = client.get_message(msg_id)
        if msg is None:
            return
        sender = client.get_sender_by_id(msg["peer_id"])
        alias = sender["alias"] if sender else None
        node_id = sender["node_id"] if sender else ""
        self.app.push_screen(
            ComposeScreen(db_path=self.db_path, to_alias=alias, to_node_id=node_id)
        )


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

        # Enforce Meshtastic DM byte limit (~233 protobuf max; leave 13 bytes headroom)
        body_bytes = body.encode("utf-8")
        if len(body_bytes) > 220:
            status.update(f"! Message too long: {len(body_bytes)} bytes, limit 220")
            return

        client = DbClient(self.db_path)
        resolved = client.resolve_alias(alias_or_id)
        if resolved is None:
            # Auto-create sender if it looks like a node id
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
