# Per-Sender Auto-Delete Policy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire up the existing global auto-delete default and add per-sender TOML overrides so that `meshcli read` and the TUI's mark-read action both stamp `auto_delete_at` correctly using a TOML per-sender → DB per-sender → global default precedence chain.

**Architecture:** `Config` gains an `auto_delete_per_sender` dict (parsed from `[auto_delete.senders.*]` in config.toml) and a `resolve_auto_delete(node_id, db_override)` method that implements the three-level precedence chain. Both the CLI and TUI load `Config` at startup and call `resolve_auto_delete` before passing the TTL to `DbClient.mark_read`.

**Tech Stack:** Python 3.9+, `tomllib` (stdlib), `textual`, `sqlite3`, `pytest`

---

## File Map

| File | Change |
|------|--------|
| `meshtad/config.py` | Add `auto_delete_per_sender` field; add TOML parsing for `[auto_delete.senders.*]`; add `resolve_auto_delete` method |
| `meshtad/cli.py` | Add `--config` argument; load `Config.from_toml` at startup; call `resolve_auto_delete` before `mark_read` |
| `meshtad/tui/app.py` | Accept `cfg_path` param; load `Config`; pass it to `InboxScreen` |
| `meshtad/tui/screens/inbox.py` | Accept `cfg` param in `__init__`; call `resolve_auto_delete` in `action_mark_read` |
| `tests/test_config_file.py` | Add `TestAutoDeletePerSender` class (5 tests) |
| `tests/test_daemon.py` | Add `TestAutoDeletePolicy` class (2 integration tests) |

---

## Task 1: Config — add `auto_delete_per_sender` field and TOML parsing

**Files:**
- Modify: `tests/test_config_file.py`
- Modify: `meshtad/config.py`

- [ ] **Step 1: Write the failing tests**

Add the following class at the bottom of `tests/test_config_file.py`:

```python
class TestAutoDeletePerSender:
    def test_parses_per_sender_entry(self):
        """[auto_delete.senders."!id"] after_s = N is loaded into auto_delete_per_sender."""
        with tempfile.TemporaryDirectory() as td:
            p = pathlib.Path(td) / "config.toml"
            p.write_text("""
[auto_delete]
global_s = 3600

[auto_delete.senders."!aabbccdd"]
after_s = 86400
""")
            cfg = Config.from_toml(p)
            assert cfg.auto_delete_per_sender == {"!aabbccdd": 86400}
            assert cfg.auto_delete_global_s == 3600

    def test_multiple_sender_entries(self):
        """Multiple senders each get their own entry."""
        with tempfile.TemporaryDirectory() as td:
            p = pathlib.Path(td) / "config.toml"
            p.write_text("""
[auto_delete.senders."!aabbccdd"]
after_s = 86400

[auto_delete.senders."!11223344"]
after_s = 0
""")
            cfg = Config.from_toml(p)
            assert cfg.auto_delete_per_sender == {"!aabbccdd": 86400, "!11223344": 0}
```

- [ ] **Step 2: Run to confirm failure**

```bash
.venv/bin/python -m pytest tests/test_config_file.py::TestAutoDeletePerSender -v
```

Expected: `AttributeError: 'Config' object has no attribute 'auto_delete_per_sender'`

- [ ] **Step 3: Add the field and parsing to `meshtad/config.py`**

In the `Config` dataclass, add the new field after `auto_delete_global_s`:

```python
auto_delete_per_sender: dict = field(default_factory=dict)
```

The top of the file already imports `field` from `dataclasses`. No new import needed.

In `from_toml`, after the existing `if "global_s" in auto_delete:` block, add:

```python
        sender_overrides = auto_delete.get("senders", {})
        for nid, sub in sender_overrides.items():
            if isinstance(sub, dict) and "after_s" in sub:
                cfg.auto_delete_per_sender[nid] = int(sub["after_s"])
```

- [ ] **Step 4: Run to confirm passing**

```bash
.venv/bin/python -m pytest tests/test_config_file.py::TestAutoDeletePerSender -v
```

Expected: 2 tests PASS.

- [ ] **Step 5: Run full test suite to confirm no regressions**

```bash
.venv/bin/python -m pytest tests/ -v
```

Expected: all existing tests still PASS.

- [ ] **Step 6: Commit**

