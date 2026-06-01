"""Modal screens shared by InboxScreen and ComposeScreen."""
from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Footer, Input, Static


class SetAliasModal(ModalScreen[str | None]):
    """Modal for setting a sender alias."""

    BINDINGS = [
        ("escape", "dismiss", "Cancel"),
    ]

    DEFAULT_CSS = """
        SetAliasModal {
            align: center middle;
            background: black;
        }
        #alias_modal {
            border: thick green;
            padding: 1 2;
            width: 60%;
            background: black;
            color: white;
        }
        #alias_row {
            width: 1fr;
            margin: 1 0;
        }
        #alias_label {
            width: 8;
            color: green;
        }
        #alias_input {
            color: white;
            background: #282850;
            width: 1fr;
        }
        #hint {
            color: #888;
        }
    """

    def __init__(
        self, node_id: str, existing_alias: str | None = None, **kwargs
    ) -> None:
        super().__init__(**kwargs)
        self.node_id = node_id
        self.existing_alias = existing_alias

    def compose(self) -> ComposeResult:
        hint = f" (current: {self.existing_alias})" if self.existing_alias else ""
        yield Vertical(
            Static(f"Set alias for {self.node_id}{hint}", id="title"),
            Horizontal(
                Static("Alias:", id="alias_label"),
                Input(
                    value=self.existing_alias or "",
                    placeholder="e.g. emma, dad, t-deck",
                    id="alias_input",
                ),
                id="alias_row",
            ),
            Static("[b]Enter[/] to save, [b]Esc[/] to cancel", id="hint"),
            id="alias_modal",
        )

    def on_mount(self) -> None:
        self.query_one("#alias_modal", Vertical).can_focus = False
        self.call_after_refresh(self.query_one("#alias_input", Input).focus)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        value = event.value if event.value.strip() else None
        self.dismiss(value)

    def action_dismiss(self) -> None:
        self.dismiss(None)


class ConfirmDeleteModal(ModalScreen[bool]):
    """Y/N confirmation modal."""

    BINDINGS = [
        ("y", "confirm", "Yes"),
        ("n", "dismiss", "No"),
    ]

    DEFAULT_CSS = """
        ConfirmDeleteModal {
            align: center middle;
            background: black;
        }
        #question {
            border: thick red;
            padding: 1 2;
            color: white;
            background: black;
        }
    """

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

    DEFAULT_CSS = """
        ConfirmDiscardModal {
            align: center middle;
            background: black;
        }
        #question {
            border: thick yellow;
            padding: 1 2;
            color: white;
            background: black;
        }
    """

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

    DEFAULT_CSS = """
        HelpModal {
            align: center middle;
            background: black;
        }
        #help_text {
            border: thick blue;
            padding: 1 2;
            width: 60%;
            color: white;
            background: black;
        }
    """

    def __init__(self, bindings: list[tuple[str, str]], **kwargs) -> None:
        super().__init__(**kwargs)
        self._help_bindings = bindings

    def compose(self) -> ComposeResult:
        lines = ["Key Bindings", "", ]
        for key, desc in self._help_bindings:
            lines.append(f"  {key:12}  {desc}")
        yield Static("\n".join(lines), id="help_text", markup=False)
        yield Footer()

    def action_close(self) -> None:
        self.dismiss(None)
