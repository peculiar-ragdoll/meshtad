"""TUI heartbeat checker — reads daemon liveness from the meta table."""
from __future__ import annotations

import pathlib
from datetime import datetime, timezone


def is_daemon_online(db_path: pathlib.Path, threshold_s: float = 30.0) -> bool:
    """Return True if the daemon heartbeat in meta is fresh.

    Queries the `meta` table for the `daemon_heartbeat` key, parses it
    as an ISO-8601 UTC timestamp, and checks whether it is within
    `threshold_s` of now.
    """
    import sqlite3

    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT value FROM meta WHERE key='daemon_heartbeat'").fetchone()
        conn.close()
    except Exception:
        return False

    if not row:
        return False

    ts_str = row[0]
    try:
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    except Exception:
        return False

    age_s = (datetime.now(timezone.utc) - dt).total_seconds()
    return age_s <= threshold_s