```bash
git add meshtad/config.py tests/test_config_file.py
git commit -m "feat(config): add auto_delete_per_sender field and TOML parsing"
```

---

## Task 2: Config — add `resolve_auto_delete` method

**Files:**
- Modify: `tests/test_config_file.py`
- Modify: `meshtad/config.py`

- [ ] **Step 1: Write the failing tests**

Add inside the existing `TestAutoDeletePerSender` class in `tests/test_config_file.py`:

```python
    def test_resolve_toml_wins_over_db(self):
        """TOML per-sender entry takes precedence over the DB override."""
        cfg = Config(
            db_path=pathlib.Path("/tmp/x.db"),
            auto_delete_per_sender={"!aabb": 7200},
        )
        assert cfg.resolve_auto_delete("!aabb", db_override=1800) == 7200

    def test_resolve_db_fallback_when_no_toml_entry(self):
        """When sender absent from TOML, DB override is used."""
        cfg = Config(db_path=pathlib.Path("/tmp/x.db"))
        assert cfg.resolve_auto_delete("!aabb", db_override=1800) == 1800

    def test_resolve_global_fallback_when_neither(self):
        """When no TOML or DB override, global_s is used."""
        cfg = Config(db_path=pathlib.Path("/tmp/x.db"), auto_delete_global_s=900)
        assert cfg.resolve_auto_delete("!aabb", db_override=None) == 900

    def test_resolve_toml_zero_means_never(self):
        """after_s = 0 in TOML returns None (explicit never), even with global_s set."""
        cfg = Config(
            db_path=pathlib.Path("/tmp/x.db"),
            auto_delete_per_sender={"!aabb": 0},
            auto_delete_global_s=3600,
        )
        assert cfg.resolve_auto_delete("!aabb", db_override=None) is None

    def test_resolve_no_config_returns_none(self):
        """With no overrides at any level, result is None (no auto-delete)."""
        cfg = Config(db_path=pathlib.Path("/tmp/x.db"))
        assert cfg.resolve_auto_delete("!aabb", db_override=None) is None
```

- [ ] **Step 2: Run to confirm failure**

```bash
.venv/bin/python -m pytest tests/test_config_file.py::TestAutoDeletePerSender -v
```

Expected: 5 failures with `AttributeError: 'Config' object has no attribute 'resolve_auto_delete'`

- [ ] **Step 3: Add `resolve_auto_delete` to `Config` in `meshtad/config.py`**

Add this method inside the `Config` class, after `from_toml`:

```python
    def resolve_auto_delete(self, node_id: str, db_override) -> Optional[int]:
        """Return effective auto-delete TTL in seconds, or None for no auto-delete.

        Precedence: TOML per-sender → DB per-sender → global default.
        A value of 0 at any level means explicit "never" (returns None).
        """
        if node_id in self.auto_delete_per_sender:
            v = self.auto_delete_per_sender[node_id]
            return int(v) if v else None
        if db_override is not None:
            return int(db_override) if db_override else None
        return self.auto_delete_global_s
```

- [ ] **Step 4: Run to confirm passing**

```bash
.venv/bin/python -m pytest tests/test_config_file.py::TestAutoDeletePerSender -v
```

Expected: all 7 tests in this class PASS.

- [ ] **Step 5: Run full suite**

```bash
.venv/bin/python -m pytest tests/ -v
```

Expected: all existing tests still PASS.

- [ ] **Step 6: Commit**

```bash
git add meshtad/config.py tests/test_config_file.py
git commit -m "feat(config): add resolve_auto_delete with TOML > DB > global precedence"
```

---

## Task 3: CLI — load config and use resolver at `mark_read` time

**Files:**
- Modify: `tests/test_config_file.py` (add CLI integration test)
- Modify: `meshtad/cli.py`

- [ ] **Step 1: Write the failing test**

Add a new class at the bottom of `tests/test_config_file.py`:

