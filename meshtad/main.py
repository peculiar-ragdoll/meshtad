"""Entry point for meshtad daemon."""
from __future__ import annotations

import argparse
import pathlib

from meshtad.config import Config
from meshtad.daemon import Daemon


def main():
    parser = argparse.ArgumentParser(description="meshtad — Meshtastic store-and-forward daemon")
    parser.add_argument("--db", type=pathlib.Path, default=Config.default().db_path, help="Path to meshtad.db")
    parser.add_argument("--port", default=None, help="Serial device path (auto-detect if omitted)")
    args = parser.parse_args()

    cfg = Config(db_path=args.db.expanduser(), serial_port=args.port)
    Daemon(cfg).run()


if __name__ == "__main__":
    main()
