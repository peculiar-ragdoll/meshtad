"""meshcli — thin client for meshtad."""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

from meshtad.config import Config, MAX_PAYLOAD_BYTES, DEFAULT_CONFIG_PATH
from meshtad.db import DbClient


def _fmt_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}TB"


def _trunc(text: str, width: int = 40) -> str:
    if len(text) <= width:
        return text
    return text[: width - 1] + "…"


def main() -> int:
    parser = argparse.ArgumentParser(description="meshcli — thin client for meshtad")
    parser.add_argument("--db", type=Path, default=None, help="Path to meshtad.db")
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to config TOML (default: ~/.config/meshtad/config.toml)",
    )
    sub = parser.add_subparsers(dest="cmd")

    p_send = sub.add_parser("send", help="Send a DM (fire-and-forget)")
    p_send.add_argument("dest", help="Alias or node ID")
    p_send.add_argument("body", nargs=argparse.REMAINDER, help="Message body")

    p_inbox = sub.add_parser("inbox", help="List inbound messages")
    p_inbox.add_argument("--unseen-only", action="store_true")
    p_inbox.add_argument("--limit", type=int, default=50)
    p_inbox.add_argument("--with", dest="with_alias", metavar="ALIAS")

    p_read = sub.add_parser("read", help="Read a message and mark SEEN")
    p_read.add_argument("id", type=int)

    p_delete = sub.add_parser("delete", help="Soft-delete a message")
    p_delete.add_argument("id", type=int)

    p_history = sub.add_parser("history", help="Message history (both directions)")
    p_history.add_argument("--with", dest="with_alias", metavar="ALIAS")
    p_history.add_argument("--limit", type=int, default=100)
    p_history.add_argument("--direction", choices=["in", "out"])

    p_outbox = sub.add_parser("outbox", help="List unsent/outbound messages")

    p_status = sub.add_parser("status", help="Show send status of a message")
    p_status.add_argument("id", type=int)

    p_alias = sub.add_parser("alias", help="Register or update a sender alias")
    p_alias.add_argument("node_id")
    p_alias.add_argument("alias")
    p_alias.add_argument("--auto-delete", type=int, default=None, help="Auto-delete seconds after SEEN")

    p_aliases = sub.add_parser("aliases", help="List known senders")

    p_dbstatus = sub.add_parser("db-status", help="Show database size and counts")

    p_vacuum = sub.add_parser("vacuum", help="Compact database (daemon should ideally be stopped)")

    p_detect = sub.add_parser("dongle-detect", help="Detect Meshtastic serial ports")

    p_eject = sub.add_parser("dongle-eject", help="Request daemon to release the radio")

    p_retry = sub.add_parser("retry", help="Retry a FAILED outbound message")
    p_retry.add_argument("id", type=int, help="Message ID to retry")

    p_tui = sub.add_parser("tui", help="Interactive TUI for meshtad")

    args = parser.parse_args()
    if not args.cmd:
        parser.print_help()
        return 1

    if args.cmd == "tui":
        from meshtad.tui.app import MeshtuiApp
        app = MeshtuiApp(db_path=args.db, cfg_path=args.config)
        app.run()
        return 0

    cfg_path = (args.config or DEFAULT_CONFIG_PATH).expanduser()
    cfg = Config.from_toml(cfg_path)
    db_path = args.db or Config.default().db_path
    db = DbClient(db_path)

    if args.cmd == "send":
        body = " ".join(args.body)
        byte_len = len(body.encode("utf-8"))
        if byte_len > MAX_PAYLOAD_BYTES:
            print(f"! message too long: {byte_len} bytes, limit {MAX_PAYLOAD_BYTES}")
            return 1
        resolved = db.resolve_alias(args.dest)
        if not resolved:
            print(f"! no alias or node-id matches '{args.dest}'")
            return 1
        sender_id, node_id = resolved
        msg_id = db.enqueue_outbound(sender_id, body)
        print(f"queued msg_id={msg_id} to {node_id}")
        return 0

    if args.cmd == "inbox":
        rows = db.inbox(unseen_only=args.unseen_only, limit=args.limit, with_alias=args.with_alias)
        if not rows:
            print("(empty)")
            return 0
        for msg_id, alias, node_id, body, state, ts in rows:
            flag = "*" if state == "UNSEEN" else " "
            name = alias or node_id
            print(f"[{flag}] {msg_id:4d}  {ts}  {name:12s}  {_trunc(body, 50)}")
        return 0

    if args.cmd == "read":
        row = db.get_message(args.id)
        if not row:
            print(f"! message {args.id} not found")
            return 1
        if row["direction"] != "in":
            print(f"! message {args.id} is outbound; use 'status' instead")
            return 1
        sender_id = row["peer_id"]
        srow = db.get_sender_by_id(sender_id)
        node_id = srow["node_id"] if srow else ""
        db_ad = srow["auto_delete_after_s"] if srow else None
        ad = cfg.resolve_auto_delete(node_id, db_ad)
        db.mark_read(args.id, auto_delete_after_s=ad)
        print(f"[{row['state']}] from {row['alias'] or row['node_id']}")
        print(row["body"])
        return 0

    if args.cmd == "delete":
        db.mark_deleted(args.id)
        print(f"deleted msg_id={args.id}")
        return 0

    if args.cmd == "history":
        rows = db.history(with_alias=args.with_alias, limit=args.limit, direction=args.direction)
        if not rows:
            print("(empty)")
            return 0
        for msg_id, direction, alias, node_id, body, state, queued, sent, acked, error in rows:
            arrow = ">>>" if direction == "out" else "<<<"
            name = alias or node_id
            ts = acked or sent or queued
            print(f"{arrow} {msg_id:4d}  {state:7s}  {ts}  {name:12s}  {_trunc(body, 50)}")
        return 0

    if args.cmd == "outbox":
        rows = db.outbox()
        if not rows:
            print("(empty)")
            return 0
        for msg_id, alias, node_id, body, state, retries, next_at, error in rows:
            name = alias or node_id
            next_str = f" next={next_at}" if next_at else ""
            print(f"{msg_id:4d}  {state:7s}  retries={retries}{next_str}  {name:12s}  {_trunc(body, 50)}")
        return 0

    if args.cmd == "status":
        row = db.get_message(args.id)
        if not row:
            print(f"! message {args.id} not found")
            return 1
        print(f"id:        {row['id']}")
        print(f"direction: {row['direction']}")
        print(f"peer:      {row['alias'] or row['node_id']}")
        print(f"state:     {row['state']}")
        print(f"queued:    {row['queued_at']}")
        print(f"sent:      {row['sent_at'] or '-'}")
        print(f"acked:     {row['acked_at'] or '-'}")
        print(f"retries:   {row['retry_count']}")
        print(f"error:     {row['error'] or '-'}")
        return 0

    if args.cmd == "alias":
        existing = db.resolve_alias(args.node_id)
        if existing:
            sender_id, _ = existing
            db.update_sender(sender_id, alias=args.alias, auto_delete_after_s=args.auto_delete)
            print(f"updated alias '{args.alias}' -> {args.node_id}")
        else:
            sid = db.ensure_sender(args.node_id, alias=args.alias, auto_delete_after_s=args.auto_delete)
            print(f"created alias '{args.alias}' -> {args.node_id} (id={sid})")
        return 0

    if args.cmd == "aliases":
        rows = db.list_senders()
        if not rows:
            print("(no senders)")
            return 0
        for sid, node_id, alias, short_name, long_name, ad, created in rows:
            name = alias or short_name or node_id
            ad_str = f" auto_delete={ad}s" if ad else ""
            print(f"{sid:4d}  {name:12s}  {node_id}{ad_str}")
        return 0

    if args.cmd == "db-status":
        size = db.db_size_bytes()
        print(f"DB: {db_path}")
        print(f"Size: {_fmt_size(size)}")
        counts = db.message_counts()
        for st, c in counts:
            print(f"  {st}: {c}")
        return 0

    if args.cmd == "vacuum":
        try:
            db.vacuum()
            new_size = db.db_size_bytes()
            print(f"Vacuumed. New size: {_fmt_size(new_size)}")
        except sqlite3.OperationalError as exc:
            if "database is locked" in str(exc):
                print("! database is locked (daemon running?). Stop daemon first, or retry.")
                return 1
            raise
        return 0

    if args.cmd == "dongle-detect":
        from meshtad.radio import Radio
        ports = Radio.detect_ports()
        if not ports:
            ports = Radio.find_macos_ports()
        if not ports:
            print("(no Meshtastic serial ports found)")
            return 0
        for p in ports:
            print(p)
        return 0

    if args.cmd == "dongle-eject":
        db.enqueue_control("eject")
        print("eject requested; daemon should release radio shortly")
        return 0

    if args.cmd == "retry":
        row = db.get_message(args.id)
        if not row:
            print(f"! message {args.id} not found")
            return 1
        if row["state"] != "FAILED":
            print(f"! message {args.id} is not FAILED (state={row['state']})")
            return 1
        db.retry_message(args.id)
        print(f"msg_id={args.id} requeued")
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