```python
class TestCliAutoDelete:
    def test_read_command_applies_toml_per_sender_ttl(self):
        """meshcli read stamps auto_delete_at using the TOML per-sender TTL."""
        import sys
        from unittest.mock import patch
        from meshtad.cli import main as cli_main
        from meshtad.db import DbClient, DbThread

        with tempfile.TemporaryDirectory() as td:
            td_path = pathlib.Path(td)
            db_path = td_path / "meshtad.db"
            cfg_path = td_path / "config.toml"

            # Create DB with a sender and an UNSEEN inbound message
            t = DbThread(db_path)
            t.start()
            t.wait_ready(timeout=5.0)
            sid = t.execute("INSERT INTO senders (node_id) VALUES ('!aabbccdd')")
            t.execute(
                "INSERT INTO messages (direction, peer_id, body, state) VALUES ('in',?,?,'UNSEEN')",
                (sid, "hello"),
            )
            rows = t.execute("SELECT id FROM messages WHERE direction='in'")
            msg_id = rows[0][0]
            t.stop()

            # Config with a per-sender TTL for this sender
            cfg_path.write_text("""
[auto_delete.senders."!aabbccdd"]
after_s = 7200
""")

            with patch.object(sys, "argv", [
                "meshcli", "--db", str(db_path), "--config", str(cfg_path),
                "read", str(msg_id),
            ]):
                cli_main()

            client = DbClient(db_path)
            msg = client.get_message(msg_id)
            assert msg["state"] == "SEEN"
            assert msg["auto_delete_at"] is not None
```

- [ ] **Step 2: Run to confirm failure**

```bash
.venv/bin/python -m pytest tests/test_config_file.py::TestCliAutoDelete -v
```

Expected: FAIL — `cli_main()` will not crash but `auto_delete_at` will be `None` because the resolver is not yet called.

- [ ] **Step 3: Add `--config` to the CLI and wire the resolver**

In `meshtad/cli.py`, add the default path constant after the imports (at module level):

```python
_DEFAULT_CONFIG_PATH = pathlib.Path("~/.config/meshtad/config.toml").expanduser()
```

Add `--config` to the top-level `ArgumentParser` inside `main()`, before the subparsers block:

```python
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to config TOML (default: ~/.config/meshtad/config.toml)",
    )
```

After the `"tui"` early-return block (around line 86), load the config before creating `DbClient`:

```python
    cfg_path = (args.config or _DEFAULT_CONFIG_PATH).expanduser()
    cfg = Config.from_toml(cfg_path)
    db_path = args.db or Config.default().db_path
    db = DbClient(db_path)
```

In the `args.cmd == "read"` block, replace:

```python
        srow = db.get_sender_by_id(sender_id)
        ad = srow["auto_delete_after_s"] if srow else None
        db.mark_read(args.id, auto_delete_after_s=ad)
```

with:

```python
        srow = db.get_sender_by_id(sender_id)
        node_id = srow["node_id"] if srow else ""
        db_ad = srow["auto_delete_after_s"] if srow else None
        ad = cfg.resolve_auto_delete(node_id, db_ad)
        db.mark_read(args.id, auto_delete_after_s=ad)
```

- [ ] **Step 4: Run to confirm passing**

```bash
.venv/bin/python -m pytest tests/test_config_file.py::TestCliAutoDelete -v
```

Expected: PASS.

- [ ] **Step 5: Run full suite**

```bash
.venv/bin/python -m pytest tests/ -v
```

Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add meshtad/cli.py tests/test_config_file.py
git commit -m "feat(cli): load config, apply resolve_auto_delete at mark_read"
```

---

## Task 4: TUI — thread config through app and use resolver in `action_mark_read`

**Files:**
- Modify: `tests/test_config_file.py` (add TUI path test)
- Modify: `meshtad/tui/app.py`
- Modify: `meshtad/tui/screens/inbox.py`

- [ ] **Step 1: Write the failing test**

Add a new class at the bottom of `tests/test_config_file.py`:

```python
class TestTuiAutoDelete:
    def test_action_mark_read_uses_resolved_ttl(self):
        """InboxScreen.action_mark_read applies the config TTL via resolve_auto_delete."""
        from meshtad.db import DbClient, DbThread
        from meshtad.config import Config

        with tempfile.TemporaryDirectory() as td:
            td_path = pathlib.Path(td)
            db_path = td_path / "meshtad.db"

            # Set up DB with sender and UNSEEN message
            t = DbThread(db_path)
            t.start()
            t.wait_ready(timeout=5.0)
            sid = t.execute("INSERT INTO senders (node_id) VALUES ('!tuidest1')")
            t.execute(
                "INSERT INTO messages (direction, peer_id, body, state) VALUES ('in',?,?,'UNSEEN')",
                (sid, "tui test"),
            )
            rows = t.execute("SELECT id FROM messages WHERE direction='in'")
            msg_id = rows[0][0]
            t.stop()

            # Config with a global default TTL
            cfg = Config(db_path=db_path, auto_delete_global_s=1800)

            # Simulate what action_mark_read does
            client = DbClient(db_path)
            msg = client.get_message(msg_id)
            sender = client.get_sender_by_id(msg["peer_id"])
            node_id = sender["node_id"] if sender else ""
            db_ad = sender["auto_delete_after_s"] if sender else None
            ad = cfg.resolve_auto_delete(node_id, db_ad)
            client.mark_read(msg_id, auto_delete_after_s=ad)

            result = client.get_message(msg_id)
            assert result["state"] == "SEEN"
            assert result["auto_delete_at"] is not None
