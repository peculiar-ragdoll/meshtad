"""Regression tests for the daemon entry point (meshtad.main).

These guard the wiring that connects the config file to the running daemon:
the config TOML must actually be loaded, db_path must stay on the canonical
client-visible path (not the config-file sibling from_toml derives), and the
ConfigWatcher must be installed so live reload works.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

from meshtad.config import Config
import meshtad.main as main_mod


@pytest.fixture
def captured_daemon(monkeypatch):
    """Run main() but capture the Daemon instead of starting its threads."""
    captured = {}

    class FakeDaemon:
        def __init__(self, cfg):
            self.cfg = cfg
            captured["daemon"] = self

        def run(self):
            captured["ran"] = True

    monkeypatch.setattr(main_mod, "Daemon", FakeDaemon)
    return captured


def _run_main(monkeypatch, argv):
    monkeypatch.setattr(sys, "argv", ["meshtad", *argv])
    main_mod.main()


def test_config_file_is_loaded(monkeypatch, captured_daemon, tmp_path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text("[meshtad]\nack_timeout_s = 77.0\nmax_retries = 9\n")

    _run_main(monkeypatch, ["--config", str(cfg_file)])

    d = captured_daemon["daemon"]
    assert d.cfg.ack_timeout_s == 77.0
    assert d.cfg.max_retries == 9
    assert captured_daemon["ran"] is True


def test_db_path_pinned_to_canonical_default_not_config_sibling(monkeypatch, captured_daemon, tmp_path):
    """from_toml would put db next to the config file; main() must not let it."""
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text("[meshtad]\nack_timeout_s = 5.0\n")

    _run_main(monkeypatch, ["--config", str(cfg_file)])

    d = captured_daemon["daemon"]
    assert d.cfg.db_path == Config.default().db_path
    assert d.cfg.db_path != cfg_file.parent / "meshtad.db"


def test_db_flag_overrides(monkeypatch, captured_daemon, tmp_path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text("[meshtad]\n")
    db_override = tmp_path / "custom.db"

    _run_main(monkeypatch, ["--config", str(cfg_file), "--db", str(db_override)])

    assert captured_daemon["daemon"].cfg.db_path == db_override


def test_port_flag_overrides_config(monkeypatch, captured_daemon, tmp_path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text('[meshtad]\nserial_port = "/dev/from_config"\n')

    _run_main(monkeypatch, ["--config", str(cfg_file), "--port", "/dev/from_flag"])

    assert captured_daemon["daemon"].cfg.serial_port == "/dev/from_flag"


def test_config_watcher_installed_for_live_reload(monkeypatch, captured_daemon, tmp_path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text("[meshtad]\n")

    _run_main(monkeypatch, ["--config", str(cfg_file)])

    d = captured_daemon["daemon"]
    assert d._config_watcher is not None
    assert d._config_watcher.path == cfg_file


def test_missing_config_file_uses_defaults(monkeypatch, captured_daemon, tmp_path):
    missing = tmp_path / "does_not_exist.toml"

    _run_main(monkeypatch, ["--config", str(missing)])

    d = captured_daemon["daemon"]
    assert d.cfg.ack_timeout_s == 30.0  # default
    assert d.cfg.db_path == Config.default().db_path
