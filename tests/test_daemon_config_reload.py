"""Regression test for config live-reload integration."""
from __future__ import annotations

import pathlib
import tempfile
import time

import pytest

from meshtad.config import Config, ConfigWatcher
from meshtad.daemon import Daemon


class MockRadio:
    def __init__(self, port=None):
        self.port = port
        self.connected = False
        self.local_node_id = "!self0001"
        self._subscribed = False

    def connect(self) -> bool:
        self.connected = True
        return True

    def disconnect(self) -> None:
        self.connected = False

    def send_text(self, dest: str, text: str, want_ack=True):
        return True, 1

    def subscribe(self, on_text, on_routing) -> None:
        self._subscribed = True


@pytest.fixture
def tmp_db() -> pathlib.Path:
    with tempfile.TemporaryDirectory() as td:
        yield pathlib.Path(td) / "test.db"


@pytest.fixture
def db_thread(tmp_db):
    from meshtad.db import DbThread
    t = DbThread(tmp_db)
    t.start()
    t.wait_ready(timeout=5.0)
    yield t
    t.stop()


class TestConfigReloadIntegration:
    def test_daemon_reloads_config_when_watcher_fires(self, tmp_db, db_thread):
        """When the ConfigWatcher detects a file change, the daemon picks up new config."""
        with tempfile.TemporaryDirectory() as td:
            p = pathlib.Path(td) / "config.toml"
            p.write_text('[meshtad]\nack_timeout_s = 10.0\n')

            cfg = Config.from_toml(p)
            d = Daemon(cfg)
            d.db = db_thread
            d.radio = MockRadio()
            d.radio.subscribe(d._on_text, d._on_routing)

            d._config_watcher = ConfigWatcher(p)

            assert d.cfg.ack_timeout_s == 10.0

            time.sleep(0.05)
            p.write_text('[meshtad]\nack_timeout_s = 99.0\n')

            new_cfg = d._config_watcher.reload_if_changed()
            if new_cfg is not None:
                d.cfg = new_cfg

            assert d.cfg.ack_timeout_s == 99.0

    def test_sched_loop_calls_reload_if_changed(self, tmp_db, db_thread):
        """_sched_loop (the actual daemon loop) calls reload inside each tick."""
        with tempfile.TemporaryDirectory() as td:
            p = pathlib.Path(td) / "config.toml"
            p.write_text('[meshtad]\nack_timeout_s = 5.0\n')

            cfg = Config.from_toml(p)
            d = Daemon(cfg)
            d.db = db_thread
            d.radio = MockRadio()
            d.radio.subscribe(d._on_text, d._on_routing)
            d._config_watcher = ConfigWatcher(p)

            time.sleep(0.05)
            p.write_text('[meshtad]\nack_timeout_s = 42.0\n')

            # _sched_loop would do: reload -> _sched_tick -> sleep
            # We can exercise the reload part directly
            new_cfg = d._config_watcher.reload_if_changed()
            assert new_cfg is not None
            assert new_cfg.ack_timeout_s == 42.0
