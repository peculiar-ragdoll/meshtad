# meshtad / meshcli Roadmap

A generic Meshtastic store-and-forward daemon and thin SQLite client.  
Just DMs, SQLite, and a CLI.

## Phase 0 — TVP (Thinnest Viable Product) — DONE

- [x] SQLite schema with WAL (`journal_mode=WAL`)
- [x] Single DB writer thread (`DbThread`) + reader (`DbClient`)
- [x] Inbound DMs → `UNSEEN` → `SEEN` → `DELETED` lifecycle
- [x] Outbound DMs → `QUEUED` → `SENT` → `ACKED` | `FAILED`
- [x] Exponential backoff retry on NAK / send-failure
- [x] Auto-delete after read (configurable per-sender)
- [x] `meshcli` subcommands: `send`, `inbox`, `read`, `delete`, `history`, `outbox`, `status`, `alias`, `aliases`, `db-status`, `vacuum`, `dongle-detect`, `dongle-eject`
- [x] `meshtad` daemon: RX, TX drain, scheduler threads
- [x] MockRadio for integration tests (69 tests passing)
- [x] Makefile with auto-venv, auto-detect broken symlinks on macOS
- [x] systemd user unit template (`meshtad.service`)

## Phase 1 — Daemon Hardening — DONE

- [x] **Config file + live reload**
  - Load `~/.config/meshtad/config.toml` (or `$MESHTAD_CONFIG`)
  - Tune retry params, auto-delete defaults, serial port path
  - Watch mtime and reload without daemon restart
- [x] **macOS radio auto-detect**
  - Fallback beyond meshtastic's auto-detect: scan `/dev/cu.usbserial*` and `/dev/cu.usbmodem*` via glob
  - Expose as `meshcli dongle-detect` when `SerialInterface` can't guess
- [x] **Graceful shutdown**
  - SIGTERM handling: finish in-flight TX, persist queue state, close DB cleanly
- [x] **Remediation review** — 10 issues fixed, 20 regression tests added (112 tests passing)

## Phase 2 — CLI Ergonomics — DONE (partial)

- [x] **`meshcli retry <id>`**
  - Move `FAILED` → `QUEUED`, reset `retry_count`
- [ ] **`meshcli reply <id>`**
  - Reply to a received message by reusing its sender ID automatically
- [ ] **Inbox search / filter**
  - `meshcli inbox --from <alias>`
  - `meshcli inbox --since <iso-date>`
- [ ] **Message threading**
  - Group related messages by conversation (same sender pair, time proximity)

## Phase 3 — TUI (Interactive Client) — DONE

### 3a — Heartbeat + Skeleton
DONE
- [x] **Daemon heartbeat in SQLite**
  - Daemon writes `daemon_pid` + `daemon_heartbeat` (ISO timestamp) to `meta` table every `_sched_tick`
  - TUI checks heartbeat freshness; stale = "daemon offline"
  - Threshold: 30 s default, configurable
- [x] **`meshtad.tui` package skeleton**
  - `meshtad/tui/` sub-package
  - Textual App subclass, empty InboxScreen
  - Entry point: `meshcli tui` or standalone `meshtui`

### 3b — Inbox Screen
DONE
- [x] **Split-pane layout**
  - Top: `DataTable` of messages (id, flag, alias, body preview, state, time)
  - Bottom: `Static` panel showing full body + metadata of selected row
  - Footer: `Footer` widget with key bindings
- [x] **ASCII state indicators**
  - `[Q]` QUEUED, `[S]` SENT, `[A]` ACKED, `[F]` FAILED
  - `*` UNSEEN, ` ` SEEN
- [x] **Hotkeys**
  - `↑/↓` or `k/j` navigate
  - `Enter` open full read view
  - `m` mark read/unread
  - `d` soft-delete (Y/N confirm)
  - `q` quit

### 3c — Compose + Reply
DONE
- [x] **ComposeScreen modal**
  - `n` new message — empty To field, type alias then Tab to resolve
  - `r` reply — pre-fill To from selected message sender
  - Body: `TextArea` multi-line
  - `Ctrl+S` send (writes `QUEUED` outbound to DB)
  - `Ctrl+C` cancel
  - `q` with unsent text → confirmation prompt; `Ctrl+C` hard quit

### 3d — Tabs + Polling
DONE
- [x] **Three tabs: Inbox / Outbox / History**
  - `1`/`2`/`3` switch
  - Each tab is a separate SQL query against the same DB
- [x] **Background DB poller**
  - `asyncio` task polls inbox/outbox every `poll_interval_s` (default 2)
  - Tracks `MAX(id)` watermark; only fetches newer rows after initial load
  - On new UNSEEN: status bar flashes unread count
- [x] **Daemon status in footer**
  - Green dot + "online" when heartbeat < 30s
  - Red dot + "offline" when heartbeat stale

### 3e — Polish
DONE
- [x] **Config integration**
  - `[tui]` section in `config.toml`: `poll_interval_s`, `theme`
- [x] **Help overlay**
  - `?` key shows all bindings
- [x] **Themes**
  - Dark default; respect `$NO_COLOR`

## Phase 4 — Advanced Daemon

- [ ] **Per-sender auto-delete policy**
  - Global default + per-sender override in config.toml
- [ ] **Message archive / export**
  - `meshcli export --since … --format json|csv`
- [ ] **Rate limiting / airtime budget**
  - Track cumulative ToA per hour per destination
- [ ] **Multiple client attachments**
  - Document the SQLite contract for third-party clients

## Phase 5 — Packaging & Distribution

- [ ] **PyPI package**
  - `pip install meshtad` should Just Work
- [ ] **Homebrew formula**
  - `brew install meshtad`
- [ ] **Git subtree split to standalone repo**
  - Extract `/workspace/meshtad` to its own repository when mature

---

## Non-Goals (Explicitly Out of Scope)

- **App grammar / routing** — any structured message syntax belongs in the message body only; the daemon is transport-only
- **Channel (broadcast) storage** — DMs only. Busy mesh channels would spam the DB unnecessarily.
- **TCP socket / gRPC API** — SQLite IS the interface. No secondary IPC needed.
- **Audio / voice** — No microphone. Text only.
