"""SQLite schema and CRUD.

All daemon-side DB access funnels through DbThread (single writer thread).
Client-side (meshcli) opens its own connections in WAL mode and is read-only
except for outbox inserts and control-queue writes.
"""
from __future__ import annotations

import contextlib
import pathlib
import queue
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

SCHEMA = """
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS senders (
    id          INTEGER PRIMARY KEY,
    node_id     TEXT NOT NULL,
    alias       TEXT,
    short_name  TEXT,
    long_name   TEXT,
    auto_delete_after_s INTEGER,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_senders_node_id ON senders(node_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_senders_alias ON senders(alias) WHERE alias IS NOT NULL;

CREATE TABLE IF NOT EXISTS messages (
    id                  INTEGER PRIMARY KEY,
    direction           TEXT NOT NULL,
    peer_id             INTEGER NOT NULL REFERENCES senders(id),
    body                TEXT NOT NULL,
    state               TEXT NOT NULL DEFAULT 'UNSEEN'
        CHECK (state IN ('UNSEEN','SEEN','QUEUED','SENT','ACKED','FAILED','DELETED')),
    queued_at           TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    sent_at             TEXT,
    acked_at            TEXT,
    retry_count         INTEGER NOT NULL DEFAULT 0,
    next_attempt_at     TEXT,
    error               TEXT,
    deleted_at          TEXT,
    auto_delete_at      TEXT,
    meshtastic_packet_id INTEGER
);
CREATE INDEX IF NOT EXISTS idx_messages_inbox ON messages(peer_id, queued_at)
    WHERE direction = 'in' AND state IN ('UNSEEN','SEEN');
CREATE INDEX IF NOT EXISTS idx_messages_outbox ON messages(state, next_attempt_at, queued_at)
    WHERE direction = 'out' AND state IN ('QUEUED','SENT');
CREATE INDEX IF NOT EXISTS idx_messages_auto_delete ON messages(auto_delete_at)
    WHERE auto_delete_at IS NOT NULL;

CREATE TABLE IF NOT EXISTS control_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action TEXT NOT NULL,
    params TEXT,
    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


def _iso_now(offset_s: float = 0.0) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=offset_s)).strftime("%Y-%m-%dT%H:%M:%SZ")


class DbThread(threading.Thread):
    """Single SQLite writer thread. All daemon DB access funnels here."""

    def __init__(self, db_path: pathlib.Path):
        super().__init__(name="db-writer", daemon=True)
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._q: queue.Queue[tuple[str, tuple, queue.Queue]] = queue.Queue()
        self._ready = threading.Event()
        self._running = True
        self._conn: sqlite3.Connection | None = None

    def run(self) -> None:
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._ready.set()
        while self._running or not self._q.empty():
            try:
                sql, params, reply_q = self._q.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                cur = self._conn.execute(sql, params)
                if sql.strip().upper().startswith("SELECT"):
                    rows = cur.fetchall()
                    reply_q.put(("ok", rows))
                else:
                    self._conn.commit()
                    reply_q.put(("ok", cur.lastrowid))
            except Exception as exc:
                reply_q.put(("err", exc))

    def execute(self, sql: str, params: tuple = ()) -> Any:
        """Blocking call from other threads. Returns rows for SELECT, lastrowid otherwise."""
        reply_q: queue.Queue = queue.Queue(maxsize=1)
        self._q.put((sql, params, reply_q))
        status, payload = reply_q.get()
        if status == "err":
            raise payload
        return payload

    def stop(self) -> None:
        self._running = False
        self.join(timeout=5.0)
        if self._conn:
            self._conn.close()

    def wait_ready(self, timeout: float = 10.0) -> bool:
        return self._ready.wait(timeout=timeout)


class DbClient:
    """Thin SQLite client used by meshcli. Opens its own connection per call."""

    def __init__(self, db_path: pathlib.Path):
        self.db_path = db_path

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    # ---- senders ----

    def ensure_sender(self, node_id: str, **kwargs) -> int:
        with contextlib.closing(self._conn()) as conn:
            cur = conn.execute("SELECT id FROM senders WHERE node_id=?", (node_id,))
            row = cur.fetchone()
            if row:
                return int(row[0])
            conn.execute(
                "INSERT INTO senders (node_id, alias, short_name, long_name, auto_delete_after_s) VALUES (?,?,?,?,?)",
                (node_id, kwargs.get("alias"), kwargs.get("short_name"), kwargs.get("long_name"), kwargs.get("auto_delete_after_s")),
            )
            conn.commit()
            return int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])

    def resolve_alias(self, alias_or_id: str) -> tuple[int, str] | None:
        """Return (sender_id, node_id) or None."""
        with contextlib.closing(self._conn()) as conn:
            for col in ("alias", "node_id"):
                cur = conn.execute(f"SELECT id, node_id FROM senders WHERE {col}=? COLLATE NOCASE", (alias_or_id,))
                row = cur.fetchone()
                if row:
                    return int(row[0]), str(row[1])
            return None

    def get_sender_by_id(self, sender_id: int) -> sqlite3.Row | None:
        with contextlib.closing(self._conn()) as conn:
            return conn.execute("SELECT * FROM senders WHERE id=?", (sender_id,)).fetchone()

    def list_senders(self):
        with contextlib.closing(self._conn()) as conn:
            return conn.execute("SELECT * FROM senders ORDER BY alias, node_id").fetchall()

    def update_sender(self, sender_id: int, **fields):
        with contextlib.closing(self._conn()) as conn:
            for k, v in fields.items():
                conn.execute(f"UPDATE senders SET {k}=? WHERE id=?", (v, sender_id))
            conn.commit()

    # ---- messages ----

    def insert_inbound(self, peer_id: int, body: str, packet_id: int | None = None) -> int:
        with contextlib.closing(self._conn()) as conn:
            conn.execute(
                "INSERT INTO messages (direction, peer_id, body, state, meshtastic_packet_id) VALUES (?,?,?,'UNSEEN',?)",
                ("in", peer_id, body, packet_id),
            )
            conn.commit()
            return int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])

    def enqueue_outbound(self, peer_id: int, body: str) -> int:
        with contextlib.closing(self._conn()) as conn:
            conn.execute(
                "INSERT INTO messages (direction, peer_id, body, state) VALUES (?,?,?,'QUEUED')",
                ("out", peer_id, body),
            )
            conn.commit()
            return int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])

    def inbox(self, unseen_only: bool = False, limit: int = 50, with_alias: str | None = None):
        where = ["direction = 'in'", "state IN ('UNSEEN','SEEN')"]
        params: list = []
        if unseen_only:
            where.append("state = 'UNSEEN'")
        if with_alias:
            where.append("s.alias = ?")
            params.append(with_alias)
        sql = f"""
            SELECT m.id, s.alias, s.node_id, m.body, m.state, m.queued_at
            FROM messages m JOIN senders s ON s.id = m.peer_id
            WHERE {' AND '.join(where)}
            ORDER BY m.queued_at DESC
            LIMIT ?
        """
        params.append(limit)
        with contextlib.closing(self._conn()) as conn:
            return conn.execute(sql, params).fetchall()

    def history(self, with_alias: str | None = None, limit: int = 100, direction: str | None = None):
        where = ["m.state != 'DELETED'"]
        params: list = []
        if with_alias:
            where.append("s.alias = ?")
            params.append(with_alias)
        if direction:
            where.append("m.direction = ?")
            params.append(direction)
        sql = f"""
            SELECT m.id, m.direction, s.alias, s.node_id, m.body, m.state, m.queued_at, m.sent_at, m.acked_at, m.error
            FROM messages m JOIN senders s ON s.id = m.peer_id
            WHERE {' AND '.join(where)}
            ORDER BY m.queued_at DESC
            LIMIT ?
        """
        params.append(limit)
        with contextlib.closing(self._conn()) as conn:
            return conn.execute(sql, params).fetchall()

    def outbox(self):
        with contextlib.closing(self._conn()) as conn:
            return conn.execute("""
                SELECT m.id, s.alias, s.node_id, m.body, m.state, m.retry_count, m.next_attempt_at, m.error
                FROM messages m JOIN senders s ON s.id = m.peer_id
                WHERE m.direction = 'out' AND m.state IN ('QUEUED','SENT','FAILED')
                ORDER BY m.queued_at
            """).fetchall()

    def get_message(self, msg_id: int) -> sqlite3.Row | None:
        with contextlib.closing(self._conn()) as conn:
            return conn.execute("""
                SELECT m.*, s.alias, s.node_id FROM messages m
                JOIN senders s ON s.id = m.peer_id WHERE m.id=?
            """, (msg_id,)).fetchone()

    def mark_read(self, msg_id: int, auto_delete_after_s: int | None = None):
        delete_at = _iso_now(offset_s=auto_delete_after_s) if auto_delete_after_s is not None else None
        with contextlib.closing(self._conn()) as conn:
            conn.execute(
                "UPDATE messages SET state='SEEN', auto_delete_at=? WHERE id=? AND direction='in'",
                (delete_at, msg_id),
            )
            conn.commit()

    def mark_deleted(self, msg_id: int):
        with contextlib.closing(self._conn()) as conn:
            conn.execute(
                "UPDATE messages SET state='DELETED', deleted_at=? WHERE id=?",
                (_iso_now(), msg_id),
            )
            conn.commit()

    def vacuum(self):
        with contextlib.closing(self._conn()) as conn:
            conn.execute("VACUUM")

    def db_size_bytes(self) -> int:
        try:
            return self.db_path.stat().st_size
        except Exception:
            return 0

    # ---- control queue (client -> daemon) ----

    def enqueue_control(self, action: str, params: str = "{}") -> None:
        with contextlib.closing(self._conn()) as conn:
            conn.execute("INSERT INTO control_queue (action, params) VALUES (?,?)", (action, params))
            conn.commit()

    def message_counts(self) -> list[tuple[str, int]]:
        with contextlib.closing(self._conn()) as conn:
            return conn.execute("SELECT state, COUNT(*) FROM messages GROUP BY state").fetchall()

    def control_queue_counts(self) -> list[tuple[str, int]]:
        with contextlib.closing(self._conn()) as conn:
            return conn.execute(
                "SELECT action, COUNT(*) FROM control_queue GROUP BY action"
            ).fetchall()
