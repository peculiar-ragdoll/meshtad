"""Modal screens shared by InboxScreen and ComposeScreen."""
from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Footer, Static


class ConfirmDeleteModal(ModalScreen[bool]):
    """Y/N confirmation modal."""

    BINDINGS = [
        ("y", "confirm", "Yes"),
        ("n", "dismiss", "No"),
    ]

    def compose(self) -> ComposeResult:
        yield Static("Delete this message? [y/n]", id="question")

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_dismiss(self) -> None:
        self.dismiss(False)


class ConfirmDiscardModal(ModalScreen[bool]):
    """Y/N confirmation for discarding unsent draft."""

    BINDINGS = [
        ("y", "confirm", "Yes"),
        ("n", "dismiss", "No"),
    ]

    def compose(self) -> ComposeResult:
        yield Static("Discard unsent message? [y/n]", id="question")

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_dismiss(self) -> None:
        self.dismiss(False)


class HelpModal(ModalScreen[None]):
    """Modal showing all available key bindings for the current screen."""

    BINDINGS = [
        ("q", "close", "Close"),
        ("escape", "close", "Close"),
    ]

    def __init__(self, bindings: list[tuple[str, str]], **kwargs) -> None:
        super().__init__(**kwargs)
        self._help_bindings = bindings

    def compose(self) -> ComposeResult:
        lines = ["Key Bindings", "",]
        for key, desc in self._help_bindings:
            lines.append(f"  {key:12}  {desc}")
        yield Static("\n".join(lines), id="help_text", markup=False)
        yield Footer()

    def action_close(self) -> None:
        self.dismiss(None)