```

- [ ] **Step 2: Run to confirm passing**

This test exercises the exact logic path being added to `action_mark_read`. It should already pass since `resolve_auto_delete` is complete from Task 2 and `DbClient.mark_read` already accepts a TTL. Its purpose is to lock in the behavior before wiring it into the TUI widget.

```bash
.venv/bin/python -m pytest tests/test_config_file.py::TestTuiAutoDelete -v
```

Expected: PASS.

- [ ] **Step 3: Update `meshtad/tui/app.py` to load config and pass it to `InboxScreen`**

Replace the entire file content with:

```python
"""Textual TUI app for meshtad."""
from __future__ import annotations

import pathlib

from textual.app import App
from textual.screen import Screen

from meshtad.config import Config
from meshtad.tui.screens import InboxScreen

_DEFAULT_CONFIG_PATH = pathlib.Path("~/.config/meshtad/config.toml").expanduser()


class MeshtuiApp(App):
    """Main Textual application."""

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("ctrl+c", "quit", "Quit"),
    ]

    def __init__(
        self,
        db_path: pathlib.Path | None = None,
        cfg_path: pathlib.Path | None = None,
    ) -> None:
        self.db_path = db_path or pathlib.Path.home() / ".local" / "share" / "meshtad" / "meshtad.db"
        self.cfg = Config.from_toml((cfg_path or _DEFAULT_CONFIG_PATH).expanduser())
        super().__init__()

    def get_default_screen(self) -> Screen:
        return InboxScreen(db_path=self.db_path, cfg=self.cfg)

    def action_quit(self) -> None:
        self.exit()


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="meshtui — TUI for meshtad")
    parser.add_argument("--db", type=pathlib.Path, default=None, help="Path to meshtad.db")
    parser.add_argument(
        "--config",
        type=pathlib.Path,
        default=None,
        help="Path to config TOML (default: ~/.config/meshtad/config.toml)",
    )
    args = parser.parse_args()
    app = MeshtuiApp(db_path=args.db, cfg_path=args.config)
    app.run()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Update `InboxScreen.__init__` and `action_mark_read` in `meshtad/tui/screens/inbox.py`**

Change the `__init__` signature to accept `cfg`:

```python
    def __init__(self, db_path, cfg=None, **kwargs) -> None:
        super().__init__(**kwargs)
        self.db_path = db_path
        from meshtad.config import Config
        _default_cfg_path = pathlib.Path("~/.config/meshtad/config.toml").expanduser()
        self.cfg = cfg if cfg is not None else Config.from_toml(_default_cfg_path)
        self.tab_idx = 0
        self._max_id_seen = 0
        self._poll_timer = None
```

You'll need to add the `pathlib` import at the top of `inbox.py` if it is not already there. Check the existing imports — add `import pathlib` if missing.

Replace the `action_mark_read` method body:

```python
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
        client = DbClient(self.db_path)
        msg = client.get_message(msg_id)
        if msg is not None:
            sender = client.get_sender_by_id(msg["peer_id"])
            node_id = sender["node_id"] if sender else ""
            db_ad = sender["auto_delete_after_s"] if sender else None
            ad = self.cfg.resolve_auto_delete(node_id, db_ad)
            client.mark_read(msg_id, auto_delete_after_s=ad)
        self._refresh_table(cursor_row=row_idx)
```

- [ ] **Step 5: Run full suite**

