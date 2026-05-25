"""Meshtastic radio wrapper."""
from __future__ import annotations

import logging
from typing import Any, Callable, Optional

logger = logging.getLogger("meshtad.radio")

MESHTASTIC_AVAILABLE = False
try:
    import meshtastic.serial_interface
    import meshtastic.util
    from pubsub import pub
    MESHTASTIC_AVAILABLE = True
except Exception:
    pass


class Radio:
    def __init__(self, port: Optional[str] = None):
        self.port = port
        self.client: Any = None
        self.connected = False
        self.local_node_id: Optional[str] = None
        self._subscribed = False

    def connect(self) -> bool:
        if not MESHTASTIC_AVAILABLE:
            raise RuntimeError("meshtastic library not installed")
        try:
            kwargs = {"devPath": self.port} if self.port else {}
            self.client = meshtastic.serial_interface.SerialInterface(**kwargs)
            self.local_node_id = f"!{self.client.localNode.nodeNum:08x}"
            self.connected = True
            return True
        except Exception as exc:
            logger.warning("Radio connect failed: %s", exc)
            return False

    def disconnect(self) -> None:
        if self.client:
            try:
                self.client.close()
            except Exception:
                pass
        self.client = None
        self.connected = False

    def send_text(self, dest: str, text: str, want_ack: bool = True) -> tuple[bool, Optional[int]]:
        """Returns (success, packet_id)."""
        if not self.connected or not self.client:
            return False, None
        try:
            packet = self.client.sendText(text, destinationId=dest, wantAck=want_ack)
            return True, getattr(packet, "id", None)
        except Exception as exc:
            logger.warning("send_text to %s failed: %s", dest, exc)
            return False, None

    def subscribe(self, on_text: Callable, on_routing: Callable) -> None:
        if not MESHTASTIC_AVAILABLE or self._subscribed:
            return
        pub.subscribe(on_text, "meshtastic.receive.text")
        pub.subscribe(on_routing, "meshtastic.receive.routing")
        self._subscribed = True

    @staticmethod
    def detect_ports() -> list[str]:
        if not MESHTASTIC_AVAILABLE:
            return []
        try:
            return meshtastic.util.findPorts()
        except Exception:
            return []
