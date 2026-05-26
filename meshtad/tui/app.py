"""Textual TUI app for meshtad."""
from __future__ import annotations

import pathlib

from textual.app import App
from textual.screen import Screen

from meshtad.tui.screens import InboxScreen


class MeshtuiApp(App):
    """Main Textual application."""

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("ctrl+c", "quit", "Quit"),
    ]

    def __init__(self, db_path: pathlib.Path | None = None) -> None:
        self.db_path = db_path or pathlib.Path.home() / ".local" / "share" / "meshtad" / "meshtad.db"
        super().__init__()

    def get_default_screen(self) -> Screen:
        return InboxScreen(db_path=self.db_path)

    def action_quit(self) -> None:
        self.exit()


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="meshtui — TUI for meshtad")
    parser.add_argument("--db", type=pathlib.Path, default=None, help="Path to meshtad.db")
    args = parser.parse_args()
    app = MeshtuiApp(db_path=args.db)
    app.run()


if __name__ == "__main__":
    main()
