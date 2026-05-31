"""meshtad daemon: RX, TX, and scheduler threads."""
from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timezone

from meshtad.config import Config
from meshtad.db import DbThread, _iso_now

logger = logging.getLogger("meshtad.daemon")


class Daemon:
    def __init__(self, config: Config):
        self.cfg = config
        self.db = DbThread(config.db_path)
        from meshtad.radio import Radio
        self.radio = Radio(port=config.serial_port)
        self._shutdown = threading.Event()
        self._drain_lock = threading.RLock()
        self._inflight: dict[int, dict] = {}  # packet_id -> {msg_id, sent_at, sent_at_mono}
        self._deferred_acks: dict[int, str] = {}  # packet_id -> error_reason (sync-race buffer)
        self._inflight_lock = threading.Lock()
        self._deferred_lock = threading.Lock()
        self._rx_thread: threading.Thread | None = None
        self._tx_thread: threading.Thread | None = None
        self._sched_thread: threading.Thread | None = None
        self._config_watcher = None  # set by the entry point to enable live config reload

    def run(self) -> None:
        logging.basicConfig(
            level=getattr(logging, self.cfg.log_level.upper(), logging.INFO),
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )
        self.db.start()
        if not self.db.wait_ready(timeout=10.0):
            raise RuntimeError("DB thread did not become ready")
        logger.info("meshtad starting; db=%s", self.cfg.db_path)

        # Subscribe pubsub callbacks once (global)
        self.radio.subscribe(self._on_text, self._on_routing)

        if not self.radio.connect():
            logger.error("Initial radio connect failed; will retry in background")
        else:
            logger.info("Radio connected; port=%s local_id=%s",
                        self.radio.port or "(auto)", self.radio.local_node_id)

        self._rx_thread = threading.Thread(target=self._rx_loop, name="rx", daemon=True)
        self._tx_thread = threading.Thread(target=self._tx_loop, name="tx", daemon=True)
        self._sched_thread = threading.Thread(target=self._sched_loop, name="sched", daemon=True)
        self._rx_thread.start()
        self._tx_thread.start()
        self._sched_thread.start()
        logger.info("Daemon ready — RX/TX/scheduler threads running")

        try:
            while not self._shutdown.is_set():
                time.sleep(1.0)
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()

    def stop(self) -> None:
        logger.info("Shutting down...")
        self._shutdown.set()
        # Wait for any in-flight TX drain to finish before closing DB
        with self._drain_lock:
            pass
        self.radio.disconnect()
        self.db.stop()

    # ---- RX ----

    def _on_text(self, packet, interface) -> None:
        """Pubsub callback for inbound text messages."""
        try:
            decoded = packet.get("decoded", {})
            text = decoded.get("text", "")
            if not text:
                return
            from_id = packet.get("fromId", "")
            packet_id = packet.get("id")
            to_id = packet.get("toId", "")
            # Only DMs (not broadcast)
            if to_id in ("^all", "ffffffff", "4294967295", "!ffffffff"):
                return
            sender_id = self._ensure_sender_db(from_id)
            self.db.execute(
                "INSERT INTO messages (direction, peer_id, body, state, meshtastic_packet_id) VALUES (?,?,?,'UNSEEN',?)",
                ("in", sender_id, text, packet_id),
            )
            body_preview = text[:20] + "..." if len(text) > 20 else text
            if self.cfg.redact_bodies:
                body_preview = f"<{len(text)} chars redacted>"
            logger.info("RX from %s: %s", from_id, body_preview)
        except Exception:
            logger.exception("RX handler error")

    def _on_routing(self, packet, interface) -> None:
        """Pubsub callback for routing (ACK/NAK) packets."""
        try:
            decoded = packet.get("decoded", {})
            routing = decoded.get("routing", {})
            if not routing:
                return
            # requestId is inside the decoded protobuf dict, not at the packet root
            request_id = decoded.get("requestId")
            if request_id is None:
                return
            error_reason = routing.get("errorReason", "NONE")
            self._handle_ack_nak(request_id, error_reason)
        except Exception:
            logger.exception("Routing handler error")

    def _rx_loop(self) -> None:
        """Watches connection and reconnects if needed."""
        while not self._shutdown.is_set():
            if self.radio.connected:
                time.sleep(2.0)
                continue
            if self.radio.connect():
                logger.info("Radio connected; local node %s", self.radio.local_node_id)
            else:
                time.sleep(5.0)

    def _ensure_sender_db(self, node_id: str) -> int:
        rows = self.db.execute("SELECT id FROM senders WHERE node_id=?", (node_id,))
        if rows:
            return int(rows[0][0])
        return int(self.db.execute("INSERT INTO senders (node_id) VALUES (?)", (node_id,)))

    # ---- TX (drain once, exposed for tests) ----

    def _tx_loop(self) -> None:
        while not self._shutdown.is_set():
            if not self.radio.connected:
                time.sleep(2.0)
                continue
            self._tx_drain_once()
            time.sleep(0.5)

    def _tx_drain_once(self) -> None:
        """Single outbox drain pass.  Invoked by _tx_loop and directly in tests."""
        with self._drain_lock:
            now = _iso_now()
            rows = self.db.execute(
                """SELECT m.id, m.body, m.retry_count, s.node_id
                   FROM messages m JOIN senders s ON s.id = m.peer_id
                   WHERE m.direction='out' AND m.state='QUEUED'
                     AND (m.next_attempt_at IS NULL OR m.next_attempt_at <= ?)
                   ORDER BY m.queued_at""",
                (now,),
            )
            if not rows:
                return

            for msg_id, body, retry_count, dest in rows:
                ok, packet_id = self.radio.send_text(dest, body)
                if ok:
                    self.db.execute(
                        "UPDATE messages SET state='SENT', sent_at=?, meshtastic_packet_id=? WHERE id=?",
                        (_iso_now(), packet_id, msg_id),
                    )
                    deferred_reason = None
                    if packet_id is not None:
                        with self._inflight_lock:
                            self._inflight[packet_id] = {
                                "msg_id": msg_id,
                                "sent_at": time.time(),
                                "sent_at_mono": time.monotonic(),
                            }
                            deferred_reason = self._deferred_acks.pop(packet_id, None)
                        logger.info("TX -> %s msg_id=%s packet_id=%s", dest, msg_id, packet_id)
                        if deferred_reason is not None:
                            self._handle_ack_nak(packet_id, deferred_reason)
                else:
                    self._handle_send_failure(msg_id, retry_count, "send_failed")

    def _handle_send_failure(self, msg_id: int, retry_count: int, error: str) -> None:
        if retry_count >= self.cfg.max_retries:
            self.db.execute(
                "UPDATE messages SET state='FAILED', error=? WHERE id=?",
                (error, msg_id),
            )
            logger.warning("Message %s FAILED after %s retries (%s)", msg_id, retry_count, error)
        else:
            delay = min(
                self.cfg.retry_initial_s * (self.cfg.retry_base ** retry_count),
                self.cfg.retry_max_s,
            )
            next_at = _iso_now(offset_s=delay)
            # Reset to QUEUED so the TX drain actually retransmits after next_attempt_at.
            # This path is reached both from a radio-handoff failure (already QUEUED) and
            # from an ACK timeout / NAK (state was SENT) — in the latter case, without
            # flipping back to QUEUED the message would never be resent and would just
            # tick retry_count to FAILED. Clearing sent_at + meshtastic_packet_id removes
            # it from the scheduler's SENT-timeout scan and stops a late ACK for the old
            # packet id from matching the resend.
            self.db.execute(
                "UPDATE messages SET state='QUEUED', retry_count=retry_count+1, "
                "next_attempt_at=?, sent_at=NULL, meshtastic_packet_id=NULL, error=? WHERE id=?",
                (next_at, error, msg_id),
            )
            logger.info("Message %s retry %s scheduled at %s (+%.0fs)", msg_id, retry_count + 1, next_at, delay)

    def _handle_ack_nak(self, request_id: int, error_reason: str) -> None:
        with self._inflight_lock:
            info = self._inflight.pop(request_id, None)
            if not info:
                # Synchronous ACK race: callback fired before _inflight was populated.
                # Stash for replay once the drain finishes recording the packet.
                self._deferred_acks[request_id] = error_reason
                return
            msg_id = info["msg_id"]

        # Verify still SENT before acting (race with scheduler)
        rows = self.db.execute("SELECT state, retry_count FROM messages WHERE id=?", (msg_id,))
        if not rows or rows[0][0] != "SENT":
            return

        if error_reason == "NONE":
            self.db.execute(
                "UPDATE messages SET state='ACKED', acked_at=? WHERE id=?",
                (_iso_now(), msg_id),
            )
            logger.info("ACK msg_id=%s", msg_id)
        else:
            retry_count = rows[0][1]
            self._handle_send_failure(msg_id, retry_count, f"NAK:{error_reason}")
            logger.warning("NAK msg_id=%s reason=%s", msg_id, error_reason)

    # ---- Scheduler (tick exposed for tests) ----

    def _sched_loop(self) -> None:
        while not self._shutdown.is_set():
            # Live config reload
            if self._config_watcher:
                new_cfg = self._config_watcher.reload_if_changed()
                if new_cfg is not None:
                    # db_path and serial_port are startup-bound: DbThread and Radio are
                    # already constructed from the original values, so a live reload must
                    # not repoint them. Carry the runtime values onto the new config.
                    new_cfg.db_path = self.cfg.db_path
                    new_cfg.serial_port = self.cfg.serial_port
                    self.cfg = new_cfg
                    logger.info("Config reloaded from %s", self._config_watcher.path)
            self._sched_tick()
            time.sleep(5.0)

    def _sched_tick(self) -> None:
        """Single scheduler pass. Invoked by _sched_loop and directly in tests."""
        now = _iso_now()

        # 1. ACK timeouts (monotonic clock)
        all_sent = self.db.execute(
            "SELECT id, retry_count, sent_at, meshtastic_packet_id FROM messages "
            "WHERE direction='out' AND state='SENT' AND acked_at IS NULL"
        )
        cutoff_mono = time.monotonic() - self.cfg.ack_timeout_s
        for msg_id, retry_count, sent_at_str, packet_id in all_sent:
            with self._inflight_lock:
                info = self._inflight.get(packet_id)
            if info:
                if info.get("sent_at_mono", 0) < cutoff_mono:
                    with self._inflight_lock:
                        self._inflight.pop(packet_id, None)
                    self._handle_send_failure(msg_id, retry_count, "ack_timeout")
            else:
                # Fallback to wall-clock when no in-memory tracking (e.g. restart)
                try:
                    t = datetime.fromisoformat(sent_at_str.replace("Z", "+00:00")).timestamp()
                    if t < time.time() - self.cfg.ack_timeout_s:
                        self._handle_send_failure(msg_id, retry_count, "ack_timeout")
                except Exception:
                    continue

        # 2. Auto-delete
        self.db.execute(
            "UPDATE messages SET state='DELETED', deleted_at=? "
            "WHERE auto_delete_at IS NOT NULL AND auto_delete_at <= ?",
            (now, now),
        )

        # 3. Control queue
        ctrl = self.db.execute("SELECT id, action, params FROM control_queue ORDER BY id LIMIT 1")
        if ctrl:
            ctrl_id, action, params = ctrl[0]
            if action == "eject":
                logger.info("Control: eject requested")
                self.radio.disconnect()
            elif action == "reconnect":
                logger.info("Control: reconnect requested")
                self.radio.disconnect()
            self.db.execute("DELETE FROM control_queue WHERE id=?", (ctrl_id,))

        # 4. Size warning
        if self.cfg.size_warning_enabled:
            try:
                size_mb = self.cfg.db_path.stat().st_size / (1024 * 1024)
                if size_mb > self.cfg.size_warning_mb:
                    logger.warning("DB size %.1f MB exceeds threshold %s MB", size_mb, self.cfg.size_warning_mb)
            except Exception:
                pass

        # 5. Heartbeat for TUI liveness
        self.db.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES ('daemon_pid', ?)",
            (str(os.getpid()),),
        )
        self.db.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES ('daemon_heartbeat', ?)",
            (_iso_now(),),
        )
