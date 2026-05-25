"""Textual screens for meshtad TUI."""
from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import DataTable, Footer, Label, Static

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
