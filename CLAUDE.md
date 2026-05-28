# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`meshtad` is a **generic** Meshtastic store-and-forward daemon with two thin clients (`meshcli` CLI, `meshtui` Textual TUI). It is its own git repo, nested under `polly_foundation/polly-deck/` but unrelated to the PollyDeck `home_node.py` app-router that lives one directory up. PollyDeck routes `#app` commands and bridges Signal/LLMs; **meshtad has none of that** — it is a plain DM inbox/outbox with reliable delivery. Don't import PollyDeck concepts (apps, digest mode, rate limiter, signal bridge) here.

Same regional/RF assumptions as PollyDeck apply (EU868 LongFast, airtime is precious) but there is no airtime governor in this codebase — the daemon sends whenever the outbox has a QUEUED row.

## Commands

```bash
make setup       # create .venv + pip install -e ".[dev]"
make test        # pytest tests/ -v   (reinstalls deps every run — see below)
make test-cov    # + coverage term-missing
make run         # start the daemon (python -m meshtad)
make tuitest     # seed /tmp/meshtad_tuitest.db via scripts/mockdata.py, launch TUI against it
make dbreset     # delete the mock TUI db
make lint        # ruff check meshtad/ tests/
make typecheck   # mypy meshtad/
```

Run a single test with the venv python directly (don't activate; keep calls stateless):

```bash
.venv/bin/python -m pytest tests/test_daemon.py::TestTxThread::test_drains_queued_to_sent -v
.venv/bin/python -m pytest tests/test_daemon.py -k ack -v
```

Note: every Make `test`/`run`/`lint` target depends on `install`, which runs `pip install -e ".[dev]"` **every invocation**. For a tight test loop, call `.venv/bin/python -m pytest …` directly to skip the reinstall.

## Architecture — the SQLite DB *is* the IPC

There are no sockets, no HTTP, no message bus. The daemon and every client coordinate **only** through a shared WAL-mode SQLite file (default `~/.local/share/meshtad/meshtad.db`). This is the single most important fact about the codebase:

- **`DbThread`** (`db.py`) is the daemon's **single writer thread**. All daemon DB access funnels through `DbThread.execute(sql, params)`, which posts to an internal queue and blocks for the reply. Never open a second writer connection from daemon code.
- **`DbClient`** (`db.py`) is what `meshcli` and the TUI use: opens a short-lived connection per call, read-only except for outbox inserts (`enqueue_outbound`), alias edits, and control-queue writes. Clients and daemon are separate processes sharing the file via WAL.
- **Client → daemon commands** go through the `control_queue` table (e.g. `dongle-eject` inserts an `eject` row; the scheduler drains it). There is no other client→daemon channel.
- **Daemon → TUI liveness**: the scheduler writes `daemon_pid` and `daemon_heartbeat` into the `meta` table each tick (5 s). `tui/heartbeat.is_daemon_online()` reads `daemon_heartbeat` and treats it as online if within 30 s.

### Daemon threads (`daemon.py`)

`Daemon.run()` starts the DbThread, subscribes pubsub callbacks, then launches three daemon threads:

| Thread | Loop fn | Tick fn (called directly in tests) | Job |
|---|---|---|---|
| `rx` | `_rx_loop` | (pubsub `_on_text` / `_on_routing`) | watch/reconnect radio; inbound DMs land via pubsub, not the loop |
| `tx` | `_tx_loop` (0.5 s) | `_tx_drain_once()` | drain QUEUED outbox rows → `radio.send_text`, mark SENT, register in-flight |
| `sched` | `_sched_loop` (5 s) | `_sched_tick()` | ACK timeouts, auto-delete, control queue, size warning, heartbeat, config reload |

Inbound text arrives on the **pubsub thread** via `_on_text` (broadcasts to `^all`/`ffffffff` are dropped — DMs only). Routing ACK/NAK packets arrive via `_on_routing`.

### Delivery state machine

```
Inbound:  UNSEEN → SEEN → DELETED
Outbound: QUEUED → SENT → ACKED | FAILED → DELETED
```

A SENT message becomes ACKED only when a `ROUTING_APP` packet with matching `requestId` and `errorReason == "NONE"` arrives. NAK or timeout → `_handle_send_failure`, which either reschedules with exponential backoff (`next_attempt_at`) or moves to FAILED after `max_retries`. `meshcli retry <id>` resets a FAILED row to QUEUED.

### Concurrency details worth knowing before you touch ACK/TX code

- **Synchronous-ACK race**: `radio.send_text` can return *before* `_inflight` is populated, yet the routing callback may already have fired. `_handle_ack_nak` stashes the verdict in `_deferred_acks[packet_id]` when no in-flight entry exists; `_tx_drain_once` replays it immediately after recording the packet. Don't "simplify" this away.
- **ACK timeouts use the monotonic clock** (`sent_at_mono` in `_inflight`), with a wall-clock fallback (`sent_at` column) for entries lost across a restart. Use `time.monotonic()` for any new timeout logic, not wall time.
- `_drain_lock` is an `RLock`; `stop()` acquires it to let an in-flight drain finish before closing the DB.

### Radio wrapper (`radio.py`)

`Radio` wraps `meshtastic.serial_interface.SerialInterface`. The `meshtastic`/`pubsub` import is **guarded** (`MESHTASTIC_AVAILABLE`) so the package imports and tests run without the library or hardware present. Tests inject a `MockRadio` (see `tests/test_daemon.py`) that implements `connect/disconnect/send_text/subscribe` plus `inject_text`/`inject_routing` to simulate the mesh — `_daemon_with_mock()` is the standard fixture for daemon tests. Tests drive behaviour by calling `_tx_drain_once()` / `_sched_tick()` directly rather than running the loops.

### TUI (`tui/`)

Textual app. `app.MeshtuiApp` → `screens.InboxScreen` (tabbed Inbox/Outbox/History, polls the DB every 2 s), `screens.ComposeScreen` (new/reply), `screens.modals` (confirm-delete, confirm-discard, help). The TUI is a pure `DbClient` consumer — it never talks to the radio.

## Config gotcha — README is stale

`config.py` reads exactly three TOML tables: **`[meshtad]`**, **`[auto_delete]`**, **`[tui]`**. The retry/ack/logging keys (`max_retries`, `ack_timeout_s`, `retry_initial_s`, `log_level`, `redact_bodies`, `serial_port`, …) live **flat under `[meshtad]`**, not under the `[retry]`/`[ack]`/`[logging]`/`[size_warning]` tables the README shows. The README config block is aspirational and does **not** match the loader. When documenting or editing config, trust `config.py` (and `tests/test_config_file.py`) over the README. `db_path` is derived from the config file's parent directory; `auto_delete.global_s` and `tui.poll_interval_s`/`tui.theme` are the only keys outside `[meshtad]`.

`ConfigWatcher` reloads on mtime change; the scheduler loop swaps `self.cfg` live, so config edits take effect without a restart.

## Conventions

- Entry points (`pyproject.toml` `[project.scripts]`): `meshtad` → daemon, `meshcli` → CLI, `meshtui` → TUI. `python -m meshtad` also runs the daemon.
- `MAX_PAYLOAD_BYTES = 228` in `config.py` caps outbound body length (conservative vs Meshtastic's 233-byte `DATA_PAYLOAD_LEN`); `meshcli send` rejects longer bodies.
- Node IDs are the `!`-prefixed 8-char lowercase hex form (e.g. `!aabbccdd`). Aliases resolve case-insensitively against `node_id` or `alias`.
- `redact_bodies` defaults to `true` — RX/TX log lines show `<N chars redacted>`, never message contents. Keep it that way; only disable for a debug session.
- All timestamps are stored as ISO-8601 UTC `…Z` strings (`_iso_now`).
- `meshtad.service` is a user-level systemd unit template for running the daemon under Linux.
