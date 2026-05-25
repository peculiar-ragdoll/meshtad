# meshtad / meshcli Roadmap

A generic Meshtastic store-and-forward daemon and thin CLI client.  
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

## Phase 1 — Daemon Hardening

- [ ] **SerialInterface smoke test against real hardware**
  - Verify `SerialInterface` connection on macOS with real dongle
  - Test node ID auto-detection and packet callback wiring
  - Validate `sendText` produces real `ROUTING_APP` ACKs on air
- [ ] **Config file + live reload**
  - Load `~/.config/meshtad/config.toml` (or `$MESHTAD_CONFIG`)
  - Tune retry params, auto-delete defaults, serial port path
  - Watch mtime and reload without daemon restart
- [ ] **macOS radio auto-detect**
  - Fallback beyond meshtastic's auto-detect: scan `/dev/cu.usbserial*` and `/dev/cu.usbmodem*` via `pyserial.tools.list_ports`
  - Expose as `meshcli dongle-detect` when `SerialInterface` can't guess
- [ ] **Graceful shutdown**
  - SIGTERM handling: finish in-flight TX, persist queue state, close DB cleanly
  - Currently threads are `daemon=True` and may drop in-flight messages on kill

## Phase 2 — CLI Ergonomics

- [ ] **`meshcli retry <id>`**
  - Move `FAILED` → `QUEUED`, reset `retry_count`
  - Let user manually re-drive after RF recovers
- [ ] **`meshcli reply <id>`**
  - Reply to a received message by reusing its sender ID automatically
  - No need to type alias/node id manually
- [ ] **Inbox search / filter**
  - `meshcli inbox --from <alias>`
  - `meshcli inbox --since <iso-date>`
  - `meshcli inbox --unseen-only` (exists, but make it explicit)
- [ ] **Message threading**
  - Group related messages by conversation (same sender pair, time proximity)
  - `meshcli thread <id>` shows the back-and-forth

## Phase 3 — TUI (Interactive Client)

- [ ] **textual / Textual-based TUI**
  - Split-pane inbox viewer: list top, message body bottom
  - Auto-poll SQLite every N seconds (configurable)
  - Hotkeys: `r` reply, `d` delete, `n` next, `p` prev, `q` quit
  - Think `mutt` or `aerc` but for Meshtastic DMs
  - Runs as a separate process from the daemon, reads same SQLite DB

## Phase 4 — Advanced Daemon

- [ ] **Per-sender auto-delete policy**
  - Global default + per-sender override in config.toml
  - Granularity: auto-delete after N minutes, or never, or on-read immediately
- [ ] **Message archive / export**
  - `meshcli export --since … --format json|csv`
  - Hard-deleted messages optionally archived to a second SQLite file
- [ ] **Rate limiting / airtime budget**
  - Track cumulative ToA per hour per destination
  - Hold messages when budget exhausted
  - Optional digest mode under backpressure
- [ ] **Multiple client attachments**
  - Daemon already uses SQLite-as-interface, but document the contract
  - Allow multiple `meshcli` / TUI instances reading concurrently (WAL handles this)

## Phase 5 — Packaging & Distribution

- [ ] **PyPI package**
  - `pip install meshtad` should Just Work
  - Include `meshtad` console-script entry point
- [ ] **Homebrew formula**
  - `brew install meshtad`
- [ ] **Git subtree split to standalone repo**
  - Extract `/workspace/meshtad` to its own repository when mature
  - Preserve history with `git subtree split --prefix=meshtad`

---

## Non-Goals (Explicitly Out of Scope)

- **App grammar / routing** — any structured message syntax belongs in the message body only; the daemon is transport-only
- **Channel (broadcast) storage** — DMs only. Busy mesh channels would spam the DB unnecessarily.
- **TCP socket / gRPC API** — SQLite IS the interface. No secondary IPC needed.
- **Audio / voice** — No microphone. Text only.
