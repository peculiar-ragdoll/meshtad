# meshtad + meshcli

Generic Meshtastic store-and-forward daemon with thin SQLite clients.

## Architecture

```
┌─────────┐  ┌─────────┐  ┌─────────┐
│ meshcli │  │ TUI     │  │ Web UI  │
│ (CLI)   │  │ (future)│  │ (future)│
└────┬────┘  └────┬────┘  └────┬────┘
     │            │            │
     └────────────┼────────────┘
                  ▼
         ┌─────────────────┐
         │   SQLite (WAL)  │   ← shared interface, no sockets
         │  ~/.local/...   │
         └────────┬────────┘
                  ▼
         ┌─────────────────┐
         │   meshtad       │   RX thread  (pubsub)
         │   daemon        │   TX thread  (drain outbox)
         │                 │   Scheduler  (timeouts, cleanup)
         └────────┬────────┘
                  ▼
         ┌─────────────────┐
         │ SerialInterface │   (auto-detect or --port)
         │  (Meshtastic)   │
         └─────────────────┘
```

## Message states

**Inbound:**  UNSEEN → SEEN → DELETED
**Outbound:** QUEUED → SENT → ACKED | FAILED → DELETED

## Install

```bash
cd /workspace/meshtad
pip install -e .
```

## Run the daemon

```bash
meshtad                    # auto-detect serial port
meshtad --port /dev/cu.usbmodem1234
```

Or:

```bash
python -m meshtad.main
```

## meshcli commands

```bash
meshcli send homenode "hello world"        # fire-and-forget
meshcli inbox                              # list inbound
meshcli inbox --unseen-only
meshcli read 42                            # mark SEEN, print body
meshcli delete 42                          # soft delete
meshcli history --with homenode --limit 20
meshcli outbox                             # queued/SENT/FAILED
meshcli status 42                          # send status
meshcli alias '!aabbccdd' homenode         # register alias
meshcli aliases                            # list known senders
meshcli dongle-detect                      # find serial ports
meshcli dongle-eject                       # request daemon release
meshcli db-status                          # DB size + counts
meshcli vacuum                             # compact DB
```

## Config (TOML, optional)

`~/.config/meshtad/config.toml`:

```toml
[daemon]
db_path = "~/.local/share/meshtad/meshtad.db"

[retry]
max_retries = 5
initial_interval_s = 5
max_interval_s = 300
exponential_base = 2

[ack]
timeout_s = 30

[auto_delete]
global_after_s = null   # null = never

[size_warning]
enabled = true
threshold_mb = 100

[logging]
level = "INFO"
redact_bodies = true
```
