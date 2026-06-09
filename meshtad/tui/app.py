"""Textual TUI app for meshtad."""
from __future__ import annotations

import pathlib

from textual.app import App
from textual.screen import Screen

from meshtad.config import Config
from meshtad.tui.screens import InboxScreen

_DEFAULT_CONFIG_PATH = pathlib.Path("~/.config/meshtad/config.toml").expanduser()


class MeshtuiApp(App):
    """Main Textual application."""

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("ctrl+c", "quit", "Quit"),
    ]

    def __init__(
        self,
        db_path: pathlib.Path | None = None,
        cfg_path: pathlib.Path | None = None,
    ) -> None:
        self.db_path = db_path or pathlib.Path.home() / ".local" / "share" / "meshtad" / "meshtad.db"
        self.cfg = Config.from_toml((cfg_path or _DEFAULT_CONFIG_PATH).expanduser())
        super().__init__()

    def get_default_screen(self) -> Screen:
        return InboxScreen(db_path=self.db_path, cfg=self.cfg)

    def action_quit(self) -> None:
        self.exit()


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="meshtui — TUI for meshtad")
    parser.add_argument("--db", type=pathlib.Path, default=None, help="Path to meshtad.db")
    parser.add_argument(
        "--config",
        type=pathlib.Path,
        default=None,
        help="Path to config TOML (default: ~/.config/meshtad/config.toml)",
    )
    args = parser.parse_args()
    app = MeshtuiApp(db_path=args.db, cfg_path=args.config)
    app.run()


if __name__ == "__main__":
    main()
