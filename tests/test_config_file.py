"""RED-phase tests for config file loading and live reload.

These tests exercise Config.from_toml() and a ConfigWatcher that
monitors mtime changes.  They will fail until the implementation
lands (Phase 1).
"""
from __future__ import annotations

import pathlib
import tempfile
import time

import pytest

from meshtad.config import Config, ConfigWatcher


class TestFromToml:
    def test_reads_serial_port_and_log_level(self):
        """Config.from_toml() overrides serial_port and log_level from TOML."""
        with tempfile.TemporaryDirectory() as td:
            p = pathlib.Path(td) / "config.toml"
            p.write_text(
                """
[meshtad]
serial_port = "/dev/cu.usbserial-ABCD"
log_level = "DEBUG"
max_retries = 10
"""
            )
            cfg = Config.from_toml(p)
            assert cfg.serial_port == "/dev/cu.usbserial-ABCD"
            assert cfg.log_level == "DEBUG"
            assert cfg.max_retries == 10

    def test_missing_file_falls_back_to_defaults(self):
        """Non-existent TOML path yields default Config values."""
        cfg = Config.from_toml(pathlib.Path("/nonexistent/meshtad.toml"))
        assert cfg.serial_port is None
        assert cfg.log_level == "INFO"
        assert cfg.max_retries == 5

    def test_partial_overrides_preserve_defaults(self):
        """Only keys present in TOML are overridden; the rest keep defaults."""
        with tempfile.TemporaryDirectory() as td:
            p = pathlib.Path(td) / "config.toml"
            p.write_text('[meshtad]\nretry_initial_s = 1.0\n')
            cfg = Config.from_toml(p)
            assert cfg.retry_initial_s == 1.0
            assert cfg.retry_max_s == 300.0   # default
            assert cfg.ack_timeout_s == 30.0  # default

    def test_auto_delete_global_from_toml(self):
        """[auto_delete] global_s is parsed into auto_delete_global_s."""
        with tempfile.TemporaryDirectory() as td:
            p = pathlib.Path(td) / "config.toml"
            p.write_text("""
[auto_delete]
global_s = 3600
""")
            cfg = Config.from_toml(p)
            assert cfg.auto_delete_global_s == 3600

    def test_db_path_derived_from_toml_location(self):
        """When path ends in .toml, db_path becomes sibling file meshtad.db."""
        with tempfile.TemporaryDirectory() as td:
            p = pathlib.Path(td) / "config.toml"
            p.write_text("[meshtad]\n")
            cfg = Config.from_toml(p)
            assert cfg.db_path == p.parent / "meshtad.db"


class TestConfigWatcher:
    def test_reload_detects_mtime_change(self):
        """ConfigWatcher.reload_if_changed() returns new Config when file is touched."""
        with tempfile.TemporaryDirectory() as td:
            p = pathlib.Path(td) / "config.toml"
            p.write_text('[meshtad]\nlog_level = "INFO"\n')
            watcher = ConfigWatcher(p)
            cfg1 = watcher.current
            assert cfg1.log_level == "INFO"

            time.sleep(0.05)
            p.write_text('[meshtad]\nlog_level = "DEBUG"\n')
            cfg2 = watcher.reload_if_changed()
            assert cfg2 is not None
            assert cfg2.log_level == "DEBUG"

    def test_reload_no_change_returns_none(self):
        """When mtime has not moved, reload_if_changed() returns None."""
        with tempfile.TemporaryDirectory() as td:
            p = pathlib.Path(td) / "config.toml"
            p.write_text('[meshtad]\nlog_level = "INFO"\n')
            watcher = ConfigWatcher(p)
            assert watcher.reload_if_changed() is None

    def test_reload_missing_file_returns_none(self):
        """If the watched file disappears, reload_if_changed() returns None safely."""
        p = pathlib.Path("/nonexistent/meshtad.toml")
        watcher = ConfigWatcher(p)
        assert watcher.reload_if_changed() is None


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
