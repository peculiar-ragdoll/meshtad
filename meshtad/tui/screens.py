"""Textual screens for meshtad TUI."""
from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import DataTable, Footer, Input, Label, Static, TextArea

from meshtad.db import DbClient
from meshtad.tui.heartbeat import is_daemon_online


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
    """Tabbed message viewer with inbox / outbox / history."""

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("r", "reply", "Reply"),
        ("n", "new", "New"),
        ("m", "mark_read", "Mark read"),
        ("d", "delete", "Delete"),
        ("question_mark", "help", "Help"),
        ("1", "tab_inbox", "Inbox"),
        ("2", "tab_outbox", "Outbox"),
        ("3", "tab_history", "History"),
    ]

    TAB_NAMES = ["Inbox", "Outbox", "History"]

    def __init__(self, db_path, **kwargs) -> None:
        super().__init__(**kwargs)
        self.db_path = db_path
        self.tab_idx = 0
        self._max_id_seen = 0
        self._poll_timer = None

    # ------------------------------------------------------------------
    # Compose / lifecycle
    # ------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Static("[init]", id="status_bar", markup=False)
        yield DataTable(id="message_list")
        yield Vertical(
            Label("Select a message", id="preview"),
            id="detail_pane",
        )
        yield Footer()

    def on_mount(self) -> None:
        self._setup_table()
        self._refresh_table()
        self._update_status_bar()
        # Poller every 2s (default)
        self._poll_timer = self.app.set_interval(2.0, self._poll_db)

    def _setup_table(self) -> None:
        table = self.query_one("#message_list", DataTable)
        table.cursor_type = "row"
        self._rebuild_columns()

    def _rebuild_columns(self) -> None:
        table = self.query_one("#message_list", DataTable)
        table.clear(columns=True)
        cols = self._columns_for_tab()
        table.add_columns(*cols)

    # ------------------------------------------------------------------
    # Column specs per tab
    # ------------------------------------------------------------------

    def _columns_for_tab(self) -> tuple[str, ...]:
        return {
            0: ("Flag", "ID", "From", "Preview", "State", "Time"),
            1: ("ID", "To", "Preview", "State", "Retries", "Next Attempt"),
            2: ("Dir", "ID", "From/To", "Preview", "State", "Time"),
        }[self.tab_idx]

    # ------------------------------------------------------------------
    # Data fetching
    # ------------------------------------------------------------------

    def _fetch_rows(self):
        client = DbClient(self.db_path)
        try:
            if self.tab_idx == 0:
                return client.inbox(limit=100)
            if self.tab_idx == 1:
                return client.outbox()
            return client.history(limit=200)
        except Exception:
            return []

    def _row_data(self, raw_row) -> tuple:
        """Convert a DB row tuple into DataTable cell values."""
        if self.tab_idx == 0:
            # inbox: (id, alias, node_id, body, state, ts)
            msg_id, alias, node_id, body, state, ts = raw_row
            flag = "*" if state == "UNSEEN" else " "
            name = alias or node_id
            preview = body[:40] + "…" if len(body) > 40 else body
            return (flag, str(msg_id), name, preview, state, ts)

        if self.tab_idx == 1:
            # outbox: (id, alias, node_id, body, state, retry_count, next_attempt, error)
            msg_id, alias, node_id, body, state, retries, next_at, error = raw_row
            name = alias or node_id
            preview = body[:40] + "…" if len(body) > 40 else body
            next_str = next_at or "-"
            return (str(msg_id), name, preview, state, str(retries), next_str)

        # history: (id, direction, alias, node_id, body, state, queued_at, sent_at, acked_at, error)
        msg_id, direction, alias, node_id, body, state, queued_at, sent_at, acked_at, error = raw_row
        flag = "→" if direction == "out" else "←"
        name = alias or node_id
        preview = body[:40] + "…" if len(body) > 40 else body
        return (flag, str(msg_id), name, preview, state, queued_at)

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def _refresh_table(self) -> None:
        table = self.query_one("#message_list", DataTable)
        table.clear()
        rows = self._fetch_rows()
        if not rows:
            table.add_row("(no messages)", "", "", "", "", "")
            self.query_one("#preview", Label).update("Select a message")
            return
        for raw in rows:
            table.add_row(*self._row_data(raw))
        table.move_cursor(row=0)
        self._update_preview_from_index(0)

    def _update_preview_from_index(self, idx: int) -> None:
        rows = self._fetch_rows()
        if idx < 0 or idx >= len(rows):
            return
        raw = rows[idx]
        # Build preview text generically
        client = DbClient(self.db_path)
        if self.tab_idx == 0:
            _, alias, node_id, body, state, ts = raw
            name = alias or node_id
            text = f"From: {name}\nState: {state}\nTime: {ts}\n\n{body}"
        elif self.tab_idx == 1:
            msg_id, alias, node_id, body, state, retries, next_at, error = raw
            name = alias or node_id
            err_str = f"\nError: {error}" if error else ""
            text = f"To: {name}\nState: {state}\nRetries: {retries}\nNext: {next_at or '-'}{err_str}\n\n{body}"
        else:
            msg_id, direction, alias, node_id, body, state, queued_at, sent_at, acked_at, error = raw
            name = alias or node_id
            dir_label = "To" if direction == "out" else "From"
            text = f"{dir_label}: {name}\nState: {state}\nQueued: {queued_at}\n\n{body}"

        self.query_one("#preview", Label).update(text)

    def on_data_table_row_selected(self, event) -> None:
        table = self.query_one("#message_list", DataTable)
        row_idx = event.cursor_row
        if row_idx is not None and 0 <= row_idx < table.row_count:
            self._update_preview_from_index(row_idx)

    def on_data_table_row_highlighted(self, event) -> None:
        self.on_data_table_row_selected(event)

    # ------------------------------------------------------------------
    # Status bar
    # ------------------------------------------------------------------

    def _update_status_bar(self) -> None:
        bar = self.query_one("#status_bar", Static)
        online = is_daemon_online(self.db_path, threshold_s=30.0)
        status = "[online]" if online else "[OFFLINE]"
        client = DbClient(self.db_path)
        try:
            unseen = len(client.inbox(unseen_only=True, limit=9999))
        except Exception:
            unseen = 0
        tab_label = self.TAB_NAMES[self.tab_idx]
        if unseen:
            bar.update(f"{status}  |  Tab: {tab_label}  |  {unseen} unread")
        else:
            bar.update(f"{status}  |  Tab: {tab_label}")

    # ------------------------------------------------------------------
    # Polling
    # ------------------------------------------------------------------

    def _poll_db(self) -> None:
        """Background poll: refresh table and status if DB has changed."""
        client = DbClient(self.db_path)
        try:
            result = client._conn().execute("SELECT MAX(id) FROM messages").fetchone()
            max_id = result[0] or 0
        except Exception:
            return
        if max_id != self._max_id_seen:
            self._max_id_seen = max_id
            self._refresh_table()
        self._update_status_bar()

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def action_tab_inbox(self) -> None:
        self._switch_tab(0)

    def action_tab_outbox(self) -> None:
        self._switch_tab(1)

    def action_tab_history(self) -> None:
        self._switch_tab(2)

    def _switch_tab(self, idx: int) -> None:
        if self.tab_idx == idx:
            return
        self.tab_idx = idx
        self._rebuild_columns()
        self._refresh_table()
        self._update_status_bar()

    def action_mark_read(self) -> None:
        if self.tab_idx != 0:
            return
        table = self.query_one("#message_list", DataTable)
        row_idx = table.cursor_row
        if row_idx is None:
            return
        rows = self._fetch_rows()
        if row_idx >= len(rows):
            return
        msg_id = rows[row_idx][0]
        DbClient(self.db_path).mark_read(msg_id)
        self._refresh_table()

    def action_delete(self) -> None:
        if self.tab_idx != 0:
            return
        table = self.query_one("#message_list", DataTable)
        row_idx = table.cursor_row
        if row_idx is None:
            return
        rows = self._fetch_rows()
        if row_idx >= len(rows):
            return
        msg_id = rows[row_idx][0]

        def on_confirm(confirmed: bool | None) -> None:
            if confirmed:
                DbClient(self.db_path).mark_deleted(msg_id)
                self._refresh_table()

        self.app.push_screen(ConfirmDeleteModal(), on_confirm)

    def action_new(self) -> None:
        self.app.push_screen(ComposeScreen(db_path=self.db_path))

    def action_reply(self) -> None:
        if self.tab_idx != 0:
            return
        table = self.query_one("#message_list", DataTable)
        row_idx = table.cursor_row
        if row_idx is None:
            return
        rows = self._fetch_rows()
        if row_idx >= len(rows):
            return
        msg_id = rows[row_idx][0]
        msg = DbClient(self.db_path).get_message(msg_id)
        if msg is None:
            return
        sender = DbClient(self.db_path).get_sender_by_id(msg["peer_id"])
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
