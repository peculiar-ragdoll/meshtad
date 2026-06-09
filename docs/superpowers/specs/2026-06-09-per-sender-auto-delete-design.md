# Per-Sender Auto-Delete Policy

**Date:** 2026-06-09  
**Status:** Approved, pending implementation

## Summary

Extend meshtad's auto-delete system so that messages from individual senders can expire at different rates. A global default in `[auto_delete]` applies to everyone; per-sender overrides in `[auto_delete.senders.*]` take precedence. The precedence chain is: TOML per-sender → DB per-sender → global default.

## Background

The current code has three partially-wired pieces:

- `Config.auto_delete_global_s` — parsed from `[auto_delete] global_s`, but **never used** to set `auto_delete_at` on messages (the fallback path in the CLI was `None`, not the global default).
- `senders.auto_delete_after_s` column — set via `meshcli sender edit --auto-delete N`, IS read in the CLI at `mark_read` time, but the global default isn't applied when the sender has no DB override.
- The TUI's `action_mark_read` calls `mark_read(msg_id)` with no TTL at all — auto-delete never fires for messages marked read through the TUI.

This spec closes all three gaps and adds the config.toml per-sender table.

## Config file format

```toml
[auto_delete]
global_s = 3600          # applies to all senders not individually overridden

[auto_delete.senders."!aabbccdd"]
after_s = 86400          # this sender's messages expire 24 h after reading

[auto_delete.senders."!11223344"]
after_s = 0              # explicit opt-out — never auto-delete, even if global_s is set
```

`after_s` absent for a sender → that sender is not in the dict → fall through to DB or global.  
`after_s = 0` → explicit "never" (returns `None` from the resolver, no `auto_delete_at` is set).

## Architecture

### Config layer (`config.py`)

New field on `Config`:

```python
auto_delete_per_sender: dict[str, Optional[int]] = field(default_factory=dict)
```

Keys are node IDs in `!hex` form. Values: positive int = seconds; `None` or `0` = never.

New parsing in `from_toml` — reads `data.get("auto_delete", {}).get("senders", {})` and stores each entry's `after_s`. Missing `after_s` key skips that sender.

New resolution method:

```python
def resolve_auto_delete(self, node_id: str, db_override: Optional[int]) -> Optional[int]:
    if node_id in self.auto_delete_per_sender:
        v = self.auto_delete_per_sender[node_id]
        return v if v else None      # 0 → explicit "never"
    if db_override is not None:
        return db_override if db_override else None
    return self.auto_delete_global_s  # None → no auto-delete
```

### CLI (`cli.py`)

Add `--config PATH` argument (default `~/.config/meshtad/config.toml`). Load `Config.from_toml` at startup. `db_path` override: `args.db` wins, otherwise `Config.default().db_path` (same pattern as the daemon).

At `meshcli read <id>`, replace:

```python
ad = srow["auto_delete_after_s"] if srow else None
```

with:

```python
node_id = srow["node_id"] if srow else ""
ad = cfg.resolve_auto_delete(node_id, srow["auto_delete_after_s"] if srow else None)
```

### TUI (`tui/app.py`, `tui/screens/inbox.py`)

`MeshtuiApp.__init__` gains `cfg_path: Optional[Path] = None`. It loads `cfg = Config.from_toml(cfg_path or DEFAULT_CONFIG_PATH)` and passes `cfg` to `InboxScreen`.

`action_mark_read` in `InboxScreen`:
1. Gets the selected message row (already available from the poll loop).
2. Fetches the sender row via `DbClient`.
3. Calls `cfg.resolve_auto_delete(node_id, db_override)`.
4. Passes the result to `mark_read(msg_id, auto_delete_after_s=ad)`.

### Timer start

Auto-delete timer starts at **read time** (when `mark_read` is called), not at arrival. Messages never read stay until manually deleted.

## Precedence chain

```
TOML [auto_delete.senders."!id"] after_s
    ↓ (not present)
DB senders.auto_delete_after_s
    ↓ (NULL)
Config auto_delete_global_s
    ↓ (None)
No auto-delete
```

`0` at any level in the chain means "never auto-delete for this sender" and short-circuits to `None`.

## Tests

In `tests/test_config_file.py`:

1. `[auto_delete.senders."!aabb"]` with `after_s = 3600` parses into `auto_delete_per_sender`.
2. `resolve_auto_delete` — TOML entry wins over a DB override.
3. `resolve_auto_delete` — falls back to DB override when sender absent from TOML.
4. `resolve_auto_delete` — falls back to `auto_delete_global_s` when neither TOML nor DB set.
5. `resolve_auto_delete` — `after_s = 0` in TOML returns `None` (explicit never).

In `tests/test_auto_delete_policy.py`:

6. `mark_read` with a resolved TTL sets `auto_delete_at` on the message row.
7. Global default applies end-to-end: daemon `_sched_tick` marks the message DELETED after TTL fires (using `MockRadio` fixture).

## Out of scope

- Auto-delete on outbound messages (separate feature).
- Arrival-time stamping (timer starts at read, not arrival).
- Config.toml sync-to-DB (config.toml is authoritative; DB column is a fallback only).