```bash
.venv/bin/python -m pytest tests/ -v
```

Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add meshtad/tui/app.py meshtad/tui/screens/inbox.py tests/test_config_file.py
git commit -m "feat(tui): pass config to InboxScreen, apply resolve_auto_delete at mark_read"
```

---

## Task 5: Integration — global default flows end-to-end through the daemon

**Files:**
- Modify: `tests/test_daemon.py`

- [ ] **Step 1: Write the failing tests**

Add a new class at the bottom of `tests/test_daemon.py` (after the existing test classes, using the existing `tmp_db` and `db_thread` fixtures and the `_daemon_with_mock` helper that are already defined in that file):

```python
class TestAutoDeletePolicy:
    def test_global_default_flows_through_resolve_and_mark_read(self, tmp_db, db_thread):
        """resolve_auto_delete uses global_s when no TOML or DB override; mark_read stamps auto_delete_at."""
        from meshtad.config import Config
        from meshtad.db import DbClient

        sid = db_thread.execute("INSERT INTO senders (node_id) VALUES ('!poltest1')")
        db_thread.execute(
            "INSERT INTO messages (direction, peer_id, body, state) VALUES ('in',?,?,'UNSEEN')",
            (int(sid), "global default test"),
        )
        rows = db_thread.execute("SELECT id FROM messages WHERE body='global default test'")
        msg_id = rows[0][0]

        cfg = Config(db_path=tmp_db, auto_delete_global_s=3600)
        client = DbClient(tmp_db)
        srow = client.get_sender_by_id(int(sid))
        ad = cfg.resolve_auto_delete(srow["node_id"], srow["auto_delete_after_s"])
        assert ad == 3600
        client.mark_read(msg_id, auto_delete_after_s=ad)

        msg = client.get_message(msg_id)
        assert msg["state"] == "SEEN"
        assert msg["auto_delete_at"] is not None

    def test_sched_tick_deletes_message_with_resolved_auto_delete_at(self, tmp_db, db_thread):
        """After resolve + mark_read stamps auto_delete_at in the past, _sched_tick marks it DELETED."""
        from meshtad.config import Config
        from meshtad.db import DbClient

        # Insert sender with no per-sender DB override (auto_delete_after_s NULL)
        sid = db_thread.execute("INSERT INTO senders (node_id) VALUES ('!poltest2')")
        # Insert a SEEN message with auto_delete_at already in the past (simulates TTL elapsed)
        db_thread.execute(
            "INSERT INTO messages (direction, peer_id, body, state, auto_delete_at) "
            "VALUES ('in',?,?,'SEEN','2000-01-01T00:00:00Z')",
            (int(sid), "expired via global"),
        )

        d = _daemon_with_mock(tmp_db)
        d.db = db_thread
        d._sched_tick()

        rows = db_thread.execute(
            "SELECT state FROM messages WHERE body='expired via global'"
        )
        assert rows[0]["state"] == "DELETED"
```

- [ ] **Step 2: Run to confirm passing**

```bash
.venv/bin/python -m pytest tests/test_daemon.py::TestAutoDeletePolicy -v
```

Expected: both tests PASS (the logic is complete from prior tasks).

- [ ] **Step 3: Run full suite one final time**

```bash
.venv/bin/python -m pytest tests/ -v
```

Expected: all tests PASS, no regressions.

- [ ] **Step 4: Commit**

```bash
git add tests/test_daemon.py
git commit -m "test: integration tests for per-sender auto-delete policy end-to-end"
```

---

## Self-review notes

- **Spec coverage:** all spec requirements covered — TOML parsing (Task 1), resolution method (Task 2), CLI (Task 3), TUI (Task 4), daemon end-to-end (Task 5).
- **`auto_delete_per_sender` default:** uses `field(default_factory=dict)` — `dataclasses.field` is already imported in `config.py`.
- **`pathlib` import in `inbox.py`:** check whether it needs to be added; the `_DEFAULT_CONFIG_PATH` construction inside `__init__` requires it.
- **`db_thread.execute` returns the last rowid for INSERT:** the existing tests in `test_daemon.py` rely on this (e.g. `sid = db_thread.execute("INSERT INTO senders …")`). Task 5 follows the same pattern.
- **`auto_delete_after_s` on fresh sender row is `None`:** SQLite `SELECT *` via `DbThread` returns rows as `sqlite3.Row` (dict-like). `srow["auto_delete_after_s"]` on a newly inserted sender returns `None`, which is the correct DB fallback input to `resolve_auto_delete`.
