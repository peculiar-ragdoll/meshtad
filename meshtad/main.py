"""Entry point for meshtad daemon."""
from __future__ import annotations

import argparse
import pathlib

from meshtad.config import Config, ConfigWatcher, DEFAULT_CONFIG_PATH
from meshtad.daemon import Daemon


def main():
    parser = argparse.ArgumentParser(description="meshtad — Meshtastic store-and-forward daemon")
    parser.add_argument(
        "--config",
        type=pathlib.Path,
        default=DEFAULT_CONFIG_PATH,
        help="Path to config TOML (default: ~/.config/meshtad/config.toml; missing file = defaults)",
    )
    parser.add_argument(
        "--db",
        type=pathlib.Path,
        default=None,
        help="Path to meshtad.db (default: ~/.local/share/meshtad/meshtad.db)",
    )
    parser.add_argument("--port", default=None, help="Serial device path (auto-detect if omitted)")
    args = parser.parse_args()

    cfg_path = args.config.expanduser()
    watcher = ConfigWatcher(cfg_path)
    cfg = watcher.current

    # from_toml derives db_path from the config file's directory, but meshcli and the
    # TUI read Config.default().db_path — the daemon must use that same canonical path
    # or the clients would read an empty database. CLI --db wins; otherwise pin the
    # default rather than the config-file sibling.
    cfg.db_path = (args.db or Config.default().db_path).expanduser()
    if args.port is not None:
        cfg.serial_port = args.port

    daemon = Daemon(cfg)
    daemon._config_watcher = watcher
    daemon.run()


if __name__ == "__main__":
    main()
