"""Inbox screen with tabbed message viewer."""
from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Label, Static

from meshtad.db import DbClient
from meshtad.tui.heartbeat import is_daemon_online
from meshtad.tui.screens.compose import ComposeScreen
from meshtad.tui.screens.modals import ConfirmDeleteModal, HelpModal, SetAliasModal


class InboxScreen(Screen):
    """Tabbed message viewer with inbox / outbox / history."""

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("r", "reply", "Reply"),
        ("n", "new", "New"),
        ("m", "mark_read", "Mark read"),
        ("d", "delete", "Delete"),
        ("a", "set_alias", "Set alias"),
        ("question_mark", "help", "Help"),
        ("left", "prev_tab", "Prev tab"),
        ("right", "next_tab", "Next tab"),
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
            msg_id, alias, node_id, body, state, ts = raw_row
            flag = "*" if state == "UNSEEN" else " "
            name = alias or node_id
            preview = body[:40] + "…" if len(body) > 40 else body
            return (flag, str(msg_id), name, preview, state, ts)

        if self.tab_idx == 1:
            msg_id, alias, node_id, body, state, retries, next_at, error = raw_row
            name = alias or node_id
            preview = body[:40] + "…" if len(body) > 40 else body
            next_str = next_at or "-"
            return (str(msg_id), name, preview, state, str(retries), next_str)

        msg_id, direction, alias, node_id, body, state, queued_at, sent_at, acked_at, error = raw_row
        flag = "→" if direction == "out" else "←"
        name = alias or node_id
        preview = body[:40] + "…" if len(body) > 40 else body
        return (flag, str(msg_id), name, preview, state, queued_at)

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def _refresh_table(self, cursor_row: int | None = None) -> None:
        table = self.query_one("#message_list", DataTable)
        table.clear()
        rows = self._fetch_rows()
        if not rows:
            table.add_row("(no messages)", "", "", "", "", "")
            self.query_one("#preview", Label).update("Select a message")
            return
        for raw in rows:
            table.add_row(*self._row_data(raw))
        idx = cursor_row if cursor_row is not None else 0
        idx = max(0, min(idx, len(rows) - 1))
        table.move_cursor(row=idx)
        self._update_preview_from_index(idx)

    def _update_preview_from_index(self, idx: int) -> None:
        rows = self._fetch_rows()
        if idx < 0 or idx >= len(rows):
            return
        raw = rows[idx]
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
        try:
            max_id = DbClient(self.db_path).max_message_id()
        except Exception:
            return
        if max_id != self._max_id_seen:
            self._max_id_seen = max_id
            self._refresh_table()
        self._update_status_bar()

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def action_help(self) -> None:
        """Show a modal with the current screen's key bindings."""
        bindings = [(b.key, b.description) for b in self._bindings.shown_keys]
        self.app.push_screen(HelpModal(bindings))

    def action_tab_inbox(self) -> None:
        self._switch_tab(0)

    def action_tab_outbox(self) -> None:
        self._switch_tab(1)

    def action_tab_history(self) -> None:
        self._switch_tab(2)

    def action_prev_tab(self) -> None:
        self._switch_tab((self.tab_idx - 1) % 3)

    def action_next_tab(self) -> None:
        self._switch_tab((self.tab_idx + 1) % 3)

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
        self._refresh_table(cursor_row=row_idx)

    def action_delete(self) -> None:
        if self.tab_idx == 2:  # history is read-only
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
                self._refresh_table(cursor_row=row_idx)

        self.app.push_screen(ConfirmDeleteModal(), on_confirm)

    def action_set_alias(self) -> None:
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
        node_id = sender["node_id"] if sender else ""
        existing_alias = sender["alias"] if sender else None

        def on_confirm(alias: str | None) -> None:
            if alias is None or not alias.strip():
                return  # dismissed or empty
            alias = alias.strip()
            client = DbClient(self.db_path)
            sender_id = client.ensure_sender(node_id)
            client.update_sender(sender_id, alias=alias)
            self._refresh_table(cursor_row=row_idx)

        self.app.push_screen(
            SetAliasModal(node_id=node_id, existing_alias=existing_alias),
            on_confirm,
        )

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
        reply_from = alias or node_id or "unknown"
        self.app.push_screen(
            ComposeScreen(
                db_path=self.db_path,
                to_alias=alias,
                to_node_id=node_id,
                reply_to_body=msg["body"],
                reply_from=reply_from,
            )
        )
