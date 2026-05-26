#!/usr/bin/env python3
"""Populate a meshtad database with realistic mock data for TUI testing.

Usage:
    python scripts/mockdata.py [--db /path/to/meshtad.db]

Creates senders and messages across all states (UNSEEN, SEEN, DELETED,
QUEUED, SENT, ACKED, FAILED) so the TUI tabs look alive.
"""
from __future__ import annotations

import argparse
import pathlib
import sqlite3
from datetime import datetime, timezone, timedelta


DEFAULT_DB = pathlib.Path("/tmp/meshtad_tuitest.db")

SENDERS = [
    ("!f6974350", "home",    "HomeNode",  "Base Station"),
    ("!d4814348", "deck",    "TDeck",     "PolyDeck Unit"),
    ("!a1b2c3d5", "alex",    "Alex",      None),
    ("!e5f6g7h9", "emma",    "Emma",      None),
    ("!11111112", "relay",   "Relay01",   "Hilltop Repeater"),
]

MESSAGES = [
    # ---- INBOUND ----
    # UNSEEN (flag '*')
    {"direction": "in",  "peer_alias": "deck",  "state": "UNSEEN",
     "body": "Hey, are you receiving me? Signal is weak on my end.",
     "offset_m": -5},
    {"direction": "in",  "peer_alias": "alex",  "state": "UNSEEN",
     "body": "Meeting at the usual spot in 30. Bring the gear.",
     "offset_m": -12},
    {"direction": "in",  "peer_alias": "emma",  "state": "UNSEEN",
     "body": "Can you check the weather forecast for tonight?",
     "offset_m": -45},
    # SEEN
    {"direction": "in",  "peer_alias": "home",  "state": "SEEN",
     "body": "Daemon heartbeat OK. Radio connected on EU868 LongFast.",
     "offset_m": -120},
    {"direction": "in",  "peer_alias": "deck",  "state": "SEEN",
     "body": "Battery at 67%. Screen dimming after 5 min idle.",
     "offset_m": -180},
    # DELETED (won't show in inbox/history)
    {"direction": "in",  "peer_alias": "relay", "state": "DELETED",
     "body": "SPAM: Buy cheap meshtastic nodes now!!!",
     "offset_m": -240},

    # ---- OUTBOUND ----
    # QUEUED
    {"direction": "out", "peer_alias": "home",  "state": "QUEUED",
     "body": "#ask what's the capital of Mongolia",
     "offset_m": -3},
    {"direction": "out", "peer_alias": "alex",  "state": "QUEUED",
     "body": "Leaving base now. ETA 20 minutes.",
     "offset_m": -8},
    # SENT (in-flight, waiting for ACK)
    {"direction": "out", "peer_alias": "deck",  "state": "SENT",
     "body": "#signal emma running late, be there in 40",
     "offset_m": -15, "packet_id": 1001},
    # ACKED
    {"direction": "out", "peer_alias": "home",  "state": "ACKED",
     "body": "yes, bring the spare antenna",
     "offset_m": -60,  "packet_id": 994},
    {"direction": "out", "peer_alias": "emma",  "state": "ACKED",
     "body": "roger that, switching to night mode",
     "offset_m": -90,  "packet_id": 991},
    # FAILED
    {"direction": "out", "peer_alias": "relay", "state": "FAILED",
     "body": "test ping to hilltop — any relay in range?",
     "offset_m": -200, "retry_count": 5,
     "error": "ack_timeout", "packet_id": 880},
]


def _iso(offset_m: int = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=offset_m)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """Apply the same schema as meshtad's DbThread."""
    conn.executescript("""
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
    """)
    conn.commit()


def populate(db_path: pathlib.Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    _ensure_schema(conn)

    # ---- senders ----
    alias_to_id: dict[str, int] = {}
    for node_id, alias, short_name, long_name in SENDERS:
        cur = conn.execute("SELECT id FROM senders WHERE node_id=?", (node_id,))
        row = cur.fetchone()
        if row:
            sender_id = row[0]
        else:
            cur = conn.execute(
                "INSERT INTO senders (node_id, alias, short_name, long_name) VALUES (?,?,?,?)",
                (node_id, alias, short_name, long_name),
            )
            sender_id = cur.lastrowid
        alias_to_id[alias] = sender_id

    # clear any existing mock messages (keep senders)
    conn.execute("DELETE FROM messages")
    conn.commit()

    # ---- messages ----
    for m in MESSAGES:
        peer_id = alias_to_id[m["peer_alias"]]
        state = m["state"]
        direction = m["direction"]
        body = m["body"]
        queued_at = _iso(m["offset_m"])

        sent_at = None
        acked_at = None
        deleted_at = None
        error = m.get("error")
        retry_count = m.get("retry_count", 0)
        next_attempt = None
        packet_id = m.get("packet_id")

        if state == "SENT":
            sent_at = queued_at
        elif state == "ACKED":
            sent_at = _iso(m["offset_m"] + 1)
            acked_at = _iso(m["offset_m"] + 2)
        elif state == "FAILED":
            sent_at = _iso(m["offset_m"] + 1)
        elif state == "DELETED":
            deleted_at = queued_at

        if state == "QUEUED" and retry_count > 0:
            next_attempt = _iso(m["offset_m"] + 5)

        conn.execute(
            """INSERT INTO messages
               (direction, peer_id, body, state, queued_at,
                sent_at, acked_at, retry_count, next_attempt_at,
                error, deleted_at, meshtastic_packet_id)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (direction, peer_id, body, state, queued_at,
             sent_at, acked_at, retry_count, next_attempt,
             error, deleted_at, packet_id),
        )

    # ---- heartbeat (so TUI shows "online") ----
    pid = "mock"
    now_heartbeat = _iso()
    conn.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES ('daemon_pid', ?)",
        (pid,),
    )
    conn.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES ('daemon_heartbeat', ?)",
        (now_heartbeat,),
    )

    conn.commit()
    conn.close()
    print(f"Populated {db_path} with {len(MESSAGES)} messages and {len(SENDERS)} senders.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Populate meshtad DB with mock TUI data")
    parser.add_argument("--db", type=pathlib.Path, default=DEFAULT_DB, help="Path to database")
    args = parser.parse_args()
    populate(args.db)


if __name__ == "__main__":
    main()
